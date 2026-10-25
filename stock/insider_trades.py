"""高管及关联人增减持：谁在买、谁在卖，净增持还是净减持。

公告只说明"发生了什么"，增减持明细回答"内部人最近站在哪一边"。
这里取东方财富高管持股变动明细，记录变动人、与高管的关系、职务、变动
股数、成交均价、金额、变动后持股与变动原因；再按年度汇总净增减持，按
人汇总净买入/净卖出。持股计划和其他组织单列，避免把计划买入当成某个
高管的个人判断。

* ``ensure_insider_trades(code)``    按需取数，本地缓存一天
* ``build_insider_trades(code)``     生成分析用摘要
* ``insider_trades_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_insider_trades(...)``     渲染成可读文本

口径：变动股数为正表示增持、为负表示减持；金额单位元，负数为卖出金额；
比例是占总股本百分比；``years`` 按自然年回看，默认三年。缺失值保留为
None，不填 0。``build_insider_trades`` 会访问网络，界面里请放到后台线程
调用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_EXECUTIVE_HOLD_DETAILS"
PAGE_URL = "https://data.eastmoney.com/executive/{code}.html"
SOURCE_LABEL = "东方财富 高管增减持"

TABLE = "company_insider_trades"

# 增减持披露不频繁，缓存一天足够
DEFAULT_CACHE_HOURS = 24
# 默认回看三年，最多十年
DEFAULT_YEARS = 3
MAX_YEARS = 10
# 明细最多返回多少条，年度和个人汇总仍使用全部记录
MAX_EVENTS = 60
# 文本里年度和个人最多列几项
SUMMARY_YEARS = 5
SUMMARY_PEOPLE = 3

PLAN_KEYWORDS = ("持股计划", "员工持股", "激励计划", "持股集合")


class InsiderTradeError(Exception):
    """高管增减持数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def insider_trades_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """把接口返回整理成增减持摘要（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise InsiderTradeError(f"{code}：高管增减持接口返回格式异常")

    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = _normalize_row(row)
        if event is not None:
            events.append(event)
    return insider_trades_from_events(events, code, years=years, today=today)


def insider_trades_from_events(
    events: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """由已规范化的变动记录生成年度与个人汇总（纯函数）。"""
    if not isinstance(events, (list, tuple)):
        raise InsiderTradeError(f"{code}：高管增减持记录格式异常")

    years = _window(years)
    history = _dedupe([
        event for event in events
        if isinstance(event, dict) and event.get("change_date")
    ])
    end = _date(today) or datetime.now().strftime("%Y-%m-%d")
    start_year = int(end[:4]) - years + 1
    start = f"{start_year}-01-01"
    window = [
        event for event in history if start <= event["change_date"] <= end
    ]

    annual = _annual_rows(window)
    people = _people_rows(window)
    counts = _counts(window, people)
    truncated = max(0, len(window) - MAX_EVENTS)
    data = {
        "available": True,
        "code": code,
        "as_of": history[0]["change_date"] if history else None,
        "window_start": start,
        "window_end": end,
        "years": years,
        "has_trades": bool(window),
        "latest": history[0] if history else None,
        "latest_window": window[0] if window else None,
        "annual": annual,
        "people": people,
        "events": window[:MAX_EVENTS],
        "events_truncated": truncated,
        "counts": counts,
        "notes": [],
    }
    data["notes"] = _notes(history, window, counts, years, truncated)
    data["summary"] = format_insider_trades(data, code=code)
    return data


def fetch_insider_trades(code: str) -> dict:
    """抓取高管增减持明细；只缓存规范化后的记录。"""
    code = normalize_stock_code(code)
    if not code:
        raise InsiderTradeError("请输入股票代码")

    try:
        rows = _request(code)
    except InsiderTradeError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise InsiderTradeError(f"获取高管增减持数据失败：{error}") from error

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


def ensure_insider_trades(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取增减持数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise InsiderTradeError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_insider_trades(code)
    except InsiderTradeError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_insider_trades(
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise InsiderTradeError("请输入股票代码")

    result = ensure_insider_trades(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    events = result["data"].get("events") or []
    data = insider_trades_from_events(events, code, years=years, today=today)

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name and events:
        name = events[0].get("security_name")
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL.format(code=code)
    data["summary"] = format_insider_trades(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_insider_trades(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：净额 + 年度节奏 + 主要变动人。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    years = data.get("years") or DEFAULT_YEARS
    counts = data.get("counts") or {}
    annual = data.get("annual") or []
    people = data.get("people") or []
    title = f"{name}({code})" if name else (code or "高管增减持")

    parts = [title, f"近{years}年"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if counts.get("events"):
        sides = []
        if counts.get("buy_events"):
            sides.append(
                f"增持 {counts['buy_events']} 笔 "
                f"{_shares(counts.get('buy_shares'))} / "
                f"{_amount(counts.get('buy_amount'))}"
            )
        if counts.get("sell_events"):
            sides.append(
                f"减持 {counts['sell_events']} 笔 "
                f"{_shares(counts.get('sell_shares'))} / "
                f"{_amount(counts.get('sell_amount'))}"
            )
        if sides:
            lines.append("；".join(sides))
        net_shares = counts.get("net_shares") or 0
        direction = "增持" if net_shares >= 0 else "减持"
        text = (
            f"净{direction} {_shares(abs(net_shares))}，"
            f"金额 {_amount(abs(counts.get('net_amount') or 0))}"
        )
        if counts.get("missing_amount_events"):
            text += "（部分记录缺金额）"
        lines.append(text)

        year_rows = [row for row in annual if row.get("events")][-SUMMARY_YEARS:]
        if year_rows:
            lines.append(
                "年度净增减："
                + " / ".join(
                    f"{row['year']} {_signed_shares(row.get('net_shares'))}"
                    for row in year_rows
                )
            )

        top = people[:SUMMARY_PEOPLE]
        if top:
            lines.append("主要变动：" + "；".join(_person_text(row) for row in top))
    elif data.get("latest"):
        latest = data["latest"]
        lines.append(
            f"近{years}年没有披露的增减持记录；最近一次 "
            f"{latest.get('change_date')} {latest.get('person')}"
            f"{latest.get('direction')} {_shares(abs(latest.get('shares') or 0))}"
        )
    else:
        lines.append("暂无高管增减持记录")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    change_date = _date(row.get("CHANGE_DATE"))
    if not change_date:
        return None
    shares = _int(row.get("CHANGE_SHARES"))
    if shares is None or shares == 0:
        return None

    person = _text(row.get("PERSON_NAME")) or _text(row.get("DSE_PERSON_NAME"))
    if not person:
        return None
    relation = _text(row.get("PERSON_DSE_RELATION")) or "未披露"
    executive = _text(row.get("DSE_PERSON_NAME"))
    is_plan = relation == "其他组织" or any(
        word in person for word in PLAN_KEYWORDS
    )

    price = _number(row.get("AVERAGE_PRICE"))
    amount = _number(row.get("CHANGE_AMOUNT"))
    estimated = False
    if amount is None and price is not None:
        # 接口偶尔只给均价，用均价 × 股数补一个可用的估算值
        amount = round(price * shares, 2)
        estimated = True

    return {
        "change_date": change_date,
        "year": int(change_date[:4]),
        "person": person,
        "executive": None if is_plan else executive,
        "position": None if is_plan else _text(row.get("POSITION_NAME")),
        "relation": relation,
        "kind": "plan" if is_plan else "individual",
        "direction": "增持" if shares > 0 else "减持",
        "shares": shares,
        "average_price": price,
        "amount": amount,
        "amount_estimated": estimated,
        "reason": _text(row.get("CHANGE_REASON")),
        "change_ratio_pct": _number(row.get("CHANGE_RATIO")),
        "hold_after": _int(row.get("CHANGE_AFTER_HOLDNUM")),
        "begin_hold": _int(row.get("BEGIN_HOLD_NUM")),
        "end_hold": _int(row.get("END_HOLD_NUM")),
        "hold_type": _text(row.get("HOLD_TYPE")),
        "security_name": _text(row.get("SECURITY_NAME")),
        "key": _text(row.get("GGEID")),
    }


def _annual_rows(events: list[dict]) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for event in events:
        by_year.setdefault(event["year"], []).append(event)

    rows = []
    for year in sorted(by_year):
        group = by_year[year]
        metrics = _metrics(group)
        rows.append({
            "year": year,
            "events": len(group),
            "buy_events": metrics["buy_events"],
            "sell_events": metrics["sell_events"],
            "buy_shares": metrics["buy_shares"],
            "sell_shares": metrics["sell_shares"],
            "net_shares": metrics["net_shares"],
            "buy_amount": metrics["buy_amount"],
            "sell_amount": metrics["sell_amount"],
            "net_amount": metrics["net_amount"],
            "missing_amount_events": metrics["missing_amount_events"],
            "amount_complete": metrics["missing_amount_events"] == 0,
        })
    return rows


def _people_rows(events: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for event in events:
        if event["kind"] == "plan":
            key = ("plan", event["person"], None)
        else:
            key = (
                "individual",
                event["person"],
                event.get("executive"),
            )
        groups.setdefault(key, []).append(event)

    rows = []
    for key, group in groups.items():
        ordered = sorted(
            group,
            key=lambda item: (item["change_date"], item["person"]),
            reverse=True,
        )
        latest = ordered[0]
        metrics = _metrics(group)
        rows.append({
            "person": latest["person"],
            "executive": latest.get("executive"),
            "position": latest.get("position"),
            "relation": latest.get("relation"),
            "kind": latest["kind"],
            "events": len(group),
            "buy_shares": metrics["buy_shares"],
            "sell_shares": metrics["sell_shares"],
            "net_shares": metrics["net_shares"],
            "buy_amount": metrics["buy_amount"],
            "sell_amount": metrics["sell_amount"],
            "net_amount": metrics["net_amount"],
            "missing_amount_events": metrics["missing_amount_events"],
            "amount_complete": metrics["missing_amount_events"] == 0,
            "first_date": min(event["change_date"] for event in group),
            "latest_date": latest["change_date"],
            "latest_hold": latest.get("hold_after"),
            "latest_price": latest.get("average_price"),
        })

    rows.sort(
        key=lambda row: (
            abs(row.get("net_amount") or 0),
            abs(row.get("net_shares") or 0),
            row.get("latest_date") or "",
        ),
        reverse=True,
    )
    return rows


def _counts(events: list[dict], people: list[dict]) -> dict:
    metrics = _metrics(events)
    return {
        "events": len(events),
        "buy_events": metrics["buy_events"],
        "sell_events": metrics["sell_events"],
        "people": len(people),
        "insiders": sum(1 for row in people if row["kind"] == "individual"),
        "plans": sum(1 for row in people if row["kind"] == "plan"),
        "plan_events": sum(1 for event in events if event["kind"] == "plan"),
        "buy_shares": metrics["buy_shares"],
        "sell_shares": metrics["sell_shares"],
        "net_shares": metrics["net_shares"],
        "buy_amount": metrics["buy_amount"],
        "sell_amount": metrics["sell_amount"],
        "net_amount": metrics["net_amount"],
        "missing_amount_events": metrics["missing_amount_events"],
        "estimated_amount_events": sum(
            1 for event in events if event.get("amount_estimated")
        ),
        "latest_date": events[0]["change_date"] if events else None,
    }


def _metrics(events: list[dict]) -> dict:
    buy = [event for event in events if event["shares"] > 0]
    sell = [event for event in events if event["shares"] < 0]
    buy_shares = sum(event["shares"] for event in buy)
    sell_shares = abs(sum(event["shares"] for event in sell))
    buy_amount = _sum_amount(buy)
    sell_amount = abs(_sum_amount(sell) or 0.0)
    return {
        "buy_events": len(buy),
        "sell_events": len(sell),
        "buy_shares": buy_shares,
        "sell_shares": sell_shares,
        "net_shares": buy_shares - sell_shares,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "net_amount": round(buy_amount - sell_amount, 2),
        "missing_amount_events": sum(
            1 for event in events if event.get("amount") is None
        ),
    }


def _sum_amount(events: list[dict]) -> float:
    values = [
        event["amount"] for event in events if event.get("amount") is not None
    ]
    return round(sum(values), 2) if values else 0.0


def _notes(
    history: list[dict],
    events: list[dict],
    counts: dict,
    years: int,
    truncated: int,
) -> list[str]:
    notes: list[str] = []
    if not history:
        notes.append("没有取到高管增减持记录，可能数据源未收录")
        return notes

    if not events:
        latest = history[0]
        notes.append(
            f"近{years}年没有披露的增减持记录；最近一次为 "
            f"{latest['change_date']} {latest['person']}"
            f"{latest['direction']} {_shares(abs(latest['shares']))}"
        )
        notes.append(
            "只统计已披露的高管及其关联人变动，未覆盖未披露或未达披露标准的交易"
        )
        return notes

    notes.append(
        "只统计已披露的高管及其关联人变动，未覆盖未披露或未达披露标准的交易"
    )
    notes.append(
        "变动股数为正表示增持、为负表示减持；金额为披露金额，"
        "缺失时按成交均价×股数估算"
    )
    if any(
        event["kind"] == "individual"
        and event.get("relation") not in (None, "本人", "未披露")
        for event in events
    ):
        notes.append(
            "关联人（配偶、子女、父母、兄弟姐妹等）交易已计入，"
            "但不等于高管本人判断"
        )
    if counts.get("plan_events"):
        notes.append("持股计划/其他组织单列，金额不代表个人增持")
    if counts.get("missing_amount_events"):
        notes.append(
            f"{counts['missing_amount_events']} 笔缺少成交金额，金额合计不完整"
        )
    if counts.get("estimated_amount_events"):
        notes.append(
            f"{counts['estimated_amount_events']} 笔成交金额按均价估算"
        )
    if truncated:
        notes.append(f"明细只列最近 {MAX_EVENTS} 笔，年度与个人汇总使用全部记录")
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
            raise InsiderTradeError(
                f"{code}：获取高管增减持数据失败（{message}）"
            )
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise InsiderTradeError(f"{code}：高管增减持接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise InsiderTradeError(f"{code}：高管增减持接口数据格式异常")
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
            "columns": (
                "SECURITY_CODE,SECURITY_NAME,CHANGE_DATE,PERSON_NAME,"
                "PERSON_DSE_RELATION,DSE_PERSON_NAME,POSITION_NAME,"
                "CHANGE_SHARES,AVERAGE_PRICE,CHANGE_AMOUNT,CHANGE_REASON,"
                "CHANGE_RATIO,CHANGE_AFTER_HOLDNUM,BEGIN_HOLD_NUM,"
                "END_HOLD_NUM,HOLD_TYPE,GGEID"
            ),
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": str(page),
            "pageSize": "100",
            "sortColumns": "CHANGE_DATE,SECURITY_CODE,PERSON_NAME",
            "sortTypes": "-1,1,1",
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
        raise InsiderTradeError(f"{code}：高管增减持接口返回格式异常")
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
    """同一披露编号只保留一条；持股计划按日期、股数、金额合并。"""
    by_key: dict[tuple, dict] = {}
    for event in sorted(
        (item for item in events if isinstance(item, dict) and item.get("change_date")),
        key=lambda item: item["change_date"],
        reverse=True,
    ):
        key = _event_key(event)
        current = by_key.get(key)
        if current is None or _completeness(event) > _completeness(current):
            by_key[key] = event
    return sorted(
        by_key.values(),
        key=lambda item: (item["change_date"], item["person"]),
        reverse=True,
    )


def _event_key(event: dict) -> tuple:
    if event.get("kind") == "plan":
        # 同一笔持股计划会按归属高管重复出现，GGEID 不同但交易相同
        return (
            "plan",
            event.get("change_date"),
            event.get("person"),
            event.get("shares"),
            event.get("amount"),
            event.get("reason"),
        )
    key = event.get("key")
    if key:
        return ("id", key)
    return (
        "row",
        event.get("change_date"),
        event.get("person"),
        event.get("executive"),
        event.get("shares"),
        event.get("amount"),
        event.get("reason"),
        event.get("relation"),
    )


def _completeness(event: dict) -> int:
    return sum(
        1 for key in ("amount", "average_price", "hold_after", "begin_hold",
                      "end_hold")
        if event.get(key) is not None
    )


def _person_text(row: dict) -> str:
    label = row.get("person") or ""
    if row.get("kind") == "plan":
        label += "（持股计划）"
    elif row.get("executive") and row.get("executive") != row.get("person"):
        label += f"（{row.get('executive')}{row.get('relation') or ''}）"
    net = row.get("net_shares") or 0
    action = "净增持" if net > 0 else ("净减持" if net < 0 else "净变动")
    text = f"{label} {action} {_shares(abs(net))}"
    if row.get("net_amount") is not None:
        text += f" / {_amount(abs(row['net_amount']))}"
    return text


def _window(years) -> int:
    try:
        return min(max(int(years), 1), MAX_YEARS)
    except (TypeError, ValueError):
        return DEFAULT_YEARS


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


def _signed_shares(value) -> str:
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + _shares(value)


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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
