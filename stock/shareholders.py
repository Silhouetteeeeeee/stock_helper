"""股东结构：谁在持有、集中度如何、筹码在变集中还是分散。

十大股东回答"谁说了算"，股东户数回答"筹码在谁手里"。这里取东方
财富 F10 的股东研究数据，整理成最新十大股东、十大流通股东、实控人
与最近若干期股东户数。

* ``ensure_shareholders(code)``   按需取数，本地缓存一天
* ``build_shareholders(code)``    生成分析用摘要
* ``shareholders_from_payload``   纯函数，由接口返回生成，便于测试
* ``format_shareholders(...)``    渲染成可读文本

口径：持股数是股；比例是百分数（45.8 表示 45.8%）；股东户数是户。
缺失值保留为 None，不填 0。``build_shareholders`` 会访问网络，界面里
请放到后台线程调用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

import requests

import stock.utils as su
from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax"
PAGE_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/Index"
    "?type=web&code={code}"
)
SOURCE_LABEL = "东方财富 F10 股东结构"

TABLE = "company_shareholders"

# 十大股东一个季度才变一次，缓存一天足够
DEFAULT_CACHE_HOURS = 24
# 股东户数默认回看几期：八个季度约两年
DEFAULT_PERIODS = 8
# 本地缓存固定保留最多十期，调用方要几期再切片
MAX_PERIODS = 10
# 文本摘要里十大股东列几个
SUMMARY_TOP = 5


class ShareholderError(Exception):
    """股东结构操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def shareholders_from_payload(
    payload: dict,
    code: str,
    periods: int = DEFAULT_PERIODS,
) -> dict:
    """把 F10 股东研究返回整理成摘要（纯函数）。"""
    if not isinstance(payload, dict):
        raise ShareholderError(f"{code}：股东结构接口返回格式异常")

    try:
        periods = min(max(int(periods), 1), MAX_PERIODS)
    except (TypeError, ValueError):
        periods = DEFAULT_PERIODS

    top10 = _top_holders(payload.get("sdgd"), free_float=False)
    free_top10 = _top_holders(payload.get("sdltgd"), free_float=True)
    if not top10 and not free_top10:
        raise ShareholderError(f"{code}：没有取到股东结构数据")

    as_of = top10[0]["period"] if top10 else free_top10[0]["period"]
    history = _holder_history(payload.get("gdrs"), periods)
    latest = history[-1] if history else None

    controller = None
    for row in payload.get("sjkzr") or []:
        if not isinstance(row, dict):
            continue
        name = _text(row.get("HOLDER_NAME"))
        if name:
            controller = {"name": name, "ratio": _number(row.get("HOLD_RATIO"))}
            break

    return {
        "code": code,
        "as_of": as_of,
        "controller": controller,
        "top10": top10,
        "free_float_top10": free_top10,
        "holder_count": {
            "latest": latest,
            "history": history,
        },
        "counts": {
            "top10": len(top10),
            "free_float_top10": len(free_top10),
            "holder_periods": len(history),
        },
    }


def fetch_shareholders(code: str) -> dict:
    """抓取股东结构数据；缓存始终保留最多十期。"""
    code = normalize_stock_code(code)
    if not code:
        raise ShareholderError("请输入股票代码")
    prefix, digits = su.get_stock_prefix(code)
    if prefix not in ("SH", "SZ", "BJ"):
        raise ShareholderError(f"{code}：无法判断交易所")

    try:
        response = requests.get(
            ENDPOINT,
            params={"code": f"{prefix}{digits}"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except ShareholderError:
        raise
    except Exception as error:
        raise ShareholderError(f"获取股东结构失败：{error}") from error

    return shareholders_from_payload(payload, code, periods=MAX_PERIODS)


def ensure_shareholders(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取股东结构，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ShareholderError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_shareholders(code)
    except ShareholderError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_shareholders(
    code: str,
    periods: int = DEFAULT_PERIODS,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise ShareholderError("请输入股票代码")

    result = ensure_shareholders(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    data = _apply_periods(result["data"], periods)
    stock = get_stock(code)
    name = stock.name if stock is not None else None
    return {
        "available": True,
        "code": code,
        "name": name,
        "as_of": data.get("as_of"),
        "controller": data.get("controller"),
        "top10": data.get("top10") or [],
        "free_float_top10": data.get("free_float_top10") or [],
        "holder_count": data.get("holder_count") or {},
        "counts": data.get("counts") or {},
        "fetched_at": data.get("fetched_at") or "",
        "stale": result.get("stale", False),
        "sources": {SOURCE_LABEL: data.get("as_of")},
        "summary": format_shareholders(
            data, code=code, name=name, as_of=data.get("as_of")
        ),
    }


def format_shareholders(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：身份 + 十大股东 + 股东户数。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    holder_count = data.get("holder_count") or {}
    latest = holder_count.get("latest") or {}
    controller = data.get("controller") or {}

    parts = [f"{name}({code})" if name else (code or "股东结构")]
    if as_of:
        parts.append(f"股东结构 {as_of}")
    if latest.get("holders") is not None:
        text = f"股东 {_count(latest['holders'])} 户"
        if latest.get("period") and latest["period"] != as_of:
            text += f"（{latest['period']}）"
        if latest.get("holders_change_pct") is not None:
            text += f"，较上期 {latest['holders_change_pct']:+.1f}%"
        parts.append(text)
    if controller.get("name"):
        ratio = controller.get("ratio")
        parts.append(
            f"实控人 {controller['name']}"
            + (f" {ratio:.1f}%" if ratio is not None else "")
        )
    lines = [" · ".join(parts)]

    for key, label in (("top10", "十大股东"), ("free_float_top10", "十大流通股东")):
        rows = data.get(key) or []
        if not rows:
            continue
        shown = []
        for row in rows[:SUMMARY_TOP]:
            text = f"{row.get('name')} {_ratio(row.get('ratio'))}"
            if row.get("change") is not None:
                text += f"（{_change(row['change'])}）"
            elif row.get("change_text"):
                text += f"（{row['change_text']}）"
            shown.append(text)
        lines.append(f"{label}：" + "、".join(shown))

    if len(holder_count.get("history") or []) >= 2:
        first = holder_count["history"][0]
        last = holder_count["history"][-1]
        lines.append(
            f"股东户数：{first.get('period')} {_count(first.get('holders'))} 户"
            f" → {last.get('period')} {_count(last.get('holders'))} 户"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _apply_periods(data: dict, periods: int) -> dict:
    """从缓存的最多十期里切出调用方要的期数，不修改缓存对象。"""
    try:
        periods = min(max(int(periods), 1), MAX_PERIODS)
    except (TypeError, ValueError):
        periods = DEFAULT_PERIODS
    holder = data.get("holder_count") or {}
    history = (holder.get("history") or [])[-periods:]
    counts = dict(data.get("counts") or {})
    counts["holder_periods"] = len(history)
    return {
        **data,
        "holder_count": {
            "latest": history[-1] if history else None,
            "history": history,
        },
        "counts": counts,
    }


def _top_holders(rows, free_float: bool) -> list[dict]:
    normalized = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        period = _date(row.get("END_DATE"))
        name = _text(row.get("HOLDER_NAME"))
        if not period or not name:
            continue
        change_text = _text(row.get("HOLD_NUM_CHANGE"))
        change = _number(change_text)
        item = {
            "period": period,
            "rank": _int(row.get("HOLDER_RANK")),
            "name": name,
            "share_type": _text(row.get("SHARES_TYPE")),
            "shares": _number(row.get("HOLD_NUM")),
            "ratio": _number(
                row.get("FREE_HOLDNUM_RATIO") if free_float
                else row.get("HOLD_NUM_RATIO")
            ),
            "change": change,
            "change_text": None if change is not None else change_text,
            "change_ratio": _number(row.get("CHANGE_RATIO")),
        }
        if free_float:
            item["holder_type"] = _text(row.get("HOLDER_TYPE"))
        normalized.append(item)

    if not normalized:
        return []
    latest = max(item["period"] for item in normalized)
    latest_rows = [item for item in normalized if item["period"] == latest]
    return sorted(
        latest_rows,
        key=lambda item: item["rank"] if item["rank"] is not None else 999,
    )[:10]


def _holder_history(rows, periods: int) -> list[dict]:
    by_period: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        period = _date(row.get("END_DATE"))
        holders = _int(row.get("HOLDER_TOTAL_NUM"))
        if not period or holders is None:
            continue
        by_period[period] = {
            "period": period,
            "holders": holders,
            "holders_change_pct": _number(row.get("TOTAL_NUM_RATIO")),
            "avg_free_shares": _number(row.get("AVG_FREE_SHARES")),
            "avg_free_shares_change_pct": _number(row.get("AVG_FREESHARES_RATIO")),
            "focus": _text(row.get("HOLD_FOCUS")),
            "top10_ratio": _number(row.get("HOLD_RATIO_TOTAL")),
            "free_float_top10_ratio": _number(row.get("FREEHOLD_RATIO_TOTAL")),
        }
    ordered = [by_period[key] for key in sorted(by_period)]
    return ordered[-periods:]


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
    # 缓存时间以表列为准；数据体里不能留旧值，否则过期判断会失效
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


def _ratio(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _count(value) -> str:
    return "—" if value is None else f"{int(value):,}"


def _change(value: float) -> str:
    return f"{value / 1e4:+.1f}万股"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
