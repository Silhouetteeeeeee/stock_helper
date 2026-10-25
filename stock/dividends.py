"""分红历史：公司把多少利润分给了股东，是否持续、是否透支。

公告只说明"发生了什么"，分红数据回答"赚到的钱有多少回到了股东手里"。
这里取东方财富分红送配数据，按报告期整理现金分红、送转股、实施进度与
除权除息日，再按年度汇总现金分红总额，并用年报归母净利润计算分红率。

* ``ensure_dividends(code)``    按需取数，本地缓存一天
* ``build_dividends(code)``     生成分析用摘要
* ``dividends_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_dividends(...)``     渲染成可读文本

口径：现金分红是税前每 10 股派息（元）；送股、转增是每 10 股股数；
分红总额按"每 10 股派息 / 10 × 报告期总股本"估算，单位元；分红率是
年度现金分红总额 / 年报归母净利润，百分数（62.2 表示 62.2%）。缺失值
保留为 None，不填 0。``build_dividends`` 会访问网络，界面里请放到后台
线程调用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code
from .finance_service import FinanceError, ensure_financials

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_SHAREBONUS_DET"
PAGE_URL = "https://data.eastmoney.com/yjfp/detail/{code}.html"
SOURCE_LABEL = "东方财富 分红送配"

TABLE = "company_dividends"

# 分红方案一年只更新几次，缓存一天足够；方案实施期再手动 force
DEFAULT_CACHE_HOURS = 24
# 默认回看十年，最多二十年
DEFAULT_YEARS = 10
MAX_YEARS = 20
# 文本摘要里年度现金分红最多列几年
SUMMARY_YEARS = 5
# 方案原文可能是一整段话（例如"不分配"的说明），摘要里截断
SUMMARY_PLAN_CHARS = 40

PERIOD_LABELS = {
    "03": "一季报",
    "06": "中报",
    "09": "三季报",
    "12": "年报",
}


class DividendError(Exception):
    """分红数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def dividends_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    financial_rows: list[dict] | tuple[dict, ...] = (),
) -> dict:
    """把接口返回整理成分红摘要（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise DividendError(f"{code}：分红接口返回格式异常")

    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = _normalize_row(row)
        if event is not None:
            events.append(event)
    return dividends_from_events(
        events, code, years=years, financial_rows=financial_rows
    )


def dividends_from_events(
    events: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    financial_rows: list[dict] | tuple[dict, ...] = (),
) -> dict:
    """由已规范化的分红记录生成年度序列（纯函数）。"""
    if not isinstance(events, (list, tuple)):
        raise DividendError(f"{code}：分红记录格式异常")

    years = _window(years)
    history = _dedupe(
        [event for event in events if isinstance(event, dict)
         and event.get("report_date")]
    )
    ordered = history
    if ordered:
        first_year = max(event["fiscal_year"] for event in ordered) - years + 1
        ordered = [
            event for event in ordered if event["fiscal_year"] >= first_year
        ]

    financial, completed_years = _annual_financials(financial_rows)
    annual = _annual_rows(ordered, financial)
    history_annual = (
        annual if len(history) == len(ordered)
        else _annual_rows(history, financial)
    )
    counts = _counts(ordered, annual, completed_years, history_annual)
    data = {
        "available": True,
        "code": code,
        "as_of": ordered[0]["report_date"] if ordered else None,
        "has_dividends": any(
            event.get("cash_per_10") is not None
            or (event.get("share_bonus_per_10") or 0) > 0
            for event in ordered
        ),
        "latest": ordered[0] if ordered else None,
        "annual": annual,
        "events": ordered,
        "counts": counts,
        "notes": [],
    }
    data["notes"] = _notes(ordered, annual, counts)
    data["summary"] = format_dividends(data, code=code)
    return data


def fetch_dividends(code: str) -> dict:
    """抓取分红记录；只缓存规范化后的事件，不缓存分红率。"""
    code = normalize_stock_code(code)
    if not code:
        raise DividendError("请输入股票代码")

    try:
        rows = _request(code)
    except DividendError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise DividendError(f"获取分红数据失败：{error}") from error

    events = _dedupe(
        [
            event
            for row in rows
            if isinstance(row, dict)
            for event in [_normalize_row(row)]
            if event is not None
        ]
    )
    return {
        "code": code,
        "events": events,
        "count": len(events),
    }


def ensure_dividends(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取分红数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise DividendError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_dividends(code)
    except DividendError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_dividends(
    code: str,
    years: int = DEFAULT_YEARS,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上净利润、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise DividendError("请输入股票代码")

    result = ensure_dividends(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    events = result["data"].get("events") or []

    financial_rows: list[dict] = []
    try:
        financial_rows = ensure_financials(code)
    except FinanceError:
        # 分红本身仍然可用，只是算不了分红率
        pass

    data = dividends_from_events(
        events,
        code,
        years=years,
        financial_rows=financial_rows,
    )
    stock = get_stock(code)
    name = stock.name if stock is not None else None
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL.format(code=code)
    data["summary"] = format_dividends(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_dividends(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：最新方案 + 最近完整年度 + 年度现金分红序列。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    events = data.get("events") or []
    annual = data.get("annual") or []
    counts = data.get("counts") or {}
    title = f"{name}({code})" if name else (code or "分红")

    if not events:
        lines = [f"{title} · 暂无分红记录"]
        if data.get("notes"):
            lines.append("提示：" + "；".join(data["notes"][:2]))
        lines.append(f"数据来源：{SOURCE_LABEL}")
        return "\n".join(lines)

    parts = [title]
    if as_of:
        parts.append(f"最新报告期 {as_of}")
    if counts.get("latest_cash_year"):
        parts.append(f"最近现金分红 {counts['latest_cash_year']} 年")
    if counts.get("consecutive_years"):
        parts.append(f"连续分红 {counts['consecutive_years']} 年")
    lines = [" · ".join(parts)]

    highlight = next(
        (row for row in reversed(annual) if row.get("payout_ratio") is not None),
        None,
    )
    if highlight is None:
        highlight = next(
            (row for row in reversed(annual) if row.get("cash_total") is not None
             and row.get("cash_total") > 0),
            None,
        )
    if highlight is not None:
        text = (
            f"{highlight['year']} 年度：现金分红 "
            f"{_yi(highlight.get('cash_total'))}"
        )
        if highlight.get("net_profit") is not None:
            text += (
                f"，{highlight.get('payout_basis') or '净利润'} "
                f"{_yi(highlight['net_profit'])}"
            )
        if highlight.get("payout_ratio") is not None:
            text += f"，分红率 {highlight['payout_ratio']:.1f}%"
        elif highlight.get("cash_total") is not None:
            text += "（未取得年报净利润，未计算分红率）"
        lines.append(text)

    latest = events[0]
    plan = latest.get("plan_short") or latest.get("plan") or "方案未披露"
    text = f"最新方案：{latest.get('report_date') or ''} {plan}".strip()
    if latest.get("cash_total") is not None:
        text += f"，合计 {_yi(latest['cash_total'])}"
    if latest.get("share_bonus_per_10"):
        text += f"，每 10 股送转 {_fmt(latest['share_bonus_per_10'])} 股"
    if latest.get("status"):
        text += f"（{latest['status']}）"
    if latest.get("ex_date"):
        text += f"，除权除息 {latest['ex_date']}"
    lines.append(text)

    cash_rows = [
        row for row in annual
        if row.get("cash_total") is not None and row.get("cash_total") > 0
    ][-SUMMARY_YEARS:]
    if cash_rows:
        lines.append(
            "年度现金分红："
            + " / ".join(
                f"{row['year']} {_yi_short(row['cash_total'])}"
                + (
                    f"({row['payout_ratio']:.1f}%)"
                    if row.get("payout_ratio") is not None else ""
                )
                for row in cash_rows
            )
        )

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    report_date = _date(row.get("REPORT_DATE"))
    if not report_date:
        return None

    plan = _text(row.get("IMPL_PLAN_PROFILE"))
    cash = _number(row.get("PRETAX_BONUS_RMB"))
    bonus = _number(row.get("BONUS_RATIO"))
    transfer = _number(row.get("IT_RATIO"))
    share_bonus = _number(row.get("BONUS_IT_RATIO"))
    if share_bonus is None:
        known = [value for value in (bonus, transfer) if value is not None]
        share_bonus = sum(known) if known else None
    if cash is None and not share_bonus and not plan:
        return None

    total_shares = _int(row.get("TOTAL_SHARES"))
    if total_shares is not None and total_shares <= 0:
        total_shares = None
    cash_total = (
        cash / 10.0 * total_shares
        if cash is not None and total_shares is not None
        else None
    )
    yield_ratio = _number(row.get("DIVIDENT_RATIO"))
    return {
        "report_date": report_date,
        "fiscal_year": int(report_date[:4]),
        "period_label": PERIOD_LABELS.get(report_date[5:7]),
        "plan": plan,
        "plan_short": _short_plan(plan),
        "status": _text(row.get("ASSIGN_PROGRESS")),
        "cash_per_10": cash,
        "cash_per_share": None if cash is None else cash / 10.0,
        "cash_total": cash_total,
        "bonus_per_10": bonus,
        "transfer_per_10": transfer,
        "share_bonus_per_10": share_bonus,
        "total_shares": total_shares,
        "yield_pct": None if yield_ratio is None else yield_ratio * 100.0,
        "eps": _number(row.get("BASIC_EPS")),
        "plan_notice_date": _date(row.get("PLAN_NOTICE_DATE")),
        "notice_date": _date(row.get("NOTICE_DATE")),
        "record_date": _date(row.get("EQUITY_RECORD_DATE")),
        "ex_date": _date(row.get("EX_DIVIDEND_DATE")),
    }


def _annual_rows(
    events: list[dict],
    financial: dict[int, tuple[float | None, str | None]],
) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for event in events:
        by_year.setdefault(event["fiscal_year"], []).append(event)

    rows = []
    for year in sorted(by_year):
        group = by_year[year]
        cash_complete = True
        cash_total = 0.0
        for event in group:
            if event.get("cash_per_10") is None:
                continue
            if event.get("cash_total") is None:
                cash_complete = False
            else:
                cash_total += event["cash_total"]

        net_profit, basis = financial.get(year, (None, None))
        payout = None
        if cash_complete and net_profit is not None and net_profit > 0:
            payout = round(cash_total / net_profit * 100.0, 1)
        rows.append({
            "year": year,
            "periods": sorted(event["report_date"] for event in group),
            "report_count": len(group),
            "cash_total": cash_total if cash_complete else None,
            "cash_complete": cash_complete,
            "net_profit": net_profit,
            "payout_ratio": payout,
            "payout_basis": basis if payout is not None else None,
            "share_bonus_count": sum(
                1 for event in group
                if (event.get("share_bonus_per_10") or 0) > 0
            ),
        })
    return rows


def _annual_financials(
    rows: list[dict] | tuple[dict, ...],
) -> tuple[dict[int, tuple[float | None, str | None]], set[int]]:
    financial: dict[int, tuple[float | None, str | None]] = {}
    completed: set[int] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        report_date = row.get("report_date")
        if not isinstance(report_date, str) or not report_date.endswith("-12-31"):
            continue
        try:
            year = int(report_date[:4])
        except ValueError:
            continue
        completed.add(year)
        net_profit = row.get("net_profit_attributable")
        basis = "归母净利润"
        if net_profit is None:
            net_profit = row.get("net_profit")
            basis = "净利润"
        profit = _number(net_profit)
        if profit is not None:
            financial[year] = (profit, basis)
    return financial, completed


def _counts(
    events: list[dict],
    annual: list[dict],
    completed_years: set[int],
    history_annual: list[dict] | None = None,
) -> dict:
    cash_years = [
        row["year"] for row in annual
        if row.get("cash_total") is not None and row["cash_total"] > 0
    ]
    latest_cash_year = max(cash_years) if cash_years else None
    consecutive = 0
    # 连续年数按完整历史算，不因文本只回看五年就把 2021 年漏掉。
    history = history_annual or annual
    history_cash_years = [
        row["year"] for row in history
        if row.get("cash_total") is not None and row["cash_total"] > 0
    ]
    if history_cash_years:
        # 只从完整年度往前数，避免把当年中期分红当成完整年度。
        # 没有财报时，用"年度分红方案本身是否为年报期"判断完整年度。
        complete = set(completed_years)
        if not complete:
            complete = {
                row["year"] for row in history
                if f"{row['year']}-12-31" in (row.get("periods") or [])
            }
        start = max(complete) if complete else max(history_cash_years)
        by_year = {row["year"]: row for row in history}
        while start in by_year:
            row = by_year[start]
            if row.get("cash_total") is None or row["cash_total"] <= 0:
                break
            consecutive += 1
            start -= 1
    return {
        "events": len(events),
        "years": len(annual),
        "cash_years": len(cash_years),
        "consecutive_years": consecutive,
        "latest_cash_year": latest_cash_year,
    }


def _notes(events: list[dict], annual: list[dict], counts: dict) -> list[str]:
    notes: list[str] = []
    if not events:
        notes.append("没有取到分红记录，可能从未分红或数据源未收录")
        return notes

    has_cash = any(event.get("cash_per_10") is not None for event in events)
    if has_cash:
        notes.append(
            "分红总额按税前每 10 股派息 ÷ 10 × 报告期总股本估算，"
            "未考虑回购专户等股本差异"
        )
        notes.append("分红率 = 年度现金分红总额 ÷ 年报归母净利润，按报告期归年")
    if any(event.get("yield_pct") is not None for event in events):
        notes.append("股息率由数据源按公告时点股价计算，不能代表当前股息率")

    missing_profit = [
        str(row["year"]) for row in annual
        if row.get("cash_total") and row.get("net_profit") is None
    ]
    if missing_profit:
        shown = "、".join(missing_profit[-3:])
        if len(missing_profit) > 3:
            notes.append(f"{shown} 等年度缺少年报净利润，未计算分红率")
        else:
            notes.append(f"{shown} 年度缺少年报净利润，未计算分红率")

    incomplete = [
        str(row["year"]) for row in annual if not row.get("cash_complete", True)
    ]
    if incomplete:
        notes.append(
            "、".join(incomplete) + " 年度部分分红缺少总股本，现金分红总额不完整"
        )

    high = [
        f"{row['year']} 年 {row['payout_ratio']:.1f}%"
        for row in annual
        if row.get("payout_ratio") is not None and row["payout_ratio"] > 100
    ]
    if high:
        notes.append(
            "分红率超过 100%：" + "、".join(high)
            + "，检查是否含特别分红或股本口径差异"
        )
    if not any(event.get("cash_per_10") for event in events):
        if any((event.get("share_bonus_per_10") or 0) > 0 for event in events):
            notes.append("有送转记录，但没有现金分红；送转股不增加股东现金回报")
        else:
            notes.append("记录为不分配不转增，没有现金分红或送转")
    return notes


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _request(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 3:
        payload = _get_page(code, page)
        if payload.get("success") is False:
            message = payload.get("message") or "接口返回失败"
            raise DividendError(f"{code}：获取分红数据失败（{message}）")
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise DividendError(f"{code}：分红接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise DividendError(f"{code}：分红接口数据格式异常")
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
                "SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,"
                "PRETAX_BONUS_RMB,BONUS_RATIO,IT_RATIO,BONUS_IT_RATIO,"
                "TOTAL_SHARES,ASSIGN_PROGRESS,IMPL_PLAN_PROFILE,"
                "DIVIDENT_RATIO,BASIC_EPS,PLAN_NOTICE_DATE,NOTICE_DATE,"
                "EQUITY_RECORD_DATE,EX_DIVIDEND_DATE"
            ),
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": str(page),
            "pageSize": "100",
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
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
        raise DividendError(f"{code}：分红接口返回格式异常")
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
def _dedupe(events: list[dict]) -> list[dict]:
    """同一报告期只保留最新公告的一条，并按报告期倒序。"""
    by_date: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        report_date = event.get("report_date")
        if not report_date:
            continue
        current = by_date.get(report_date)
        if current is None or _stamp(event) >= _stamp(current):
            by_date[report_date] = event
    return sorted(
        by_date.values(), key=lambda item: item["report_date"], reverse=True
    )


def _stamp(event: dict) -> str:
    return event.get("notice_date") or event.get("plan_notice_date") or ""


def _short_plan(plan: str | None) -> str | None:
    if not plan:
        return None
    text = plan
    for separator in ("(", "（"):
        if separator in text:
            text = text.split(separator, 1)[0]
    text = text.strip() or plan
    if len(text) > SUMMARY_PLAN_CHARS:
        text = text[:SUMMARY_PLAN_CHARS].rstrip("，、；： ") + "…"
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


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _yi(value) -> str:
    return "—" if value is None else f"{value / 1e8:.2f}亿"


def _yi_short(value) -> str:
    return "—" if value is None else f"{value / 1e8:.2f}亿"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
