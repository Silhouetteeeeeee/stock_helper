"""同业对比：同一行业、同一报告期的财务与估值位置。

数据来自东方财富数据中心：

* ``RPT_LICO_FN_CPD`` 业绩报表：按行业板块 + 报告期取营收、归母净利、
  同比、毛利率、加权 ROE 等；
* 估值复用 ``stock.valuation`` 的行业快照，按代码拼上 PE(TTM)、PB(MRQ)
  与总市值。

对外四个函数：

* ``ensure_peers(code)``        按需取数，本地缓存 6 小时
* ``build_peers(code)``         生成对比摘要：行业中位数、排名、可比公司
* ``peers_from_rows(...)``      纯函数，由业绩报表行生成，便于测试
* ``format_peers(...)``         渲染成可读文本

口径：金额是元；比例是百分数（15.9 表示 15.9%）；PE/PB 是倍数。缺失值
保留为 None，不填 0。``build_peers`` 会访问网络，界面里请放到后台线程。
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime
from typing import Iterable

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code
from .valuation import ValuationError, ensure_valuation

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_LICO_FN_CPD"
PAGE_URL = "https://data.eastmoney.com/bbsj/{month}/yjbb.html"
SOURCE_LABEL = "东方财富 业绩报表"

TABLE = "company_peers"

# 财报一天内不会反复变，缓存六小时；估值部分另有自己的缓存
DEFAULT_CACHE_HOURS = 6
DEFAULT_LIMIT = 8
MIN_LIMIT = 3
MAX_LIMIT = 20
# 同业少于这个数就回退上一报告期，避免早披露期只和几家公司比
MIN_PEERS = 5
# 最多向前找四期
MAX_FALLBACK_PERIODS = 4
# 文本摘要里列几家规模相近的公司
SUMMARY_PEERS = 5
# 只保留 A 股，B 股与同代码重复行不进入对比
A_SHARE = "A股"

# 统计口径：这三个指标只取正值（PE/PB 为负没有大小意义，市值为 0 无意义）
POSITIVE_ONLY = {"pe_ttm", "pb_mrq", "market_cap"}
METRICS = (
    "revenue", "revenue_yoy", "net_profit", "profit_yoy",
    "gross_margin", "net_margin", "roe", "pe_ttm", "pb_mrq", "market_cap",
)

# 规模相近的带宽；带宽内按倍数差排序，不够再用带宽外补齐
SIZE_BAND = (0.3, 3.0)


class PeerError(Exception):
    """同业对比操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def peers_from_rows(
    rows: list[dict],
    code: str,
    limit: int = DEFAULT_LIMIT,
    valuation_rows: Iterable[dict] = (),
    report: dict | None = None,
    notes: Iterable[str] = (),
    valuation_date: str | None = None,
    valuation_stale: bool = False,
    valuation_error: str | None = None,
) -> dict:
    """由业绩报表行与估值行生成同业对比（纯函数）。"""
    try:
        limit = min(max(int(limit), MIN_LIMIT), MAX_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    rows = _dedupe(rows)
    if isinstance(valuation_rows, dict):
        valuation_rows = list(valuation_rows.values())
    else:
        valuation_rows = list(valuation_rows or ())
    subject_raw = next(
        (row for row in rows if _text(row.get("SECURITY_CODE")) == code),
        None,
    )
    if subject_raw is None:
        raise PeerError(f"{code}：同业数据里没有本公司")

    valuations = _valuation_index(valuation_rows)
    normalized = [
        item for item in (_normalize(row, valuations) for row in rows) if item
    ]
    subject = next((item for item in normalized if item["code"] == code), None)
    if subject is None:
        raise PeerError(f"{code}：同业数据里没有本公司")

    period = (report or {}).get("period") or _date(subject_raw.get("REPORTDATE"))
    report_data = {
        "period": period,
        "qdate": _text((report or {}).get("qdate")) or _text(subject_raw.get("QDATE")),
        "datatype": (
            _text((report or {}).get("datatype"))
            or _text(subject_raw.get("DATATYPE"))
        ),
    }
    board_name = (
        _text(subject_raw.get("BOARD_NAME"))
        or _text(subject_raw.get("PUBLISHNAME"))
    )
    board_code = _board_code(subject_raw)

    for item in normalized:
        item["revenue_ratio"] = _ratio(item.get("revenue"), subject.get("revenue"))
        item["market_cap_ratio"] = _ratio(
            item.get("market_cap"), subject.get("market_cap")
        )

    selected = _select_peers(normalized, subject, limit)
    leaders = _leaders(normalized, subject)

    stats = {
        metric: _stat(normalized, subject, metric)
        for metric in METRICS
    }
    notes = _notes(
        normalized,
        subject,
        selected=selected,
        notes=notes,
        valuation_rows=valuation_rows,
        valuation_date=valuation_date,
        valuation_stale=valuation_stale,
        valuation_error=valuation_error,
        board_name=board_name,
    )
    return {
        "available": True,
        "code": code,
        "as_of": period,
        "report": report_data,
        "industry": {
            "name": board_name,
            "code": board_code,
            "count": len(normalized),
        },
        "subject": subject,
        "stats": stats,
        "peers": selected,
        "leaders": leaders,
        "counts": {
            "industry": len(normalized),
            "valuation": sum(
                1 for item in normalized
                if item.get("pe_ttm") is not None
                or item.get("pb_mrq") is not None
                or item.get("market_cap") is not None
            ),
            "selected": len(selected),
            "leaders": len(leaders),
        },
        "notes": notes,
        "valuation_date": valuation_date,
    }


def fetch_peer_cohort(code: str) -> dict:
    """取本公司最新报告期的同行业数据；样本不足时回退上一期。"""
    code = normalize_stock_code(code)
    if not code:
        raise PeerError("请输入股票代码")

    subject_rows = _dedupe_periods(
        _request(
            f'(SECURITY_CODE="{code}")',
            sort="-1:REPORTDATE",
            page_size=40,
        )
    )
    subject_rows.sort(
        key=lambda row: _date(row.get("REPORTDATE")) or "",
        reverse=True,
    )
    if not subject_rows:
        raise PeerError(f"{code}：没有业绩报表数据")

    candidates: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for row in subject_rows:
        period = _date(row.get("REPORTDATE"))
        if not period or period in seen:
            continue
        seen.add(period)
        candidates.append((period, row))
        if len(candidates) >= MAX_FALLBACK_PERIODS:
            break

    if not candidates:
        raise PeerError(f"{code}：没有可用的报告期")

    first_row = candidates[0][1]
    board_code = _board_code(first_row)
    board_name = (
        _text(first_row.get("BOARD_NAME"))
        or _text(first_row.get("PUBLISHNAME"))
    )
    if not board_code:
        raise PeerError(f"{code}：没有行业分类，无法做同业对比")

    best: tuple[str, dict, list[dict], int] | None = None
    for index, (period, row) in enumerate(candidates):
        cohort = _dedupe(
            _request(
                f'(BOARD_CODE="{board_code}")(REPORTDATE=\'{period}\')',
                sort="-1:TOTAL_OPERATE_INCOME",
                page_size=500,
            )
        )
        cohort = [
            item for item in cohort
            if _text(item.get("SECURITY_TYPE")) == A_SHARE
        ]
        if not any(_text(item.get("SECURITY_CODE")) == code for item in cohort):
            cohort.append(row)

        if best is None or len(cohort) > len(best[2]):
            best = (period, row, cohort, index)
        if len(cohort) >= MIN_PEERS:
            best = (period, row, cohort, index)
            break

    if best is None:
        raise PeerError(f"{code}：没有取到同业数据")

    period, row, cohort, index = best
    notes: list[str] = []
    if index > 0:
        notes.append(
            f"公司最新报告期 {candidates[0][0]} 的同业样本不足 {MIN_PEERS} 家，"
            f"改用 {period}"
        )
    if len(cohort) < MIN_PEERS:
        notes.append(f"同业样本只有 {len(cohort)} 家，对比结果参考意义有限")

    return {
        "code": code,
        "board_code": board_code,
        "board_name": board_name,
        "report_date": period,
        "qdate": _text(row.get("QDATE")),
        "datatype": _text(row.get("DATATYPE")),
        "rows": cohort,
        "notes": notes,
    }


def ensure_peers(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取同业数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise PeerError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_peer_cohort(code)
    except PeerError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_peers(
    code: str,
    limit: int = DEFAULT_LIMIT,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并拼上估值、公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise PeerError("请输入股票代码")

    result = ensure_peers(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    cohort = result["data"]

    valuation_rows: list[dict] = []
    valuation_date = None
    valuation_stale = False
    valuation_error = None
    try:
        valuation = ensure_valuation(code)
        valuation_rows = valuation.get("peers") or []
        history = valuation.get("history") or []
        if history:
            valuation_date = history[-1].get("trade_date")
        valuation_stale = bool(valuation.get("stale"))
        valuation_error = valuation.get("peers_error")
    except ValuationError as error:
        # 估值是增强项，取不到只影响 PE/PB/市值，不影响财务对比
        valuation_error = str(error)

    report = {
        "period": cohort.get("report_date"),
        "qdate": cohort.get("qdate"),
        "datatype": cohort.get("datatype"),
    }
    data = peers_from_rows(
        cohort.get("rows") or [],
        code,
        limit=limit,
        valuation_rows=valuation_rows,
        report=report,
        notes=cohort.get("notes") or [],
        valuation_date=valuation_date,
        valuation_stale=valuation_stale,
        valuation_error=valuation_error,
    )

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    data.update({
        "name": name,
        "fetched_at": cohort.get("fetched_at") or "",
        "stale": result.get("stale", False),
        "sources": {SOURCE_LABEL: cohort.get("report_date")},
        "valuation_date": valuation_date,
        "valuation_stale": valuation_stale,
        "valuation_error": valuation_error,
        "url": page_url(cohort.get("report_date")),
    })
    data["summary"] = format_peers(data)
    return data


def page_url(period: str | None) -> str:
    """业绩报表页地址；报告期形如 2026-06-30。"""
    if not period:
        return ""
    return PAGE_URL.format(month=period[:7].replace("-", ""))


def format_peers(data: dict) -> str:
    """渲染成可读文本：本公司位置 + 规模相近公司 + 龙头。"""
    if not data.get("available"):
        return f"{data.get('name') or '该公司'}：暂无同业数据"

    report = data.get("report") or {}
    period = report.get("period") or data.get("as_of") or ""
    industry = data.get("industry") or {}
    title = (
        f"{data['name']}({data.get('code') or ''})"
        if data.get("name")
        else data.get("code") or "同业对比"
    )
    head = f"{title} · 同业对比 {period}"
    if industry.get("name"):
        head += f"（{industry['name']} {industry.get('count') or 0} 家）"
    if data.get("valuation_date"):
        head += f"，估值 {data['valuation_date']}"
    lines = [head]

    subject = data.get("subject") or {}
    stats = data.get("stats") or {}
    lines.append(_revenue_line(subject, stats.get("revenue") or {}))
    lines.append(_profit_line(subject, stats.get("net_profit") or {}))
    lines.append(_margin_line(subject, stats))
    valuation_line = _valuation_line(subject, stats)
    if valuation_line:
        lines.append(valuation_line)
    if report.get("qdate"):
        lines[0] += f" · {report['qdate']}"

    peers = data.get("peers") or []
    if peers:
        lines.append(
            "规模相近：" + "；".join(
                _peer_text(peer) for peer in peers[:SUMMARY_PEERS]
            )
        )
    # 龙头已经在“规模相近”里出现时不再重复，摘要留给出规模更大的龙头
    peer_codes = {item.get("code") for item in peers}
    leaders = [
        item for item in (data.get("leaders") or [])
        if item.get("code") not in peer_codes
    ]
    if leaders:
        lines.append(
            "营收龙头：" + "；".join(_peer_text(peer) for peer in leaders)
        )

    notes = data.get("notes") or []
    if notes:
        lines.append("说明：" + "；".join(notes))
    lines.append("")
    source = f"数据来源：{SOURCE_LABEL}"
    if data.get("valuation_date"):
        source += " + 估值分析"
    source += f"，报告期 {period}"
    if data.get("fetched_at"):
        source += f"，抓取于 {data['fetched_at']}"
    if data.get("stale"):
        source += "（本次抓取失败，使用本地缓存）"
    lines.append(source)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _normalize(raw: dict, valuations: dict[str, dict]) -> dict | None:
    code = _text(raw.get("SECURITY_CODE"))
    if not code:
        return None
    valuation = valuations.get(code) or {}
    revenue = _number(raw.get("TOTAL_OPERATE_INCOME"))
    net_profit = _number(raw.get("PARENT_NETPROFIT"))
    net_margin = None
    if revenue not in (None, 0) and net_profit is not None:
        net_margin = net_profit / revenue * 100.0
    return {
        "code": code,
        "name": (
            _text(raw.get("SECURITY_NAME_ABBR"))
            or _text(valuation.get("name"))
        ),
        "market": _text(raw.get("TRADE_MARKET")),
        "report_date": _date(raw.get("REPORTDATE")),
        "revenue": revenue,
        "net_profit": net_profit,
        "revenue_yoy": _number(raw.get("YSTZ")),
        "profit_yoy": _number(raw.get("SJLTZ")),
        "gross_margin": _number(raw.get("XSMLL")),
        "net_margin": net_margin,
        "roe": _number(raw.get("WEIGHTAVG_ROE")),
        "eps": _number(raw.get("BASIC_EPS")),
        "bps": _number(raw.get("BPS")),
        "ocf_per_share": _number(raw.get("MGJYXJJE")),
        "pe_ttm": _val_number(valuation, "pe_ttm", "PE_TTM"),
        "pb_mrq": _val_number(valuation, "pb_mrq", "PB_MRQ"),
        "market_cap": _val_number(valuation, "total_mv", "TOTAL_MARKET_CAP"),
    }


def _stat(normalized: list[dict], subject: dict, metric: str) -> dict:
    values = [
        item[metric] for item in normalized
        if _valid(metric, item.get(metric))
    ]
    own = subject.get(metric)
    own_valid = _valid(metric, own)
    return {
        "value": own,
        "median": statistics.median(values) if values else None,
        # rank 1 = 数值最高；percentile 是低于等于本公司的比例
        "rank": (
            1 + sum(1 for value in values if value > own)
            if own_valid else None
        ),
        "percentile": (
            sum(1 for value in values if value <= own) / len(values) * 100.0
            if own_valid and values else None
        ),
        "count": len(values),
    }


def _select_peers(
    normalized: list[dict],
    subject: dict,
    limit: int,
) -> list[dict]:
    candidates = [item for item in normalized if item["code"] != subject["code"]]
    if not candidates:
        return []

    own_revenue = subject.get("revenue")
    own_cap = subject.get("market_cap")
    if own_revenue and own_revenue > 0:
        def key(item: dict):
            ratio = item.get("revenue_ratio")
            if ratio is None:
                return (2, float("inf"), 0.0)
            return (
                0 if SIZE_BAND[0] <= ratio <= SIZE_BAND[1] else 1,
                abs(ratio - 1.0),
                -(item.get("revenue") or 0.0),
            )
    elif own_cap and own_cap > 0:
        def key(item: dict):
            ratio = item.get("market_cap_ratio")
            if ratio is None:
                return (2, float("inf"), 0.0)
            return (
                0 if SIZE_BAND[0] <= ratio <= SIZE_BAND[1] else 1,
                abs(ratio - 1.0),
                -(item.get("revenue") or 0.0),
            )
    else:
        def key(item: dict):
            return (0, 0.0, -(item.get("revenue") or 0.0))

    selected = sorted(candidates, key=key)[:limit]
    selected.sort(
        key=lambda item: (
            item.get("revenue") if item.get("revenue") is not None
            else float("-inf")
        ),
        reverse=True,
    )
    return selected


def _leaders(normalized: list[dict], subject: dict, count: int = 3) -> list[dict]:
    others = [item for item in normalized if item["code"] != subject["code"]]
    others.sort(
        key=lambda item: (
            item.get("revenue") if item.get("revenue") is not None
            else float("-inf")
        ),
        reverse=True,
    )
    return others[:count]


def _notes(
    normalized: list[dict],
    subject: dict,
    selected: list[dict],
    notes: Iterable[str],
    valuation_rows: Iterable[dict],
    valuation_date: str | None,
    valuation_stale: bool,
    valuation_error: str | None,
    board_name: str | None,
) -> list[str]:
    result: list[str] = []
    for item in notes:
        text = str(item).strip() if item is not None else ""
        if text:
            result.append(text)
    has_valuation = bool(valuation_rows)

    if selected and subject.get("revenue"):
        in_band = sum(
            1 for item in selected
            if item.get("revenue_ratio") is not None
            and SIZE_BAND[0] <= item["revenue_ratio"] <= SIZE_BAND[1]
        )
        if in_band < len(selected):
            result.append(
                f"营收在 {SIZE_BAND[0]:g}–{SIZE_BAND[1]:g} 倍范围内的公司"
                f"只有 {in_band} 家，其余按最接近的规模补齐"
            )
    result.append("同行按东方财富行业板块筛选，细分业务可能不完全可比")

    if valuation_error:
        if has_valuation:
            result.append(
                f"估值同业抓取失败，使用本地缓存：{valuation_error}"
            )
        else:
            result.append(f"估值数据抓取失败：{valuation_error}")
    elif not has_valuation:
        result.append("没有取到估值数据，PE/PB/市值为空")
    elif valuation_stale:
        result.append("估值数据来自本地缓存，可能不是最新收盘")

    period = subject.get("report_date")
    if valuation_date and period and valuation_date != period:
        result.append(
            f"估值日期 {valuation_date} 与财报期 {period} 不同，"
            "PE/PB 是最新市场数据"
        )

    if has_valuation:
        missing = sum(
            1 for item in normalized
            if item.get("pe_ttm") is None
            and item.get("pb_mrq") is None
            and item.get("market_cap") is None
        )
        if missing:
            result.append(f"{missing} 家同业缺少估值数据")

    if any(
        item.get("pe_ttm") is not None and item["pe_ttm"] <= 0
        for item in normalized
    ):
        result.append("PE 为负的公司处于亏损，不参与 PE 排名")
    if subject.get("pe_ttm") is not None and subject["pe_ttm"] <= 0:
        result.append("本公司 PE 为负，PE 排名不适用")

    if board_name and any(
        word in board_name for word in ("银行", "保险", "证券")
    ):
        result.append("金融行业毛利率、净利率口径不可比，重点看 ROE、PB 与资产质量")
    return list(dict.fromkeys(result))


def _valuation_index(rows: Iterable[dict]) -> dict[str, dict]:
    if isinstance(rows, dict):
        rows = rows.values()
    result = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = _text(row.get("code")) or _text(row.get("SECURITY_CODE"))
        if code:
            result[code] = row
    return result


def _dedupe(rows: Iterable[dict]) -> list[dict]:
    """同一代码只保留更新时间最新的一行，防止报表修订造成重复。"""
    best: dict[str, tuple[str, dict]] = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = _text(raw.get("SECURITY_CODE"))
        if not code:
            continue
        stamp = (
            _text(raw.get("UPDATE_DATE"))
            or _text(raw.get("NOTICE_DATE"))
            or ""
        )
        current = best.get(code)
        if current is None or stamp >= current[0]:
            best[code] = (stamp, raw)
    return [item[1] for item in best.values()]


def _dedupe_periods(rows: Iterable[dict]) -> list[dict]:
    """同一代码 + 报告期只保留更新时间最新的一行。

    本公司的多期报表不能按代码去重，否则只剩最新一期，回退逻辑就失效了。
    """
    best: dict[tuple[str, str], tuple[str, dict]] = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = _text(raw.get("SECURITY_CODE"))
        period = _date(raw.get("REPORTDATE"))
        if not code or not period:
            continue
        stamp = (
            _text(raw.get("UPDATE_DATE"))
            or _text(raw.get("NOTICE_DATE"))
            or ""
        )
        key = (code, period)
        current = best.get(key)
        if current is None or stamp >= current[0]:
            best[key] = (stamp, raw)
    return [item[1] for item in best.values()]


def _valid(metric: str, value) -> bool:
    if value is None:
        return False
    if metric in POSITIVE_ONLY:
        return value > 0
    return True


def _ratio(value, base) -> float | None:
    if value is None or base is None or base == 0:
        return None
    return value / base


def _board_code(row: dict) -> str | None:
    board = _text(row.get("BOARD_CODE"))
    if board:
        return board
    original = _text(row.get("ORI_BOARD_CODE"))
    if not original:
        return None
    return original if original.startswith("BK") else f"BK{original}"


# ---------------------------------------------------------------------------
# 取数与缓存
# ---------------------------------------------------------------------------
def _request(filter_expr: str, sort: str, page_size: int) -> list[dict]:
    sort_type, sort_column = sort.split(":", 1)
    rows: list[dict] = []
    page = 1
    while page <= 5:
        params = {
            "reportName": REPORT_NAME,
            "columns": "ALL",
            "quoteColumns": "",
            "filter": filter_expr,
            "pageNumber": str(page),
            "pageSize": str(page_size),
            "sortTypes": sort_type,
            "sortColumns": sort_column,
            "source": "WEB",
            "client": "WEB",
        }
        try:
            response = requests.get(
                ENDPOINT,
                params=params,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
                # 本机系统代理会拦掉东方财富的接口，这里和估值请求一样直连
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise PeerError(f"获取同业数据失败：{error}") from error

        if not isinstance(payload, dict):
            raise PeerError("同业接口返回格式异常")
        if not payload.get("success"):
            raise PeerError(
                f"同业接口返回错误：{payload.get('message') or '未知错误'}"
            )
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        data = result.get("data") or []
        if not isinstance(data, list):
            data = []
        rows.extend(item for item in data if isinstance(item, dict))
        try:
            pages = int(result.get("pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages or not data:
            break
        page += 1
    return rows


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
# 渲染小工具
# ---------------------------------------------------------------------------
def _revenue_line(subject: dict, stat: dict) -> str:
    text = f"营收 {_yi(subject.get('revenue'))}"
    if stat.get("median") is not None:
        text += f"（行业中位 {_yi(stat['median'])}"
        if stat.get("rank") is not None:
            text += f"，由高到低第 {stat['rank']}/{stat['count']}"
        text += "）"
    if subject.get("revenue_yoy") is not None:
        text += f"，同比 {_signed_pct(subject['revenue_yoy'])}"
    return text


def _profit_line(subject: dict, stat: dict) -> str:
    text = f"净利 {_yi(subject.get('net_profit'))}"
    if stat.get("median") is not None:
        text += f"（行业中位 {_yi(stat['median'])}"
        if stat.get("rank") is not None:
            text += f"，由高到低第 {stat['rank']}/{stat['count']}"
        text += "）"
    if subject.get("profit_yoy") is not None:
        text += f"，同比 {_signed_pct(subject['profit_yoy'])}"
    return text


def _margin_line(subject: dict, stats: dict) -> str:
    parts = []
    for key, label in (
        ("gross_margin", "毛利率"),
        ("net_margin", "净利率"),
        ("roe", "ROE"),
    ):
        text = f"{label} {_pct(subject.get(key))}"
        stat = stats.get(key) or {}
        if stat.get("median") is not None:
            text += f"（中位 {_pct(stat['median'])}"
            if stat.get("rank") is not None:
                text += f"，由高到低第 {stat['rank']}/{stat['count']}"
            text += "）"
        parts.append(text)
    return " · ".join(parts)


def _valuation_line(subject: dict, stats: dict) -> str:
    parts = []
    for key, label in (("pe_ttm", "PE"), ("pb_mrq", "PB")):
        text = f"{label} {_pe(subject.get(key))}"
        stat = stats.get(key) or {}
        if stat.get("median") is not None:
            text += f"（中位 {_pe(stat['median'])}"
            if stat.get("rank") is not None:
                text += f"，由高到低第 {stat['rank']}/{stat['count']}"
            text += "）"
        parts.append(text)
    if subject.get("market_cap") is not None:
        parts.append(f"市值 {_yi(subject['market_cap'])}")
    if not any(
        subject.get(key) is not None
        for key in ("pe_ttm", "pb_mrq", "market_cap")
    ):
        return ""
    return " · ".join(parts)


def _peer_text(peer: dict) -> str:
    parts = [f"{peer.get('name') or peer.get('code')} {_yi(peer.get('revenue'))}"]
    if peer.get("revenue_yoy") is not None:
        parts.append(f"同比 {_signed_pct(peer['revenue_yoy'])}")
    if peer.get("gross_margin") is not None:
        parts.append(f"毛利率 {_pct(peer['gross_margin'])}")
    if peer.get("roe") is not None:
        parts.append(f"ROE {_pct(peer['roe'])}")
    parts.append(f"PE {_pe(peer.get('pe_ttm'))}")
    return "，".join(parts)


def _yi(value) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.1f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f}万"
    return f"{value:.0f}"


def _pct(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _signed_pct(value) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _pe(value) -> str:
    if value is None:
        return "—"
    return "亏损" if value <= 0 else f"{value:.1f}"


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


def _val_number(raw: dict, *keys: str) -> float | None:
    for key in keys:
        if key in raw:
            return _number(raw.get(key))
    return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
