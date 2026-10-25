"""股票回购：计划金额、实施进度、已回购规模与用途。

回购公告回答"公司打算花多少钱、回购多少股、用来做什么"，实施公告
回答"实际执行到哪一步"。这里取东方财富回购列表，按回购计划归并，
保留计划金额/股数/价格区间、实际累计回购金额/股数/价格、完成度和
用途；再按公告年度汇总，区分注销、员工持股与股权激励。

* ``ensure_buybacks(code)``    按需取数，本地缓存一天
* ``build_buybacks(code)``     生成分析用摘要
* ``buybacks_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_buybacks(...)``     渲染成可读文本

口径：金额单位元，股数单位股；``years`` 按公告日所在自然年回看，
默认五年。计划金额与实际金额均为公告口径的累计值，不是某日成交；
完成度按"实际已回购金额 ÷ 计划金额下限"估算，下限缺失时用上限。
用于注销的回购减少总股本，用于员工持股/股权激励的回购不减少总股本。
缺失值保留为 None，不填 0。``build_buybacks`` 会访问网络，界面里请
放到后台线程调用。
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
REPORT_NAME = "RPTA_WEB_GETHGLIST_NEW"
PAGE_URL = "https://data.eastmoney.com/gphg/detail/{code}-{date}.html"
LIST_PAGE_URL = "https://data.eastmoney.com/gphg/"
SOURCE_LABEL = "东方财富 股票回购"

TABLE = "company_buybacks"

# 回购计划更新不频繁，缓存一天足够
DEFAULT_CACHE_HOURS = 24
# 回购计划比增减持稀疏，默认回看五年，最多十年
DEFAULT_YEARS = 5
MAX_YEARS = 10
# 明细最多返回多少个计划，年度汇总仍使用全部计划
MAX_PLANS = 30
# 文本里年度最多列几项
SUMMARY_YEARS = 5
# 用途原文在明细里保留，摘要只截取一段
SUMMARY_PURPOSE_CHARS = 60

PROGRESS_LABELS = {
    "001": "董事会预案",
    "002": "股东大会通过",
    "003": "股东大会否决",
    "004": "实施中",
    "005": "停止实施",
    "006": "完成实施",
}

# 仍在推进的计划：预案、股东大会通过、实施中
ACTIVE_CODES = ("001", "002", "004")
PENDING_CODES = ("001", "002")
COMPLETED_CODE = "006"
STOPPED_CODES = ("003", "005")

CANCEL_KEYWORDS = ("注销", "减少注册资本", "减少公司注册资本")
EMPLOYEE_KEYWORDS = ("员工持股",)
INCENTIVE_KEYWORDS = ("股权激励",)


class BuybackError(Exception):
    """股票回购数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def buybacks_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """把接口返回整理成回购摘要（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise BuybackError(f"{code}：股票回购接口返回格式异常")

    plans = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        plan = _normalize_row(row)
        if plan is not None:
            plans.append(plan)
    return buybacks_from_plans(plans, code, years=years, today=today)


def buybacks_from_plans(
    plans: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """由已规范化的回购计划生成年度汇总（纯函数）。"""
    if not isinstance(plans, (list, tuple)):
        raise BuybackError(f"{code}：股票回购计划格式异常")

    years = _window(years)
    history = _dedupe([
        plan for plan in plans
        if isinstance(plan, dict) and plan.get("announce_date")
    ])
    end = _date(today) or datetime.now().strftime("%Y-%m-%d")
    start_year = int(end[:4]) - years + 1
    start = f"{start_year}-01-01"
    window = [
        plan for plan in history if start <= plan["announce_date"] <= end
    ]

    annual = _annual_rows(window)
    counts = _counts(window)
    truncated = max(0, len(window) - MAX_PLANS)
    updates = [
        plan.get("update_date") or plan.get("announce_date")
        for plan in history
    ]
    data = {
        "available": True,
        "code": code,
        "as_of": max(updates) if updates else None,
        "window_start": start,
        "window_end": end,
        "years": years,
        "has_buybacks": bool(window),
        "latest": history[0] if history else None,
        "latest_window": window[0] if window else None,
        "annual": annual,
        "plans": window[:MAX_PLANS],
        "plans_truncated": truncated,
        "counts": counts,
        "notes": [],
    }
    data["notes"] = _notes(history, window, counts, years, truncated)
    data["summary"] = format_buybacks(data, code=code)
    return data


def fetch_buybacks(code: str) -> dict:
    """抓取回购计划；只缓存规范化后的计划。"""
    code = normalize_stock_code(code)
    if not code:
        raise BuybackError("请输入股票代码")

    try:
        rows = _request(code)
    except BuybackError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise BuybackError(f"获取股票回购数据失败：{error}") from error

    plans = _dedupe([
        plan
        for row in rows
        if isinstance(row, dict)
        for plan in [_normalize_row(row)]
        if plan is not None
    ])
    return {
        "code": code,
        "plans": plans,
        "count": len(plans),
    }


def ensure_buybacks(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取回购数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise BuybackError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_buybacks(code)
    except BuybackError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_buybacks(
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise BuybackError("请输入股票代码")

    result = ensure_buybacks(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    plans = result["data"].get("plans") or []
    data = buybacks_from_plans(plans, code, years=years, today=today)

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name and plans:
        name = plans[0].get("security_name")
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    latest = data.get("latest")
    if latest and latest.get("announce_date"):
        data["url"] = PAGE_URL.format(
            code=code, date=latest["announce_date"]
        )
    else:
        data["url"] = LIST_PAGE_URL
    data["summary"] = format_buybacks(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_buybacks(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：回购规模 + 进度 + 用途 + 年度节奏。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    years = data.get("years") or DEFAULT_YEARS
    counts = data.get("counts") or {}
    annual = data.get("annual") or []
    title = f"{name}({code})" if name else (code or "股票回购")

    parts = [title, f"近{years}年"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if counts.get("plans"):
        headline = f"回购计划 {counts['plans']} 个：在途 {counts.get('active', 0)}"
        if counts.get("pending"):
            headline += f"（待实施 {counts['pending']}）"
        headline += (
            f" / 完成 {counts.get('completed', 0)}"
            f" / 停止 {counts.get('stopped', 0)}"
        )
        lines.append(headline)

        actual = counts.get("actual_amount")
        if actual is not None:
            text = f"已回购 {_amount(actual)}"
            if counts.get("actual_shares") is not None:
                text += f" / {_shares(counts['actual_shares'])}"
            planned = _range_amount(
                counts.get("planned_amount_low"),
                counts.get("planned_amount_high"),
            )
            if planned != "—":
                text += f"；计划金额 {planned}"
            if counts.get("completion_pct") is not None:
                text += f"，下限完成度 {counts['completion_pct']:.1f}%"
            lines.append(text)

        uses = []
        if counts.get("cancel_plans"):
            uses.append(f"注销 {counts['cancel_plans']} 个")
        if counts.get("incentive_plans"):
            uses.append(f"员工持股或股权激励 {counts['incentive_plans']} 个")
        if uses:
            lines.append("用途：" + " / ".join(uses))

        year_rows = [row for row in annual if row.get("plans")][-SUMMARY_YEARS:]
        if year_rows:
            lines.append(
                "年度已回购："
                + " / ".join(
                    f"{row['year']} {_amount(row.get('actual_amount'))}"
                    for row in year_rows
                )
            )

        latest = data.get("latest_window") or data.get("latest")
        if latest:
            text = (
                f"最新计划：{latest.get('announce_date')} 计划金额 "
                f"{_range_amount(latest.get('planned_amount_low'), latest.get('planned_amount_high'))}"
            )
            if latest.get("actual_amount") is not None:
                text += f"，已回购 {_amount(latest['actual_amount'])}"
            text += f"（{latest.get('status') or '进度未知'}"
            if latest.get("amount_completion_pct") is not None:
                text += f"，下限完成度 {latest['amount_completion_pct']:.1f}%"
            text += "）"
            lines.append(text)
    elif data.get("latest"):
        latest = data["latest"]
        lines.append(
            f"近{years}年没有披露的回购计划；最近一次为 "
            f"{latest.get('announce_date')} "
            f"{latest.get('status') or '进度未知'}，"
            f"计划金额 {_range_amount(latest.get('planned_amount_low'), latest.get('planned_amount_high'))}"
        )
    else:
        lines.append("暂无股票回购记录")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    announce_date = _date(row.get("DIM_DATE"))
    if not announce_date:
        return None

    progress_code = _text(row.get("REPURPROGRESS"))
    status = PROGRESS_LABELS.get(progress_code or "", "进度未知")
    planned_low = _number(row.get("REPURAMOUNTLOWER"))
    planned_high = _number(row.get("REPURAMOUNTLIMIT"))
    actual_amount = _number(row.get("REPURAMOUNT"))
    actual_shares = _int(row.get("REPURNUM"))
    purpose = _text(row.get("REPUROBJECTIVE")) or ""
    tags = _purpose_tags(purpose)

    plan_id = _text(row.get("REPURCODE")) or (
        f"{announce_date}-{_text(row.get('REPURSTARTDATE')) or ''}"
        f"-{planned_low if planned_low is not None else ''}"
    )

    return {
        "plan_id": plan_id,
        "announce_date": announce_date,
        "year": int(announce_date[:4]),
        "status": status,
        "progress_code": progress_code,
        "is_active": progress_code in ACTIVE_CODES,
        "pending": progress_code in PENDING_CODES,
        "completed": progress_code == COMPLETED_CODE,
        "stopped": progress_code in STOPPED_CODES,
        "start_date": _date(row.get("REPURSTARTDATE")),
        "end_date": _date(row.get("REPURENDDATE")),
        "update_date": _date(row.get("UPD")),
        "finish_date": _date(row.get("FINISHDATE")),
        "advanced_date": _date(row.get("REPURADVANCEDATE")),
        "planned_amount_low": planned_low,
        "planned_amount_high": planned_high,
        "planned_shares_low": _int(row.get("REPURNUMLOWER")),
        "planned_shares_high": _int(row.get("REPURNUMCAP")),
        "planned_price_low": _number(row.get("REPURPRICELOWER")),
        "planned_price_high": _number(row.get("REPURPRICECAP")),
        "planned_ratio_low_pct": _number(row.get("ZSZXX")),
        "planned_ratio_high_pct": _number(row.get("ZSZSX")),
        "actual_amount": actual_amount,
        "actual_shares": actual_shares,
        "actual_price_low": _number(row.get("REPURPRICELOWER1")),
        "actual_price_high": _number(row.get("REPURPRICECAP1")),
        "actual_ratio_total_pct": _number(row.get("ZJSZBL")),
        "actual_ratio_float_pct": _number(row.get("ZJLTBL")),
        "amount_completion_pct": _completion(
            actual_amount, planned_low, planned_high
        ),
        "shares_completion_pct": _completion(
            actual_shares,
            _int(row.get("REPURNUMLOWER")),
            _int(row.get("REPURNUMCAP")),
        ),
        "purpose": purpose,
        "purpose_short": _clip(purpose, SUMMARY_PURPOSE_CHARS),
        "purpose_tags": tags,
        "cancel": "注销" in tags,
        "incentive": any(
            tag in tags for tag in ("员工持股", "股权激励")
        ),
        "security_name": _text(row.get("SECURITYSHORTNAME")),
    }


def _annual_rows(plans: list[dict]) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for plan in plans:
        by_year.setdefault(plan["year"], []).append(plan)

    rows = []
    for year in sorted(by_year):
        group = by_year[year]
        actual_amount = _sum_optional([
            plan.get("actual_amount") for plan in group
        ])
        actual_shares = _sum_optional([
            plan.get("actual_shares") for plan in group
        ])
        planned_low = _sum_optional([
            plan.get("planned_amount_low") for plan in group
        ])
        planned_high = _sum_optional([
            plan.get("planned_amount_high") for plan in group
        ])
        rows.append({
            "year": year,
            "plans": len(group),
            "active": sum(1 for plan in group if plan.get("is_active")),
            "pending": sum(1 for plan in group if plan.get("pending")),
            "completed": sum(1 for plan in group if plan.get("completed")),
            "stopped": sum(1 for plan in group if plan.get("stopped")),
            "actual_amount": actual_amount,
            "actual_shares": actual_shares,
            "planned_amount_low": planned_low,
            "planned_amount_high": planned_high,
            "completion_pct": _round(
                _completion(actual_amount, planned_low, planned_high)
            ),
            "cancel_plans": sum(1 for plan in group if plan.get("cancel")),
            "incentive_plans": sum(
                1 for plan in group if plan.get("incentive")
            ),
            "amount_complete": all(
                plan.get("actual_amount") is not None for plan in group
            ),
        })
    return rows


def _counts(plans: list[dict]) -> dict:
    actual_amount = _sum_optional([
        plan.get("actual_amount") for plan in plans
    ])
    actual_shares = _sum_optional([
        plan.get("actual_shares") for plan in plans
    ])
    planned_low = _sum_optional([
        plan.get("planned_amount_low") for plan in plans
    ])
    planned_high = _sum_optional([
        plan.get("planned_amount_high") for plan in plans
    ])
    updates = [
        plan.get("update_date") or plan.get("announce_date")
        for plan in plans
    ]
    return {
        "plans": len(plans),
        "active": sum(1 for plan in plans if plan.get("is_active")),
        "pending": sum(1 for plan in plans if plan.get("pending")),
        "completed": sum(1 for plan in plans if plan.get("completed")),
        "stopped": sum(1 for plan in plans if plan.get("stopped")),
        "cancel_plans": sum(1 for plan in plans if plan.get("cancel")),
        "incentive_plans": sum(
            1 for plan in plans if plan.get("incentive")
        ),
        "actual_amount": actual_amount,
        "actual_shares": actual_shares,
        "planned_amount_low": planned_low,
        "planned_amount_high": planned_high,
        "completion_pct": _round(
            _completion(actual_amount, planned_low, planned_high)
        ),
        "missing_actual": sum(
            1 for plan in plans if plan.get("actual_amount") is None
        ),
        "amount_complete": all(
            plan.get("actual_amount") is not None for plan in plans
        ),
        "planned_amount_complete": all(
            plan.get("planned_amount_low") is not None
            or plan.get("planned_amount_high") is not None
            for plan in plans
        ),
        "latest_update": max(updates) if updates else None,
    }


def _notes(
    history: list[dict],
    plans: list[dict],
    counts: dict,
    years: int,
    truncated: int,
) -> list[str]:
    notes: list[str] = []
    if not history:
        notes.append("没有取到股票回购记录，可能数据源未收录")
        return notes

    if not plans:
        latest = history[0]
        notes.append(
            f"近{years}年没有披露的回购计划；最近一次为"
            f"{latest['announce_date']}（{latest.get('status') or '进度未知'}）"
        )
        notes.append("只统计已披露的回购计划，未覆盖未披露的回购")
        return notes

    notes.append("金额与股数为累计已回购，不是某日成交；计划金额为公告区间")
    notes.append(
        "下限完成度=实际已回购金额÷计划金额下限，超过100%表示已超过下限"
    )
    notes.append(
        "计划用于注销的股份在注销完成后减少总股本；"
        "员工持股或股权激励不减少总股本"
    )
    if counts.get("missing_actual"):
        notes.append(
            f"{counts['missing_actual']} 个计划缺少已回购金额，合计不完整"
        )
    if any(
        plan.get("is_active")
        and plan.get("end_date")
        and plan["end_date"] < datetime.now().strftime("%Y-%m-%d")
        for plan in plans
    ):
        notes.append(
            "有计划的回购期限已过但进度仍显示在途，需核对最新实施公告"
        )
    if any(
        plan.get("actual_amount") is not None
        and plan.get("planned_amount_low") is not None
        and plan["actual_amount"] > plan["planned_amount_low"] * 1.01
        for plan in plans
    ):
        notes.append("部分计划实际回购金额已超过计划下限，完成度超过100%属正常")
    if truncated:
        notes.append(
            f"明细只列最近 {MAX_PLANS} 个计划，年度与合计使用全部计划"
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
            raise BuybackError(f"{code}：获取股票回购数据失败（{message}）")
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise BuybackError(f"{code}：股票回购接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise BuybackError(f"{code}：股票回购接口数据格式异常")
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
            "filter": f'(DIM_SCODE="{code}")',
            "pageNumber": str(page),
            "pageSize": "100",
            "sortColumns": "UPD,DIM_DATE,DIM_SCODE",
            "sortTypes": "-1,-1,1",
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
        raise BuybackError(f"{code}：股票回购接口返回格式异常")
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
def _dedupe(plans: list[dict]) -> list[dict]:
    """同一回购计划只保留 UPD 最新的一条，其次保留数据更完整的一条。"""
    by_id: dict[str, dict] = {}
    for plan in plans:
        if not isinstance(plan, dict) or not plan.get("announce_date"):
            continue
        key = str(plan.get("plan_id") or (
            plan.get("announce_date"),
            plan.get("start_date"),
            plan.get("planned_amount_low"),
        ))
        current = by_id.get(key)
        if current is None or _plan_stamp(plan) > _plan_stamp(current):
            by_id[key] = plan
    return sorted(
        by_id.values(),
        key=lambda item: (
            item.get("announce_date") or "",
            item.get("update_date") or "",
        ),
        reverse=True,
    )


def _plan_stamp(plan: dict) -> tuple:
    return (
        plan.get("update_date") or "",
        _completeness(plan),
        plan.get("actual_amount") is not None,
    )


def _completeness(plan: dict) -> int:
    return sum(
        1 for key in (
            "actual_amount", "actual_shares", "planned_amount_low",
            "planned_amount_high", "planned_shares_low",
            "planned_shares_high", "planned_price_low", "planned_price_high",
            "start_date", "end_date", "purpose",
        )
        if plan.get(key) is not None
    )


def _purpose_tags(purpose: str) -> list[str]:
    tags: list[str] = []
    if any(word in purpose for word in CANCEL_KEYWORDS):
        tags.append("注销")
    if any(word in purpose for word in EMPLOYEE_KEYWORDS):
        tags.append("员工持股")
    if any(word in purpose for word in INCENTIVE_KEYWORDS):
        tags.append("股权激励")
    return tags


def _completion(
    actual: float | int | None,
    low: float | int | None,
    high: float | int | None,
) -> float | None:
    if actual is None:
        return None
    base = low if low not in (None, 0) else high
    if base in (None, 0):
        return None
    try:
        return round(float(actual) / float(base) * 100.0, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _sum_optional(values: list) -> float | int | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    total = sum(known)
    if all(isinstance(value, int) for value in known):
        return int(total)
    return round(float(total), 2)


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _range_amount(low, high) -> str:
    if low is not None and high is not None:
        if low == high:
            return _amount(low)
        return f"{_amount(low)}~{_amount(high)}"
    if low is not None:
        return _amount(low)
    if high is not None:
        return _amount(high)
    return "—"


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


def _clip(text: str, size: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= size else text[:size] + "…"


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
