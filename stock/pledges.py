"""股权质押：最新质押比例、近一年变化与高峰。

股权质押的数据来自东方财富按周披露的质押统计：质押股份、质押市值、
质押笔数、无限售/有限售质押，以及质押股份占总股本比例。这里按公司
取最近几年的周度记录，给出最新一期、一年前对比、区间峰值和月/年度
节奏。

* ``ensure_pledges(code)``    按需取数，本地缓存一天
* ``build_pledges(code)``     生成分析用摘要
* ``pledges_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_pledges(...)``     渲染成可读文本

口径：``REPURCHASE_BALANCE``、``REPURCHASE_UNLIMITED_BALANCE``、
``REPURCHASE_LIMITED_BALANCE`` 单位是万股；``PLEDGE_MARKET_CAP`` 单位
是万元；``PLEDGE_RATIO`` 已经是百分数。比例高只说明股东质押融资的
规模，不等于违约或爆仓。``build_pledges`` 会访问网络，界面里请放到
后台线程调用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_CSDC_LIST"
PAGE_URL = "https://data.eastmoney.com/gpzy/detail/{code}.html"
SOURCE_LABEL = "东方财富 股权质押"

TABLE = "company_pledges"

# 质押统计按周更新，缓存一天足够
DEFAULT_CACHE_HOURS = 24
DEFAULT_YEARS = 3
MAX_YEARS = 10
# 风险提示阈值（百分比）
HIGH_PLEDGE_PCT = 50.0
WATCH_PLEDGE_PCT = 30.0
# 一年变化超过这个百分点才算方向
DIRECTION_THRESHOLD_PP = 1.0
# 最多返回多少个月的序列
MAX_SERIES_MONTHS = 60


class PledgeError(Exception):
    """股权质押数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def pledges_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """把接口返回整理成质押摘要（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise PledgeError(f"{code}：股权质押接口返回格式异常")

    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = _normalize_row(row)
        if event is not None:
            events.append(event)
    return pledges_from_events(events, code, years=years, today=today)


def pledges_from_events(
    events: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """由已规范化的周度记录生成摘要（纯函数）。"""
    if not isinstance(events, (list, tuple)):
        raise PledgeError(f"{code}：股权质押记录格式异常")

    years = _window_years(years)
    today_date = _parse_date(today) or date.today()
    end = today_date.isoformat()
    start = f"{today_date.year - years + 1}-01-01"
    all_events = _dedupe(events)
    window = [
        event for event in all_events if start <= event["trade_date"] <= end
    ]

    latest = all_events[0] if all_events else None
    series = _monthly(window)
    annual = _annual(window)
    counts = _counts(window, latest, today_date)
    notes = _notes(all_events, window, latest, counts, years)
    data = {
        "available": True,
        "code": code,
        "as_of": latest["trade_date"] if latest else None,
        "window_start": start,
        "window_end": end,
        "years": years,
        "has_pledges": bool(all_events),
        "latest": latest,
        "series": series,
        "annual": annual,
        "counts": counts,
        "notes": notes,
    }
    data["summary"] = format_pledges(data, code=code)
    return data


def fetch_pledges(code: str) -> dict:
    """抓取周度质押记录；只缓存规范化后的记录。"""
    code = normalize_stock_code(code)
    if not code:
        raise PledgeError("请输入股票代码")

    try:
        rows = _request(code)
    except PledgeError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise PledgeError(f"获取股权质押数据失败：{error}") from error

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


def ensure_pledges(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取质押数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise PledgeError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_pledges(code)
    except PledgeError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_pledges(
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise PledgeError("请输入股票代码")

    result = ensure_pledges(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    events = result["data"].get("events") or []
    data = pledges_from_events(events, code, years=years, today=today)

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name and data.get("latest"):
        name = data["latest"].get("security_name")
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL.format(code=code)
    data["summary"] = format_pledges(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_pledges(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：最新一期 + 一年变化 + 区间 + 年度。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    years = data.get("years") or DEFAULT_YEARS
    counts = data.get("counts") or {}
    latest = data.get("latest")
    title = f"{name}({code})" if name else (code or "股权质押")

    parts = [title, f"历史近{years}年"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if latest and counts.get("points"):
        lines.append(_latest_text(latest))
        if counts.get("year_ago_date"):
            change = counts.get("change_pp")
            lines.append(
                f"一年前：{counts['year_ago_date']} "
                f"{_pct(counts.get('year_ago_ratio_pct'))}，变化 "
                f"{_signed(change)} 个百分点（{counts.get('direction')}）"
            )
        if counts.get("peak_ratio_pct") is not None:
            lines.append(
                f"区间：峰值 {_pct(counts['peak_ratio_pct'])} "
                f"({counts.get('peak_date')}) / "
                f"均值 {_pct2(counts.get('avg_ratio_pct'))}"
            )
        annual = [row for row in data.get("annual") or [] if row.get("year")]
        if annual:
            lines.append(
                "年度："
                + " / ".join(
                    f"{row['year']} {_pct(row.get('latest_ratio_pct'))}"
                    for row in annual
                )
            )
    elif latest:
        lines.append(
            f"近{years}年没有披露的质押记录；最近一次为 "
            f"{latest.get('trade_date')} "
            f"{_pct(latest.get('ratio_pct'))} / "
            f"{_shares(latest.get('balance_shares'))}"
        )
    else:
        lines.append("暂无已披露的股权质押记录")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    trade_date = _date(row.get("TRADE_DATE"))
    if not trade_date:
        return None
    ratio = _number(row.get("PLEDGE_RATIO"))
    balance = _scaled_int(row.get("REPURCHASE_BALANCE"), 10_000)
    if ratio is None and balance is None:
        return None

    return {
        "trade_date": trade_date,
        "year": int(trade_date[:4]),
        "ratio_pct": ratio,
        "balance_shares": balance,
        "market_cap": _scaled_number(row.get("PLEDGE_MARKET_CAP"), 10_000),
        "deal_num": _int(row.get("PLEDGE_DEAL_NUM")),
        "unlimited_shares": _scaled_int(
            row.get("REPURCHASE_UNLIMITED_BALANCE"), 10_000
        ),
        "limited_shares": _scaled_int(
            row.get("REPURCHASE_LIMITED_BALANCE"), 10_000
        ),
        "industry": _text(row.get("INDUSTRY")),
        "y1_change_pct": _number(row.get("Y1_CLOSE_ADJCHRATE")),
        "security_name": _text(row.get("SECURITY_NAME_ABBR")),
        "key": trade_date,
    }


def _monthly(events: list[dict]) -> list[dict]:
    """每个自然月保留最后一条周度记录。"""
    by_month: dict[str, dict] = {}
    for event in events:
        month = event["trade_date"][:7]
        if month not in by_month:
            by_month[month] = event
    rows = []
    for month in sorted(by_month)[-MAX_SERIES_MONTHS:]:
        event = by_month[month]
        rows.append({
            "month": month,
            "trade_date": event["trade_date"],
            "ratio_pct": event.get("ratio_pct"),
            "balance_shares": event.get("balance_shares"),
            "market_cap": event.get("market_cap"),
            "deal_num": event.get("deal_num"),
        })
    return rows


def _annual(events: list[dict]) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for event in events:
        year = event.get("year") or int(event["trade_date"][:4])
        by_year.setdefault(year, []).append(event)

    rows = []
    for year in sorted(by_year):
        group = sorted(by_year[year], key=lambda item: item["trade_date"])
        ratios = [
            item["ratio_pct"] for item in group
            if item.get("ratio_pct") is not None
        ]
        rows.append({
            "year": year,
            "points": len(group),
            "latest_date": group[-1]["trade_date"],
            "latest_ratio_pct": group[-1].get("ratio_pct"),
            "balance_shares": group[-1].get("balance_shares"),
            "market_cap": group[-1].get("market_cap"),
            "peak_ratio_pct": max(ratios) if ratios else None,
            "low_ratio_pct": min(ratios) if ratios else None,
        })
    return rows


def _counts(
    window: list[dict],
    latest: dict | None,
    today_date: date,
) -> dict:
    if latest is None:
        return _empty_counts()

    ratios = [
        event["ratio_pct"] for event in window
        if event.get("ratio_pct") is not None
    ]
    peak = None
    low = None
    if window:
        peak = max(
            window,
            key=lambda item: (
                item.get("ratio_pct") if item.get("ratio_pct") is not None
                else float("-inf"),
                item["trade_date"],
            ),
        )
        low = min(
            window,
            key=lambda item: (
                item.get("ratio_pct") if item.get("ratio_pct") is not None
                else float("inf"),
                item["trade_date"],
            ),
        )

    target = _add_days(today_date, -365)
    year_ago = None
    for event in window:
        if event["trade_date"] <= target.isoformat():
            if year_ago is None or event["trade_date"] > year_ago["trade_date"]:
                year_ago = event

    latest_ratio = latest.get("ratio_pct")
    change = None
    direction = None
    if year_ago is not None and latest_ratio is not None \
            and year_ago.get("ratio_pct") is not None:
        change = round(latest_ratio - year_ago["ratio_pct"], 2)
        if change > DIRECTION_THRESHOLD_PP:
            direction = "上升"
        elif change < -DIRECTION_THRESHOLD_PP:
            direction = "下降"
        else:
            direction = "持平"

    return {
        "points": len(window),
        "latest_date": latest["trade_date"],
        "latest_ratio_pct": latest_ratio,
        "latest_balance_shares": latest.get("balance_shares"),
        "latest_market_cap": latest.get("market_cap"),
        "latest_deal_num": latest.get("deal_num"),
        "latest_unlimited_shares": latest.get("unlimited_shares"),
        "latest_limited_shares": latest.get("limited_shares"),
        "latest_industry": latest.get("industry"),
        "year_ago_date": year_ago["trade_date"] if year_ago else None,
        "year_ago_ratio_pct": (
            year_ago.get("ratio_pct") if year_ago else None
        ),
        "change_pp": change,
        "direction": direction,
        "peak_ratio_pct": peak.get("ratio_pct") if peak else None,
        "peak_date": peak["trade_date"] if peak else None,
        "low_ratio_pct": low.get("ratio_pct") if low else None,
        "low_date": low["trade_date"] if low else None,
        "avg_ratio_pct": (
            round(sum(ratios) / len(ratios), 2) if ratios else None
        ),
        "high_pledge": (
            latest_ratio is not None and latest_ratio >= HIGH_PLEDGE_PCT
        ),
        "watch_pledge": (
            latest_ratio is not None
            and WATCH_PLEDGE_PCT <= latest_ratio < HIGH_PLEDGE_PCT
        ),
    }


def _empty_counts() -> dict:
    return {
        "points": 0,
        "latest_date": None,
        "latest_ratio_pct": None,
        "latest_balance_shares": None,
        "latest_market_cap": None,
        "latest_deal_num": None,
        "latest_unlimited_shares": None,
        "latest_limited_shares": None,
        "latest_industry": None,
        "year_ago_date": None,
        "year_ago_ratio_pct": None,
        "change_pp": None,
        "direction": None,
        "peak_ratio_pct": None,
        "peak_date": None,
        "low_ratio_pct": None,
        "low_date": None,
        "avg_ratio_pct": None,
        "high_pledge": False,
        "watch_pledge": False,
    }


def _notes(
    all_events: list[dict],
    window: list[dict],
    latest: dict | None,
    counts: dict,
    years: int,
) -> list[str]:
    if not all_events or latest is None:
        return ["没有取到股权质押记录，可能公司没有质押或数据源未收录"]

    if not window:
        return [
            f"近{years}年没有披露的质押记录；最近一次为{latest['trade_date']}",
            "数据按周披露，早期记录可能已停止更新",
        ]

    notes = [
        "质押比例=质押股份÷总股本，按周披露",
        "质押比例高说明股东资金压力或控制权风险，不等于已经违约",
        "质押市值按披露参考价估算，会随股价变化",
    ]
    age = _days_since(latest["trade_date"])
    if age is not None and age > 30:
        notes.append(f"最新一期距今天{age}天，可能未更新")
    if counts.get("high_pledge"):
        notes.append("最新质押比例超过50%，需要重点看公告与股东资金安排")
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
            raise PledgeError(
                f"{code}：获取股权质押数据失败（{message}）"
            )
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise PledgeError(f"{code}：股权质押接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise PledgeError(f"{code}：股权质押接口数据格式异常")
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
            "pageSize": "500",
            "sortColumns": "TRADE_DATE",
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
        raise PledgeError(f"{code}：股权质押接口返回格式异常")
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
    """同一交易日只保留一条，优先保留字段更完整的一条。"""
    by_key: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict) or not event.get("trade_date"):
            continue
        key = event.get("key")
        if not isinstance(key, str):
            key = event["trade_date"]
        current = by_key.get(key)
        if current is None or _completeness(event) > _completeness(current):
            by_key[key] = event
    return sorted(
        by_key.values(),
        key=lambda item: item["trade_date"],
        reverse=True,
    )


def _completeness(event: dict) -> int:
    return sum(
        1 for key in (
            "ratio_pct", "balance_shares", "market_cap", "deal_num",
            "unlimited_shares", "limited_shares", "industry",
        )
        if event.get(key) is not None
    )


def _latest_text(event: dict) -> str:
    text = (
        f"最新：{event.get('trade_date')} "
        f"质押比例 {_pct(event.get('ratio_pct'))} / "
        f"质押 {_shares(event.get('balance_shares'))} / "
        f"市值 {_amount(event.get('market_cap'))}"
    )
    if event.get("deal_num") is not None:
        text += f" / {event['deal_num']} 笔"
    return text


def _window_years(years) -> int:
    try:
        return min(max(int(years), 1), MAX_YEARS)
    except (TypeError, ValueError):
        return DEFAULT_YEARS


def _parse_date(value) -> date | None:
    text = _date(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_days(day: date, days: int) -> date:
    return date.fromordinal(day.toordinal() + days)


def _days_since(value: str | None) -> int | None:
    day = _parse_date(value)
    if day is None:
        return None
    return (date.today() - day).days


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


def _pct(value) -> str:
    if value is None:
        return "—"
    text = f"{value:.2f}"
    if text.endswith("0"):
        text = text[:-1]
    if text.endswith("."):
        text += "0"
    return f"{text}%"


def _pct2(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def _signed(value) -> str:
    if value is None:
        return "—"
    if abs(value) < 1:
        return f"{value:+.2f}"
    return f"{value:+.1f}"


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
