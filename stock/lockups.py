"""限售解禁：未来还有多少股份要解禁、历史解禁规模有多大。

解禁本身不是减持，但它改变了可流通筹码的供给。这里取东方财富限售
解禁批次，记录解禁日、本次解禁数量、解禁市值、占总股本比例、股东
户数、股份类型与解禁后流通股/未流通股；再区分未来批次和历史批次，
按年度和股份类型汇总。

* ``ensure_lockups(code)``    按需取数，本地缓存一天
* ``build_lockups(code)``     生成分析用摘要
* ``lockups_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_lockups(...)``     渲染成可读文本

口径：接口的解禁数量与市值单位分别是万股、万元，这里统一换算成股和
元；比例字段是小数，换算成百分数。未来解禁日期为预计日期，市值按
接口给的参考价估算，会随股价变化。``years`` 按自然年回看，默认三年；
``ahead_months`` 默认看未来十二个月，最多三十六个月。缺失值保留为
None，不填 0。``build_lockups`` 会访问网络，界面里请放到后台线程调用。
"""

from __future__ import annotations

import calendar
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_LIFT_STAGE"
PAGE_URL = "https://data.eastmoney.com/dxf/detail.html?code={code}"
SOURCE_LABEL = "东方财富 限售解禁"

TABLE = "company_lockups"

# 解禁数据更新不频繁，缓存一天足够
DEFAULT_CACHE_HOURS = 24
# 默认回看三年，最多十年
DEFAULT_YEARS = 3
MAX_YEARS = 10
# 默认看未来十二个月，最多三十六个月
DEFAULT_AHEAD_MONTHS = 12
MAX_AHEAD_MONTHS = 36
# 最多返回多少批，年度与类型汇总仍使用全部批次
MAX_EVENTS = 40


class LockupError(Exception):
    """限售解禁数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def lockups_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    ahead_months: int = DEFAULT_AHEAD_MONTHS,
    today: str | None = None,
) -> dict:
    """把接口返回整理成解禁摘要（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise LockupError(f"{code}：限售解禁接口返回格式异常")

    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = _normalize_row(row)
        if event is not None:
            events.append(event)
    return lockups_from_events(
        events, code, years=years, ahead_months=ahead_months, today=today
    )


def lockups_from_events(
    events: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    ahead_months: int = DEFAULT_AHEAD_MONTHS,
    today: str | None = None,
) -> dict:
    """由已规范化的解禁批次生成历史、未来与年度汇总（纯函数）。"""
    if not isinstance(events, (list, tuple)):
        raise LockupError(f"{code}：限售解禁记录格式异常")

    years = _window_years(years)
    ahead_months = _window_months(ahead_months)
    history = _dedupe([
        event for event in events
        if isinstance(event, dict) and event.get("free_date")
    ])
    today_date = _parse_date(today) or date.today()
    end = today_date.isoformat()
    for event in history:
        free_day = _parse_date(event["free_date"])
        event["is_upcoming"] = event["free_date"] > end
        event["days_until"] = (
            (free_day - today_date).days if free_day is not None else None
        )
    start_year = today_date.year - years + 1
    start = f"{start_year}-01-01"
    future_end = _add_months(today_date, ahead_months).isoformat()

    window = [
        event for event in history if start <= event["free_date"] <= future_end
    ]
    past = [event for event in window if event["free_date"] <= end]
    future = sorted(
        (event for event in window if event["free_date"] > end),
        key=lambda item: (item["free_date"], -item.get("shares") or 0),
    )
    past.sort(key=lambda item: (item["free_date"], item.get("shares") or 0),
              reverse=True)

    counts = _counts(window, past, future)
    annual = _annual_rows(window)
    types = _type_rows(window)
    upcoming_display = future[:MAX_EVENTS]
    remain = max(0, MAX_EVENTS - len(upcoming_display))
    history_display = past[:remain]
    truncated = len(window) - len(upcoming_display) - len(history_display)

    updates = [
        event.get("update_date") or event.get("free_date")
        for event in history
    ]
    latest = past[0] if past else (history[0] if history else None)
    data = {
        "available": True,
        "code": code,
        "as_of": max(updates) if updates else None,
        "window_start": start,
        "window_end": future_end,
        "years": years,
        "ahead_months": ahead_months,
        "has_lockups": bool(window),
        "latest": latest,
        "next": future[0] if future else None,
        "upcoming": upcoming_display,
        "history": history_display,
        "annual": annual,
        "types": types,
        "events_truncated": max(0, truncated),
        "counts": counts,
        "notes": [],
    }
    data["notes"] = _notes(history, window, future, counts, years, ahead_months)
    data["summary"] = format_lockups(data, code=code)
    return data


def fetch_lockups(code: str) -> dict:
    """抓取限售解禁批次；只缓存规范化后的记录。"""
    code = normalize_stock_code(code)
    if not code:
        raise LockupError("请输入股票代码")

    try:
        rows = _request(code)
    except LockupError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise LockupError(f"获取限售解禁数据失败：{error}") from error

    events = _dedupe([
        event
        for row in rows
        if isinstance(row, dict)
        for event in [_normalize_row(row)]
        if event is not None
    ])
    return {
        "code": code,
        "events": events,
        "count": len(events),
    }


def ensure_lockups(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取解禁数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise LockupError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_lockups(code)
    except LockupError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_lockups(
    code: str,
    years: int = DEFAULT_YEARS,
    ahead_months: int = DEFAULT_AHEAD_MONTHS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise LockupError("请输入股票代码")

    result = ensure_lockups(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    events = result["data"].get("events") or []
    data = lockups_from_events(
        events, code, years=years, ahead_months=ahead_months, today=today
    )

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name and events:
        name = events[0].get("security_name")
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL.format(code=code)
    data["summary"] = format_lockups(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_lockups(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：未来批次 + 下一批 + 历史 + 年度节奏。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    years = data.get("years") or DEFAULT_YEARS
    ahead_months = data.get("ahead_months")
    if ahead_months is None:
        ahead_months = DEFAULT_AHEAD_MONTHS
    counts = data.get("counts") or {}
    annual = data.get("annual") or []
    title = f"{name}({code})" if name else (code or "限售解禁")

    parts = [title, f"历史近{years}年", f"未来{ahead_months}个月"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if counts.get("events"):
        if counts.get("upcoming_events"):
            lines.append(
                f"未来 {ahead_months} 个月："
                f"{counts['upcoming_events']} 批 / "
                f"{_shares(counts.get('upcoming_shares'))} / "
                f"{_amount(counts.get('upcoming_market_cap'))}"
                "（市值按参考价估算）"
            )
            lines.append(_next_text(data.get("next")))
        else:
            lines.append(f"未来{ahead_months}个月：无已披露解禁")

        if counts.get("history_events"):
            latest = data.get("latest")
            text = (
                f"历史：近{years}年 {counts['history_events']} 批 / "
                f"{_shares(counts.get('history_shares'))} / "
                f"{_amount(counts.get('history_market_cap'))}"
            )
            if latest:
                text += (
                    f"；最近 {latest.get('free_date')} "
                    f"{_shares(latest.get('shares'))} / "
                    f"{_amount(latest.get('market_cap'))}"
                )
            lines.append(text)

        year_rows = [row for row in annual if row.get("events")]
        if year_rows:
            lines.append(
                "年度解禁："
                + " / ".join(
                    f"{row['year']} {_shares(row.get('shares'))}"
                    for row in year_rows
                )
            )
    elif data.get("latest"):
        latest = data["latest"]
        lines.append(
            f"近{years}年及未来{ahead_months}个月没有披露的解禁；"
            f"最近一次为 {latest.get('free_date')} "
            f"{_shares(latest.get('shares'))} / "
            f"{_amount(latest.get('market_cap'))}"
        )
    else:
        lines.append("暂无已披露的解禁记录")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    free_date = _date(row.get("FREE_DATE"))
    if not free_date:
        return None

    shares = _scaled_int(row.get("CURRENT_FREE_SHARES"), 10_000)
    if shares is None or shares <= 0:
        return None

    return {
        "free_date": free_date,
        "year": int(free_date[:4]),
        "is_upcoming": False,
        "days_until": None,
        "shares": shares,
        "market_cap": _scaled_number(row.get("LIFT_MARKET_CAP"), 10_000),
        "price": _number(row.get("NEW")),
        "ratio_total_pct": _percent(
            row.get("TOTAL_RATIO")
            if row.get("TOTAL_RATIO") is not None
            else row.get("TOTALSHARES_RATIO")
        ),
        "ratio_free_pct": _percent(row.get("FREE_RATIO")),
        "holders": _int(row.get("BATCH_HOLDER_NUM")),
        "shares_type": _text(row.get("FREE_SHARES_TYPE")),
        "market_type": _text(row.get("MARKET_TYPE")),
        "free_shares_after": _scaled_int(row.get("FREE_SHARES"), 10_000),
        "non_free_shares_after": _scaled_int(
            row.get("NON_FREE_SHARES"), 10_000
        ),
        "able_free_shares": _scaled_int(
            row.get("ABLE_FREE_SHARES"), 10_000
        ),
        "update_date": _date(row.get("EUTIME")),
        "security_name": _text(row.get("SECURITY_NAME_ABBR")),
        "key": (
            free_date,
            _text(row.get("FREE_SHARES_TYPE")),
            shares,
            _int(row.get("BATCH_HOLDER_NUM")),
        ),
    }


def _annual_rows(events: list[dict]) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for event in events:
        by_year.setdefault(event["year"], []).append(event)

    rows = []
    for year in sorted(by_year):
        group = by_year[year]
        rows.append({
            "year": year,
            "events": len(group),
            "history_events": sum(
                1 for event in group if not event.get("is_upcoming")
            ),
            "upcoming_events": sum(
                1 for event in group if event.get("is_upcoming")
            ),
            "shares": _sum_optional([
                event.get("shares") for event in group
            ]),
            "market_cap": _sum_optional([
                event.get("market_cap") for event in group
            ]),
            "missing_market_cap": sum(
                1 for event in group if event.get("market_cap") is None
            ),
        })
    return rows


def _type_rows(events: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for event in events:
        groups.setdefault(event.get("shares_type") or "未披露", []).append(event)

    rows = []
    for shares_type, group in groups.items():
        rows.append({
            "type": shares_type,
            "events": len(group),
            "upcoming": sum(
                1 for event in group if event.get("is_upcoming")
            ),
            "shares": _sum_optional([
                event.get("shares") for event in group
            ]),
            "market_cap": _sum_optional([
                event.get("market_cap") for event in group
            ]),
        })
    rows.sort(
        key=lambda row: (row.get("events") or 0, row.get("shares") or 0),
        reverse=True,
    )
    return rows


def _counts(
    events: list[dict],
    past: list[dict],
    future: list[dict],
) -> dict:
    next_event = future[0] if future else None
    latest = past[0] if past else None
    return {
        "events": len(events),
        "history_events": len(past),
        "upcoming_events": len(future),
        "next_date": next_event.get("free_date") if next_event else None,
        "next_days": next_event.get("days_until") if next_event else None,
        "next_shares": next_event.get("shares") if next_event else None,
        "next_market_cap": (
            next_event.get("market_cap") if next_event else None
        ),
        "next_ratio_pct": (
            next_event.get("ratio_total_pct") if next_event else None
        ),
        "next_holders": next_event.get("holders") if next_event else None,
        "next_type": next_event.get("shares_type") if next_event else None,
        "upcoming_shares": _sum_optional([
            event.get("shares") for event in future
        ]),
        "upcoming_market_cap": _sum_optional([
            event.get("market_cap") for event in future
        ]),
        "upcoming_ratio_pct": _sum_optional([
            event.get("ratio_total_pct") for event in future
        ]),
        "history_shares": _sum_optional([
            event.get("shares") for event in past
        ]),
        "history_market_cap": _sum_optional([
            event.get("market_cap") for event in past
        ]),
        "total_shares": _sum_optional([
            event.get("shares") for event in events
        ]),
        "total_market_cap": _sum_optional([
            event.get("market_cap") for event in events
        ]),
        "latest_date": latest.get("free_date") if latest else None,
        "latest_type": latest.get("shares_type") if latest else None,
        "missing_market_cap": sum(
            1 for event in events if event.get("market_cap") is None
        ),
    }


def _notes(
    history: list[dict],
    events: list[dict],
    future: list[dict],
    counts: dict,
    years: int,
    ahead_months: int,
) -> list[str]:
    notes: list[str] = []
    if not history:
        notes.append("没有取到限售解禁记录，可能数据源未收录")
        return notes

    if not events:
        latest = history[0]
        notes.append(
            f"近{years}年及未来{ahead_months}个月没有披露的解禁；"
            f"最近一次为{latest['free_date']}"
        )
        notes.append("只统计已披露的解禁批次，未覆盖未公告的安排")
        return notes

    notes.append("解禁不等于减持，股东是否卖出取决于意愿和减持规则")
    notes.append("解禁数量按公告，市值按披露参考价估算，会随股价变化")
    if future:
        notes.append(
            f"未来{ahead_months}个月有{len(future)}批解禁，"
            f"合计{_shares(counts.get('upcoming_shares'))}"
        )
    else:
        notes.append(f"未来{ahead_months}个月没有已披露的解禁")
    if counts.get("missing_market_cap"):
        notes.append(
            f"{counts['missing_market_cap']} 批缺少解禁市值，合计不完整"
        )
    return notes


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _request(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 20:
        payload = _get_page(code, page)
        if payload.get("success") is False:
            message = payload.get("message") or "接口返回失败"
            if payload.get("result") is None and "为空" in message:
                return rows
            raise LockupError(f"{code}：获取限售解禁数据失败（{message}）")
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise LockupError(f"{code}：限售解禁接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise LockupError(f"{code}：限售解禁接口数据格式异常")
        rows.extend(item for item in data if isinstance(item, dict))
        try:
            pages = int(result.get("pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages:
            break
        page += 1
    return rows


def _get_page(code: str, page: int) -> dict:
    response = requests.get(
        ENDPOINT,
        params={
            "reportName": REPORT_NAME,
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": str(page),
            "pageSize": "100",
            "sortColumns": "FREE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        },
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise LockupError(f"{code}：限售解禁接口返回格式异常")
    return payload


# ---------------------------------------------------------------------------
# 本地缓存
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        create table if not exists {TABLE} (
            code text primary key,
            data text,
            fetched_at text
        )
        """
    )


def _cached_row(code: str) -> dict | None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
        row = conn.execute(
            f"select * from {TABLE} where code = ?",
            (code,),
        ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data"] or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    data.setdefault("code", code)
    data["fetched_at"] = row["fetched_at"]
    return data


def _save_row(data: dict) -> None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(
                f"""
                insert or replace into {TABLE} (code, data, fetched_at)
                values (?, ?, ?)
                """,
                (
                    data.get("code"),
                    json.dumps(
                        {key: value for key, value in data.items()
                         if key != "fetched_at"},
                        ensure_ascii=False,
                    ),
                    _now(),
                ),
            )


def _age_hours(fetched_at: str | None) -> float:
    if not fetched_at:
        return float("inf")
    try:
        fetched = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return float("inf")
    return (datetime.now() - fetched).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _dedupe(events: list[dict]) -> list[dict]:
    """同一批解禁只保留一条，优先保留更新更晚、字段更完整的一条。"""
    by_key: dict[tuple, dict] = {}
    for event in events:
        if not isinstance(event, dict) or not event.get("free_date"):
            continue
        raw_key = event.get("key")
        if isinstance(raw_key, (list, tuple)):
            key = tuple(raw_key)
        else:
            key = (
                event.get("free_date"),
                event.get("shares_type"),
                event.get("shares"),
                event.get("holders"),
            )
        current = by_key.get(key)
        if current is None or _event_stamp(event) > _event_stamp(current):
            by_key[key] = event
    return sorted(
        by_key.values(),
        key=lambda item: (item["free_date"], item.get("shares") or 0),
        reverse=True,
    )


def _event_stamp(event: dict) -> tuple:
    return (
        event.get("update_date") or "",
        _completeness(event),
    )


def _completeness(event: dict) -> int:
    return sum(
        1 for key in (
            "market_cap", "price", "ratio_total_pct", "ratio_free_pct",
            "holders", "shares_type", "free_shares_after",
            "non_free_shares_after", "able_free_shares",
        )
        if event.get(key) is not None
    )


def _next_text(event: dict | None) -> str:
    if not event:
        return "下一批：无"
    text = (
        f"下一批：{event.get('free_date')} "
        f"{event.get('shares_type') or '类型未披露'} "
        f"{_shares(event.get('shares'))} / "
        f"{_amount(event.get('market_cap'))}"
    )
    if event.get("ratio_total_pct") is not None:
        text += f" / 占总股本 {_pct(event['ratio_total_pct'])}"
    if event.get("holders") is not None:
        text += f"（{event['holders']} 名股东）"
    return text


def _window_years(years) -> int:
    try:
        return min(max(int(years), 1), MAX_YEARS)
    except (TypeError, ValueError):
        return DEFAULT_YEARS


def _window_months(months) -> int:
    try:
        return min(max(int(months), 0), MAX_AHEAD_MONTHS)
    except (TypeError, ValueError):
        return DEFAULT_AHEAD_MONTHS


def _parse_date(value) -> date | None:
    text = _date(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _sum_optional(values: list) -> float | int | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    total = sum(known)
    if all(isinstance(value, int) for value in known):
        return int(total)
    return round(float(total), 2)


def _date(value) -> str | None:
    text = _text(value)
    return text[:10] if text else None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "-" else text


def _number(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _scaled_number(value, scale: int) -> float | None:
    number = _number(value)
    return round(number * scale, 2) if number is not None else None


def _scaled_int(value, scale: int) -> int | None:
    number = _number(value)
    return int(round(number * scale)) if number is not None else None


def _percent(value) -> float | None:
    """接口比例是小数，转成百分数。"""
    number = _number(value)
    return number * 100 if number is not None else None


def _pct(value) -> str:
    if value is None:
        return "—"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _amount(value) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:.2f}亿元"
    if value >= 1e4:
        return f"{sign}{value / 1e4:.2f}万元"
    return f"{sign}{value:.0f}元"


def _shares(value) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:.2f}亿股"
    if value >= 1e4:
        return f"{sign}{value / 1e4:.2f}万股"
    return f"{sign}{int(value)}股"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
