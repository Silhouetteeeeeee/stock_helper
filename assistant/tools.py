"""研究工具：把数据层包装成模型能调用的函数。

每个工具声明一份 OpenAI function schema，执行结果统一为 ``ToolResult``。
失败不抛异常——``ok=False`` 和错误文案本身就是给模型的观察结果，
让它换一条路或如实说明资料缺口。

所有取数工具都接受可选的 ``company``：省略时研究会话默认公司，
传了就用指定公司，因此同一轮里可以对照另一家。
"""

from __future__ import annotations

import copy

from stock.announcements import build_announcements
from stock.db_service import get_all_stocks, get_stock, get_stock_by_name
from stock.finance_snapshot import build_snapshot
from stock.segments import ensure_segments, segments_from_rows
from stock.utils import get_stock_prefix
from stock.valuation import build_valuation

from .company_profile import build_profile
from .models import Source, ToolResult

_COMPANY = {
    "type": "string",
    "description": "股票代码或简称；省略则用当前公司",
}

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "resolve_company",
            "description": "把公司简称或代码解析成六位代码；重名或找不到时返回候选",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_code": {
                        "type": "string",
                        "description": "公司简称或六位股票代码",
                    },
                },
                "required": ["name_or_code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_profile",
            "description": "公司概况：主营描述、行业分类、员工数、经营范围摘要",
            "parameters": {
                "type": "object",
                "properties": {"company": _COMPANY},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_finance",
            "description": "财务快照：营收/净利及同比、毛利率、净利率、ROE、负债率、现金流、单季趋势",
            "parameters": {
                "type": "object",
                "properties": {"company": _COMPANY},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_segments",
            "description": "主营构成：按产品和按地区两组切片，含收入、占比、同比、毛利率",
            "parameters": {
                "type": "object",
                "properties": {"company": _COMPANY},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_announcements",
            "description": "近期公告：带标签的事件（回购、减持、重大合同等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "回看天数，1-365，默认 180",
                    },
                    "company": _COMPANY,
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_valuation",
            "description": "估值：PE/PB 及历史分位、同行业对比；本版只陈述事实，不做估值结论",
            "parameters": {
                "type": "object",
                "properties": {"company": _COMPANY},
                "additionalProperties": False,
            },
        },
    },
]


def schemas() -> list[dict]:
    """返回工具 schema 的副本，避免模型适配层误改全局常量。"""
    return copy.deepcopy(TOOL_SCHEMAS)


# ---------------------------------------------------------------------------
# 公司解析
# ---------------------------------------------------------------------------
def resolve_code(
    query,
    default,
) -> tuple[str | None, str | None, list[dict]]:
    """把输入解析成六位代码，返回 ``(code, error, candidates)``。

    ``query`` 为空时用 ``default``；两者都为空、代码无法识别、或简称
    找不到时返回错误，简称找不到还会带上模糊匹配的候选。
    代码支持 ``605499``、``SH605499``、``605499.SH`` 三种写法。
    """
    query, error = _company_text(query)
    if error:
        return None, error, []
    default, error = _company_text(default)
    if error:
        return None, error, []
    if not query and not default:
        return None, "未指定公司：请先给出股票代码或简称", []

    value = query or default
    upper = value.upper()
    prefix, digits = get_stock_prefix(upper)
    # 只有整体就是代码的写法才按代码处理，避免简称里恰好有六位数字
    if len(digits) == 6 and upper in (digits, f"{prefix}{digits}", f"{digits}.{prefix}"):
        if prefix not in ("SH", "SZ", "BJ"):
            return None, f"无法识别股票代码「{value}」", []
        return digits, None, []

    stock = get_stock_by_name(value)
    if stock is not None:
        return stock.code, None, []

    candidates = [
        {"code": item.code, "name": item.name}
        for item in get_all_stocks()
        if value in (item.name or "")
    ][:5]
    if candidates:
        names = "、".join(f"{item['name']}({item['code']})" for item in candidates)
        return None, f"没有找到「{value}」，你是不是指：{names}", candidates
    return None, f"没有找到「{value}」", []


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------
def run_tool(
    name: str,
    arguments: dict,
    default_company: str | None = None,
) -> ToolResult:
    """执行一个工具；任何失败都折成 ``ToolResult(ok=False)``，从不抛异常。"""
    code = None
    try:
        if not isinstance(arguments, dict):
            arguments = {}
        if name == "resolve_company":
            return _resolve_company_tool(arguments)

        code, error, _ = resolve_code(arguments.get("company"), default_company)
        if code is None:
            return ToolResult(ok=False, error=error or "无法识别公司")

        if name == "get_company_profile":
            return _company_profile(code)
        if name == "get_finance":
            return _finance(code)
        if name == "get_segments":
            return _segments(code)
        if name == "get_announcements":
            return _announcements(code, arguments)
        if name == "get_valuation":
            return _valuation(code)
        return ToolResult(ok=False, company=code, error=f"未知工具：{name}")
    except Exception as error:  # noqa: BLE001 - 数据层异常统一交给模型处理
        return ToolResult(ok=False, company=code, error=str(error))


def _resolve_company_tool(arguments: dict) -> ToolResult:
    code, error, candidates = resolve_code(arguments.get("name_or_code"), None)
    if code is None:
        return ToolResult(ok=False, error=error)
    stock = get_stock(code)
    return ToolResult(
        ok=True,
        company=code,
        data={
            "code": code,
            "name": stock.name if stock is not None else None,
            "candidates": candidates,
        },
    )


def _company_profile(code: str) -> ToolResult:
    profile = build_profile(code)
    if not profile.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：没有公司概况")

    as_of = profile.get("as_of") or ""
    source = Source(
        id=f"profile:{code}",
        label="公司概况",
        detail=f"{profile.get('name')} · {profile.get('industry')}",
        as_of=as_of,
        fetched_at=profile.get("fetched_at") or "",
    )
    data = {
        "name": profile.get("name"),
        "industry": profile.get("industry"),
        "profile": profile.get("profile"),
        "scope": profile.get("scope"),
        "employees": profile.get("employees"),
        "as_of": as_of,
    }
    return ToolResult(ok=True, company=code, as_of=as_of, sources=(source,), data=data)


def _finance(code: str) -> ToolResult:
    snapshot = build_snapshot(code)
    if not snapshot.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：没有可用的财报数据")

    latest = snapshot["latest"]
    period = latest["report_date"]
    metrics = snapshot.get("metrics") or {}
    revenue = metrics.get("revenue") or {}
    profit = metrics.get("net_profit") or {}
    cash = metrics.get("cash") or {}
    trend_rows = snapshot.get("trend") or []
    latest_trend = trend_rows[-1] if trend_rows else {}

    data = {
        "period": period,
        "revenue_yi": _yi(revenue.get("value")),
        "revenue_yoy": _pct(revenue.get("yoy")),
        "revenue_sq_yi": _yi(latest_trend.get("revenue_sq")),
        "revenue_sq_yoy": _pct(latest_trend.get("revenue_sq_yoy")),
        "net_profit_yi": _yi(profit.get("value")),
        "net_profit_yoy": _pct(profit.get("yoy")),
        "gross_margin": _pct(_metric(metrics, "gross_margin")),
        "net_margin": _pct(_metric(metrics, "net_margin")),
        "roe": _pct(_metric(metrics, "roe")),
        "debt_ratio": _pct(_metric(metrics, "debt_ratio")),
        "ocf_yi": _yi(cash.get("operating_cash_flow")),
        "free_cash_flow_yi": _yi(cash.get("free_cash_flow")),
        "ocf_to_net_profit": _round(cash.get("ocf_to_net_profit"), 3),
        "net_debt_yi": _yi(cash.get("net_debt")),
        "cash_yi": _yi(cash.get("cash_balance")),
        "trend": [
            {
                "period": row.get("report_date"),
                "revenue_sq_yi": _yi(row.get("revenue_sq")),
                "revenue_sq_yoy": _pct(row.get("revenue_sq_yoy")),
                "gross_margin": _pct(row.get("gross_margin")),
            }
            for row in trend_rows
        ],
    }
    name = snapshot.get("name")
    source = Source(
        id=f"finance:{code}:{period}",
        label="财报",
        detail=f"{name} {period} 财务快照",
        as_of=period,
        fetched_at=latest.get("fetched_at") or "",
    )
    return ToolResult(ok=True, company=code, as_of=period, sources=(source,), data=data)


def _segments(code: str) -> ToolResult:
    data = ensure_segments(code)
    rows = data.get("rows") or []
    if not rows:
        return ToolResult(ok=False, company=code, error=f"{code}：没有主营构成数据")

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    fetched_at = max((row.get("fetched_at") or "" for row in rows), default="")
    slices: list[dict] = []
    sources: list[Source] = []
    for kind, key in (("2", "product"), ("3", "region")):
        piece = segments_from_rows(
            rows, kind=kind, code=code, name=name, stale=data.get("stale", False)
        )
        # segments_from_rows 在目标口径缺失时会回退到别的口径；
        # 回退出来的产品数据不能再冒充地区切片
        if not piece.get("available") or piece.get("kind") != kind:
            continue
        as_of = piece.get("as_of") or ""
        label = piece.get("kind_label") or ("产品" if key == "product" else "地区")
        slices.append({
            "kind": key,
            "label": label,
            "total_income_yi": _yi(piece.get("total_income")),
            "items": [
                {
                    "name": item.get("name"),
                    "income_yi": _yi(item.get("income")),
                    "share": _pct(item.get("share")),
                    "yoy": _pct(item.get("yoy")),
                    "gross_margin": _pct(item.get("gross_margin")),
                }
                for item in piece.get("items") or []
            ],
        })
        sources.append(Source(
            id=f"segments:{code}:{as_of}:{key}",
            label=f"主营构成·{label}",
            detail=f"{name} {as_of} 按{label}",
            as_of=as_of,
            fetched_at=fetched_at,
        ))

    if not slices:
        return ToolResult(ok=False, company=code, error=f"{code}：没有可用的主营构成")
    as_of = sources[0].as_of
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of,
        sources=tuple(sources),
        data={"code": code, "name": name, "as_of": as_of, "slices": slices},
    )


def _announcements(code: str, arguments: dict) -> ToolResult:
    try:
        days = int(arguments.get("days") or 180)
    except (TypeError, ValueError):
        days = 180
    days = min(max(days, 1), 365)

    data = build_announcements(code, days=days)
    if not data.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：近 {days} 天没有公告")

    items: list[dict] = []
    sources: list[Source] = []
    for item in (data.get("items") or [])[:20]:
        items.append({
            "date": item.get("date"),
            "title": item.get("title"),
            "tags": item.get("tags") or [],
            "level": item.get("level"),
        })
        art_code = item.get("art_code") or "-"
        sources.append(Source(
            id=f"announcement:{code}:{item.get('date')}:{art_code}",
            label="公告",
            detail=_clip(item.get("title") or "", 60),
            as_of=item.get("date") or "",
            # 公告摘要没有带出抓取时间，引用里留空，不假装是刚抓的
            fetched_at="",
            url=item.get("url") or "",
        ))

    as_of = data.get("as_of")
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of,
        sources=tuple(sources),
        data={
            "code": code,
            "name": data.get("name"),
            "days": days,
            "as_of": as_of,
            "items": items,
        },
    )


def _valuation(code: str) -> ToolResult:
    data = build_valuation(code)
    if not data.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：没有估值数据")

    valuation = data.get("valuation") or {}
    pe = valuation.get("pe_ttm") or {}
    pb = valuation.get("pb_mrq") or {}
    industry = data.get("industry") or {}
    price = data.get("price") or {}
    as_of = data.get("as_of") or ""
    name = data.get("name")
    source = Source(
        id=f"valuation:{code}:{as_of}",
        label="估值",
        detail=f"{name} {as_of} 估值",
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
    )
    payload = {
        "pe_ttm": pe.get("value"),
        # valuation 的分位本来就是 0~100，不能再乘 100
        "pe_percentile": _pct(pe.get("percentile")),
        "pb_mrq": pb.get("value"),
        "pb_percentile": _pct(pb.get("percentile")),
        "years": pe.get("years") or pb.get("years"),
        "price": price.get("close"),
        "market_cap_yi": _yi(price.get("market_cap")),
        "industry": {
            "board": industry.get("board"),
            "count": industry.get("count"),
            "median_pe": industry.get("median_pe"),
            "median_pb": industry.get("median_pb"),
            "rank_pe": industry.get("rank_pe"),
            "rank_total": industry.get("rank_total"),
        },
        "peers": [
            {
                "name": peer.get("name"),
                "pe_ttm": peer.get("pe_ttm"),
                "pb_mrq": peer.get("pb_mrq"),
            }
            for peer in (industry.get("peers") or [])[:5]
        ],
    }
    return ToolResult(ok=True, company=code, as_of=as_of, sources=(source,), data=payload)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _metric(metrics: dict, key: str):
    item = metrics.get(key) or {}
    return item.get("value") if isinstance(item, dict) else None


def _round(value, digits: int):
    return None if value is None else round(value, digits)


def _yi(value):
    """元 -> 亿元，保留两位。"""
    return None if value is None else round(value / 1e8, 2)


def _pct(value):
    """百分比保留一位。"""
    return None if value is None else round(value, 1)


def _company_text(value) -> tuple[str, str | None]:
    """把模型给的 ``company`` 规范成字符串；返回 ``(text, error)``。"""
    if value is None:
        return "", None
    if isinstance(value, str):
        return value.strip(), None
    if type(value) is int:  # bool 不是股票代码
        return str(value), None
    return "", f"company 参数必须是字符串（收到 {type(value).__name__}）"


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，、；： ") + "…"
