"""研究工具：把数据层包装成模型能调用的函数。

每个工具声明一份 OpenAI function schema，执行结果统一为 ``ToolResult``。
失败不抛异常——``ok=False`` 和错误文案本身就是给模型的观察结果，
让它换一条路或如实说明资料缺口。

所有取数工具都接受可选的 ``company``：省略时研究会话默认公司，
传了就用指定公司，因此同一轮里可以对照另一家。
"""

from __future__ import annotations

import copy
import math

from stock.announcements import build_announcements
from stock.buybacks import build_buybacks
from stock.db_service import get_all_stocks, get_stock, get_stock_by_name
from stock.dividends import PAGE_URL as DIVIDEND_URL
from stock.dividends import build_dividends
from stock.finance_history import build_history
from stock.finance_service import ensure_financials
from stock.finance_snapshot import (
    BALANCE_DETAIL_FIELDS,
    CASHFLOW_DETAIL_FIELDS,
    INCOME_DETAIL_FIELDS,
    build_snapshot,
)
from stock.insider_trades import PAGE_URL as INSIDER_URL
from stock.insider_trades import build_insider_trades
from stock.management import PAGE_URL as MANAGEMENT_URL
from stock.management import build_management
from stock.peers import build_peers
from stock.segments import ensure_segments, segments_from_rows
from stock.shareholders import PAGE_URL as SHAREHOLDER_URL
from stock.shareholders import build_shareholders
from stock.utils import get_stock_prefix
from stock.valuation import build_valuation

from .company_profile import build_profile
from .models import Source, ToolResult
from .skills import catalog, get_skill
from .valuation_math import (
    ValuationInputError,
    compare,
    fcf_yield,
    scenario,
    terminal_pe,
)

_COMPANY = {
    "type": "string",
    "description": "股票代码或简称；省略则用当前公司",
}

_SKILL_NAMES = [item["name"] for item in catalog()]
_SKILL_HINT = "；".join(
    f"{item['name']}（{item['title']}）" for item in catalog()
)

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "加载研究 skill 的完整判据与回答结构。可用："
                f"{_SKILL_HINT}。宏观问题可一次加载多个。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": _SKILL_NAMES,
                        "description": "要加载的 skill 名称",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
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
            "description": (
                "财务快照：营收/净利及同比、毛利率、净利率、ROE、负债率、现金流、"
                "有息负债、利息保障、商誉、应收/存货同比；另含每股收益、归母净资产、"
                "固定资产/在建工程、应付/合同负债、投资收益与减值、研发与资本开支、"
                "单季趋势，以及 balance_detail / income_detail / cashflow_detail "
                "三张表的完整科目（金额均为亿元）"
            ),
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
            "name": "get_financial_history",
            "description": "年度财务序列：5–10 年的营收、净利、每股收益、毛利率、净利率、费用率、ROE、现金流、有息负债、固定资产/在建工程与负债率，用来看盈利能力能否持续",
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {
                        "type": "integer",
                        "description": "回看年数，1-10，默认 10",
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
            "name": "get_segments",
            "description": (
                "主营构成：按产品和按地区两组切片，含收入、占比、同比、毛利率，"
                "以及最近若干期同口径的分部历史，用来看业务结构变化"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {
                        "type": "integer",
                        "description": "分部历史回看期数，1-10，默认 5",
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
            "name": "get_management",
            "description": (
                "管理层与治理：董事会/高管/监事会/审计委员会成员，含职务、任期、"
                "年龄、学历、薪酬、持股与简历，以及各组薪酬与持股合计"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_chars": {
                        "type": "integer",
                        "description": "每人简历截断字数，0-2000，默认 300；0 表示不返回简历",
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
            "name": "get_shareholders",
            "description": (
                "股东结构：实控人、十大股东、十大流通股东（比例为占流通股比例），"
                "以及最近若干期股东户数与户均持股变化，用来看筹码集中还是分散"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "periods": {
                        "type": "integer",
                        "description": "股东户数回看期数，1-10，默认 8",
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
            "name": "get_peers",
            "description": (
                "同业对比：同行业、同报告期的营收/净利/增速/毛利率/净利率/ROE/PE/PB，"
                "含行业中位数、本公司排名与分位、规模相近公司与营收龙头；"
                "样本不足时回退上一报告期。rank=1 表示数值最高（PE/PB 即估值最贵）"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "规模相近的同业数量，3-20，默认 8",
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
            "name": "get_dividends",
            "description": (
                "分红历史：按报告期列现金分红（税前每 10 股）、送股/转增、"
                "实施进度与除权除息日；按年度汇总现金分红总额、归母净利润与"
                "分红率，并给出连续分红年数。分红总额按报告期总股本估算，"
                "分红率按报告期归年"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {
                        "type": "integer",
                        "description": "回看年数，1-20，默认 10",
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
            "name": "get_insider_trades",
            "description": (
                "高管增减持：列变动人、与高管的关系、职务、变动股数、"
                "成交均价、金额、变动后持股与原因；按年汇总净增持/净减持，"
                "按人汇总净额。持股计划单列，只含已披露记录"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {
                        "type": "integer",
                        "description": "回看年数，1-10，默认 3",
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
            "name": "get_buybacks",
            "description": (
                "股票回购：按计划列公告日、进度、计划金额/股数/价格区间、"
                "已回购金额/股数与完成度，并区分注销、员工持股/股权激励；"
                "按公告年度汇总。只含已披露回购计划"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {
                        "type": "integer",
                        "description": "回看年数，1-10，默认 5",
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
            "description": "估值：PE/PB 及历史分位、同行业对比；工具只提供事实数据，估值判断交给 skill 的情景分析",
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
            "name": "calc_valuation",
            "description": (
                "估值计算与校验：数字由代码用 Decimal 计算，假设原样回显。"
                "check 用财报 TTM 反推 PE/PB 并与市值口径校验；"
                "scenario 按明示假设算悲观/中性/乐观三档每股价值；"
                "terminal 检查永续增长终值 PE 的假设是否自洽。"
                "情景结果是假设的算术结果，不是目标价。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": _COMPANY,
                    "mode": {
                        "type": "string",
                        "enum": ["check", "scenario", "terminal"],
                        "description": "check=口径校验；scenario=三情景；terminal=终值 PE",
                    },
                    "price": {
                        "type": "number",
                        "description": "当前股价（元/股）；scenario 省略时用估值数据",
                    },
                    "base_value": {
                        "type": "number",
                        "description": "scenario 的基准每股价值，与 base_label 对应",
                    },
                    "base_label": {
                        "type": "string",
                        "enum": ["EPS", "FCF"],
                        "description": "基准值口径：EPS=每股收益，FCF=每股自由现金流；默认 EPS",
                    },
                    "growth_pct": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "悲观/中性/乐观三档年化增速（%）",
                    },
                    "exit_pe": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "悲观/中性/乐观三档退出 PE",
                    },
                    "years": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "情景年数，默认 3",
                    },
                    "roic_pct": {
                        "type": "number",
                        "description": "terminal：永续期 ROIC（%）",
                    },
                    "terminal_g_pct": {
                        "type": "number",
                        "description": "terminal：永续增速（%）",
                    },
                    "discount_rate_pct": {
                        "type": "number",
                        "description": "terminal：折现率（%）",
                    },
                },
                "required": ["mode"],
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
        # skill 是研究方法的说明，不属于任何一家公司，必须先于公司解析
        if name == "load_skill":
            return _load_skill_tool(arguments)
        if name == "resolve_company":
            return _resolve_company_tool(arguments)

        code, error, _ = resolve_code(arguments.get("company"), default_company)
        if code is None:
            return ToolResult(ok=False, error=error or "无法识别公司")

        if name == "get_company_profile":
            return _company_profile(code)
        if name == "get_finance":
            return _finance(code)
        if name == "get_financial_history":
            return _financial_history(code, arguments)
        if name == "get_segments":
            return _segments(code, arguments)
        if name == "get_management":
            return _management(code, arguments)
        if name == "get_shareholders":
            return _shareholders(code, arguments)
        if name == "get_peers":
            return _peers(code, arguments)
        if name == "get_dividends":
            return _dividends(code, arguments)
        if name == "get_insider_trades":
            return _insider_trades(code, arguments)
        if name == "get_buybacks":
            return _buybacks(code, arguments)
        if name == "get_announcements":
            return _announcements(code, arguments)
        if name == "get_valuation":
            return _valuation(code)
        if name == "calc_valuation":
            return _calc_valuation(code, arguments)
        return ToolResult(ok=False, company=code, error=f"未知工具：{name}")
    except Exception as error:  # noqa: BLE001 - 数据层异常统一交给模型处理
        return ToolResult(ok=False, company=code, error=str(error))


def _load_skill_tool(arguments: dict) -> ToolResult:
    requested = arguments.get("name")
    skill = get_skill(requested)
    if skill is None:
        available = "、".join(item["name"] for item in catalog())
        return ToolResult(
            ok=False, error=f"未知 skill：{requested}；可用：{available}"
        )
    return ToolResult(
        ok=True,
        data={
            "name": skill.name,
            "title": skill.title,
            "instructions": skill.instructions,
        },
    )


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
    safety = metrics.get("safety") or {}
    detail = metrics.get("detail") or {}
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
        # 财务安全：有息负债、利息保障、商誉与营运资金占用
        "interest_debt_yi": _yi(safety.get("interest_debt")),
        "short_term_debt_yi": _yi(safety.get("short_term_debt")),
        "interest_coverage": _round(safety.get("interest_coverage"), 1),
        "interest_coverage_basis": safety.get("interest_coverage_basis"),
        "goodwill_yi": _yi(safety.get("goodwill")),
        "goodwill_ratio": _pct(safety.get("goodwill_ratio")),
        "accounts_receivable_yi": _yi(safety.get("accounts_receivable")),
        "accounts_receivable_yoy": _pct(safety.get("accounts_receivable_yoy")),
        "inventory_yi": _yi(safety.get("inventory")),
        "inventory_yoy": _pct(safety.get("inventory_yoy")),
        "cash_to_short_term_debt": _round(safety.get("cash_to_short_term_debt"), 2),
        # 报表明细：每股收益、权益、产能投入与营运资金科目
        "basic_eps": _round(detail.get("basic_eps"), 3),
        "diluted_eps": _round(detail.get("diluted_eps"), 3),
        "equity_parent_yi": _yi(detail.get("equity_parent")),
        "minority_interest_yi": _yi(detail.get("minority_interest")),
        "total_equity_yi": _yi(detail.get("total_equity")),
        "total_assets_yi": _yi(detail.get("total_assets")),
        "total_liabilities_yi": _yi(detail.get("total_liabilities")),
        "fixed_assets_yi": _yi(detail.get("fixed_assets")),
        "fixed_assets_yoy": _pct(detail.get("fixed_assets_yoy")),
        "construction_in_progress_yi": _yi(detail.get("construction_in_progress")),
        "construction_in_progress_yoy": _pct(
            detail.get("construction_in_progress_yoy")
        ),
        "intangible_assets_yi": _yi(detail.get("intangible_assets")),
        "long_term_equity_invest_yi": _yi(detail.get("long_term_equity_invest")),
        "notes_payable_yi": _yi(detail.get("notes_payable")),
        "accounts_payable_yi": _yi(detail.get("accounts_payable")),
        "advances_from_customers_yi": _yi(detail.get("advances_from_customers")),
        "contract_liabilities_yi": _yi(detail.get("contract_liabilities")),
        "investment_income_yi": _yi(detail.get("investment_income")),
        "fair_value_change_yi": _yi(detail.get("fair_value_change")),
        "asset_impairment_yi": _yi(detail.get("asset_impairment")),
        "credit_impairment_yi": _yi(detail.get("credit_impairment")),
        "treasury_stock_yi": _yi(detail.get("treasury_stock")),
        "rd_expense_yi": _yi(detail.get("rd_expense")),
        "capex_yi": _yi(detail.get("capex")),
        # 营运资金科目同比，和营收增速一起看
        "notes_receivable_yoy": _pct(detail.get("notes_receivable_yoy")),
        "prepayments_yoy": _pct(detail.get("prepayments_yoy")),
        "other_receivables_yoy": _pct(detail.get("other_receivables_yoy")),
        "accounts_payable_yoy": _pct(detail.get("accounts_payable_yoy")),
        "contract_liabilities_yoy": _pct(detail.get("contract_liabilities_yoy")),
        "development_expenditure_yoy": _pct(
            detail.get("development_expenditure_yoy")
        ),
        # 三张表的完整科目，字段名统一带 _yi；按报表分组避免和摘要字段混淆
        "balance_detail": _money_detail(
            detail.get("balance"), BALANCE_DETAIL_FIELDS
        ),
        "income_detail": _money_detail(
            detail.get("income"), INCOME_DETAIL_FIELDS
        ),
        "cashflow_detail": _money_detail(
            detail.get("cashflow"), CASHFLOW_DETAIL_FIELDS
        ),
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


def _financial_history(code: str, arguments: dict) -> ToolResult:
    try:
        years = int(arguments.get("years") or 10)
    except (TypeError, ValueError):
        years = 10
    years = min(max(years, 1), 10)

    data = build_history(code, years=years)
    if not data.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：没有可用的年报序列")

    span = data.get("span") or ""
    as_of = data.get("as_of") or ""
    name = data.get("name")
    source = Source(
        id=f"finance_history:{code}:{span}",
        label="财报·年度序列",
        detail=f"{name or code} {span} 年度财务",
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "span": span,
            "as_of": as_of,
            "years": data.get("years") or [],
            "notes": data.get("notes") or [],
        },
    )


def _segments(code: str, arguments: dict) -> ToolResult:
    try:
        years = int(arguments.get("years") or 5)
    except (TypeError, ValueError):
        years = 5
    years = min(max(years, 1), 10)

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
            rows,
            kind=kind,
            code=code,
            name=name,
            stale=data.get("stale", False),
            years=years,
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
            "history": [
                {
                    "period": period.get("period"),
                    "total_income_yi": _yi(period.get("total_income")),
                    "items": [
                        {
                            "name": item.get("name"),
                            "income_yi": _yi(item.get("income")),
                            "share": _pct(item.get("share")),
                            "yoy": _pct(item.get("yoy")),
                            "gross_margin": _pct(item.get("gross_margin")),
                        }
                        for item in period.get("items") or []
                    ],
                }
                for period in piece.get("history") or []
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


def _management(code: str, arguments: dict) -> ToolResult:
    try:
        resume_chars = int(arguments.get("resume_chars", 300))
    except (TypeError, ValueError):
        resume_chars = 300
    resume_chars = min(max(resume_chars, 0), 2000)

    data = build_management(code)
    if not data.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：没有管理层数据")

    groups: list[dict] = []
    for key, group in (data.get("groups") or {}).items():
        members = []
        for member in group.get("members") or []:
            item = {
                "name": member.get("name"),
                "duties": member.get("duties") or [],
                "tenure": member.get("tenure"),
                "age": member.get("age"),
                "education": member.get("education"),
                "salary_wan": _wan(member.get("salary")),
                "hold_wan": _wan(member.get("hold")),
                "hold_ratio": _pct(member.get("hold_ratio")),
                "direct_ratio": _pct(member.get("direct_ratio")),
                "indirect_ratio": _pct(member.get("indirect_ratio")),
                "is_control": member.get("is_control"),
                "is_legal_person": member.get("is_legal_person"),
                "resume": (
                    (_clip(member.get("resume"), resume_chars) or None)
                    if resume_chars else None
                ),
            }
            if member.get("change"):
                item["change"] = member["change"]
            members.append(item)
        groups.append({
            "key": key,
            "label": group.get("label"),
            "count": group.get("count"),
            "salary_total_wan": _wan(group.get("salary_total")),
            "hold_ratio": _pct(group.get("hold_ratio")),
            "direct_ratio": _pct(group.get("direct_ratio")),
            "indirect_ratio": _pct(group.get("indirect_ratio")),
            "members": members,
        })
    if not groups:
        return ToolResult(ok=False, company=code, error=f"{code}：没有管理层数据")

    name = data.get("name")
    as_of = data.get("as_of") or ""
    source = Source(
        id=f"management:{code}",
        label="管理层与持股",
        detail=f"{name or code} {as_of}".strip(),
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
        url=MANAGEMENT_URL.format(code=code),
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "as_of": as_of,
            "salary_year": data.get("salary_year"),
            "count": data.get("count"),
            "stale": data.get("stale", False),
            "groups": groups,
        },
    )


def _shareholders(code: str, arguments: dict) -> ToolResult:
    try:
        periods = int(arguments.get("periods", 8))
    except (TypeError, ValueError):
        periods = 8
    periods = min(max(periods, 1), 10)

    data = build_shareholders(code, periods=periods)
    top10 = [_holder_row(item) for item in data.get("top10") or []]
    free_top10 = [
        _holder_row(item, free_float=True)
        for item in data.get("free_float_top10") or []
    ]
    if not data.get("available") or (not top10 and not free_top10):
        return ToolResult(ok=False, company=code, error=f"{code}：没有股东结构数据")

    holder_count = data.get("holder_count") or {}
    history = [
        _holder_count_row(item)
        for item in holder_count.get("history") or []
    ]
    controller = data.get("controller") or {}

    name = data.get("name")
    as_of = data.get("as_of") or ""
    prefix, digits = get_stock_prefix(code)
    source = Source(
        id=f"shareholders:{code}:{as_of}" if as_of else f"shareholders:{code}",
        label="股东结构",
        detail=f"{name or code} {as_of}".strip(),
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
        url=SHAREHOLDER_URL.format(code=f"{prefix}{digits}"),
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "as_of": as_of,
            "stale": data.get("stale", False),
            "controller": (
                {
                    "name": controller.get("name"),
                    "ratio": _pct(controller.get("ratio")),
                }
                if controller.get("name") else None
            ),
            "top10": top10,
            "free_float_top10": free_top10,
            "holder_count": {
                "latest": history[-1] if history else None,
                "history": history,
            },
            "counts": {
                "top10": len(top10),
                "free_float_top10": len(free_top10),
                "holder_periods": len(history),
            },
        },
    )


def _peers(code: str, arguments: dict) -> ToolResult:
    try:
        limit = int(arguments.get("limit", 8))
    except (TypeError, ValueError):
        limit = 8
    limit = min(max(limit, 3), 20)

    data = build_peers(code, limit=limit)
    peers = [_peer_row(item) for item in data.get("peers") or []]
    leaders = [_peer_row(item) for item in data.get("leaders") or []]
    if not data.get("available") or (not peers and not leaders):
        return ToolResult(ok=False, company=code, error=f"{code}：没有同业对比数据")

    industry = data.get("industry") or {}
    report = data.get("report") or {}
    as_of = data.get("as_of") or ""
    name = data.get("name")
    source = Source(
        id=f"peers:{code}:{as_of}" if as_of else f"peers:{code}",
        label="同业对比",
        detail=(
            f"{name or code} {as_of} · "
            f"{industry.get('name') or '同行业'} {industry.get('count') or 0} 家"
        ).strip(),
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
        url=data.get("url") or "",
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "as_of": as_of,
            "industry": industry,
            "report": report,
            "valuation_date": data.get("valuation_date"),
            "valuation_stale": data.get("valuation_stale", False),
            "valuation_error": data.get("valuation_error"),
            "counts": data.get("counts") or {},
            "subject": _peer_row(data.get("subject") or {}),
            "stats": {
                metric: _peer_stat(metric, stat)
                for metric, stat in (data.get("stats") or {}).items()
            },
            "peers": peers,
            "leaders": leaders,
            "notes": data.get("notes") or [],
            "stale": data.get("stale", False),
        },
    )


def _dividends(code: str, arguments: dict) -> ToolResult:
    try:
        years = int(arguments.get("years", 10))
    except (TypeError, ValueError):
        years = 10
    years = min(max(years, 1), 20)

    data = build_dividends(code, years=years)
    events = [_dividend_event(item) for item in data.get("events") or []]
    annual = [_dividend_year(item) for item in data.get("annual") or []]
    latest = _dividend_event(data["latest"]) if data.get("latest") else None

    name = data.get("name")
    as_of = data.get("as_of") or ""
    source = Source(
        id=f"dividends:{code}:{as_of}" if as_of else f"dividends:{code}",
        label="分红历史",
        detail=f"{name or code} 分红 {as_of}".strip(),
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
        url=DIVIDEND_URL.format(code=code),
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of or None,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "as_of": as_of or None,
            "has_dividends": data.get("has_dividends", False),
            "latest": latest,
            "annual": annual,
            "events": events,
            "counts": data.get("counts") or {},
            "notes": data.get("notes") or [],
            "stale": data.get("stale", False),
        },
    )


def _insider_trades(code: str, arguments: dict) -> ToolResult:
    try:
        years = int(arguments.get("years", 3))
    except (TypeError, ValueError):
        years = 3
    years = min(max(years, 1), 10)

    data = build_insider_trades(code, years=years)
    events = [_insider_event(item) for item in data.get("events") or []]
    annual = [_insider_year(item) for item in data.get("annual") or []]
    people = [_insider_person(item) for item in data.get("people") or []]
    latest = _insider_event(data["latest"]) if data.get("latest") else None

    name = data.get("name")
    as_of = data.get("as_of") or ""
    source = Source(
        id=f"insider:{code}:{as_of}" if as_of else f"insider:{code}",
        label="高管增减持",
        detail=f"{name or code} 高管增减持 {as_of}".strip(),
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
        url=INSIDER_URL.format(code=code),
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of or None,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "as_of": as_of or None,
            "window_start": data.get("window_start"),
            "window_end": data.get("window_end"),
            "years": data.get("years", years),
            "has_trades": data.get("has_trades", False),
            "latest": latest,
            "annual": annual,
            "people": people,
            "events": events,
            "events_truncated": data.get("events_truncated", 0),
            "counts": _insider_counts(data.get("counts") or {}),
            "notes": data.get("notes") or [],
            "stale": data.get("stale", False),
        },
    )


def _buybacks(code: str, arguments: dict) -> ToolResult:
    try:
        years = int(arguments.get("years", 5))
    except (TypeError, ValueError):
        years = 5
    years = min(max(years, 1), 10)

    data = build_buybacks(code, years=years)
    plans = [_buyback_plan(item) for item in data.get("plans") or []]
    annual = [_buyback_year(item) for item in data.get("annual") or []]
    latest = _buyback_plan(data["latest"]) if data.get("latest") else None

    name = data.get("name")
    as_of = data.get("as_of") or ""
    source = Source(
        id=f"buybacks:{code}:{as_of}" if as_of else f"buybacks:{code}",
        label="股票回购",
        detail=f"{name or code} 股票回购 {as_of}".strip(),
        as_of=as_of,
        fetched_at=data.get("fetched_at") or "",
        url=data.get("url") or "",
    )
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of or None,
        sources=(source,),
        data={
            "code": code,
            "name": name,
            "as_of": as_of or None,
            "window_start": data.get("window_start"),
            "window_end": data.get("window_end"),
            "years": data.get("years", years),
            "has_buybacks": data.get("has_buybacks", False),
            "latest": latest,
            "annual": annual,
            "plans": plans,
            "plans_truncated": data.get("plans_truncated", 0),
            "counts": _buyback_counts(data.get("counts") or {}),
            "notes": data.get("notes") or [],
            "stale": data.get("stale", False),
        },
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
# 估值计算与校验
# ---------------------------------------------------------------------------
def _calc_valuation(code: str, arguments: dict) -> ToolResult:
    mode = arguments.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        return ToolResult(
            ok=False, company=code,
            error="calc_valuation 需要 mode：check / scenario / terminal",
        )
    mode = mode.strip().lower()
    if mode == "check":
        return _valuation_check(code)
    if mode == "scenario":
        return _valuation_scenario(code, arguments)
    if mode == "terminal":
        return _valuation_terminal(code, arguments)
    return ToolResult(
        ok=False, company=code,
        error=f"未知 mode：{mode}；可用 check / scenario / terminal",
    )


def _valuation_check(code: str) -> ToolResult:
    """用财报 TTM 反推 PE/PB，再与东财估值口径比对。"""
    valuation = build_valuation(code)
    if not valuation.get("available"):
        return ToolResult(ok=False, company=code, error=f"{code}：没有估值数据，无法校验")

    valuation_data = valuation.get("valuation") or {}
    price_data = valuation.get("price") or {}
    reported_pe = (valuation_data.get("pe_ttm") or {}).get("value")
    reported_pb = (valuation_data.get("pb_mrq") or {}).get("value")
    price = price_data.get("close")
    market_cap = price_data.get("market_cap")
    as_of = valuation.get("as_of") or ""
    if market_cap is None:
        return ToolResult(ok=False, company=code, error=f"{code}：估值数据缺少市值，无法校验")

    rows = sorted(ensure_financials(code), key=lambda row: row["report_date"])
    if not rows:
        return ToolResult(ok=False, company=code, error=f"{code}：没有可用的财报数据")
    latest = rows[-1]
    period = latest["report_date"]
    financial = latest.get("cost_of_revenue") is None

    ttm_profit = _ttm(rows, ("net_profit_attributable", "net_profit"))
    ttm_ocf = _ttm(rows, ("operating_cash_flow",))
    ttm_capex = _ttm(rows, ("capex",))
    ttm_fcf = (
        ttm_ocf - ttm_capex
        if ttm_ocf is not None and ttm_capex is not None
        else None
    )
    equity = _field(latest, ("equity_parent", "total_equity"))

    pe_implied = market_cap / ttm_profit if ttm_profit and ttm_profit > 0 else None
    pb_implied = market_cap / equity if equity and equity > 0 else None
    pe_check = compare("PE(TTM)", reported_pe, pe_implied)
    pb_check = compare("PB(MRQ)", reported_pb, pb_implied)
    yield_value = None if financial else fcf_yield(ttm_fcf, market_cap)

    warnings: list[str] = []
    for check in (pe_check, pb_check):
        if check["status"] == "fail":
            warnings.append(
                f"{check['name']} 校验差异 {check['diff_pct']:+.2f}%，"
                "可能是指标口径或财报未同步，引用前先核对"
            )
        elif check["status"] == "warn":
            warnings.append(
                f"{check['name']} 校验差异 {check['diff_pct']:+.2f}%，"
                "注意 TTM/MRQ 口径"
            )
    if ttm_profit is not None and ttm_profit <= 0:
        warnings.append("TTM 归母净利润为负，PE 不适用")
    if financial:
        warnings.append("金融机构：自由现金流收益率口径不可比，未计算")
    elif ttm_fcf is None:
        warnings.append("缺少经营现金流或资本开支，未计算自由现金流收益率")
    minority = latest.get("minority_interest")
    if minority:
        warnings.append(f"PB 按归母净资产校验；少数股东权益 {_yi(minority)} 未计入")

    data = {
        "mode": "check",
        "price": _round(price, 2),
        "market_cap_yi": _yi(market_cap),
        "valuation": {
            "pe_ttm": reported_pe,
            "pb_mrq": reported_pb,
            "as_of": as_of,
        },
        "financials": {
            "period": period,
            "ttm_net_profit_yi": _yi(ttm_profit),
            "equity_parent_yi": _yi(equity),
            "ttm_fcf_yi": _yi(ttm_fcf),
            "is_financial": financial,
        },
        "checks": [pe_check, pb_check],
        "fcf_yield_pct": None if yield_value is None else round(float(yield_value), 2),
        "warnings": warnings,
        "notes": [
            "PE/PB 用东财估值与新浪财报两套数据交叉校验；"
            "差异可能来自 TTM/MRQ 口径或财报同步时点，不代表数据错误。"
        ],
    }

    sources: list[Source] = []
    name = valuation.get("name") or code
    if as_of:
        sources.append(Source(
            id=f"valuation:{code}:{as_of}",
            label="估值",
            detail=f"{name} {as_of} 估值",
            as_of=as_of,
            fetched_at=valuation.get("fetched_at") or "",
        ))
    sources.append(Source(
        id=f"finance:{code}:{period}",
        label="财报",
        detail=f"{name} {period} 财务快照",
        as_of=period,
        fetched_at=latest.get("fetched_at") or "",
    ))
    return ToolResult(
        ok=True,
        company=code,
        as_of=as_of or period,
        sources=tuple(sources),
        data=data,
    )


def _valuation_scenario(code: str, arguments: dict) -> ToolResult:
    price = _as_number(arguments.get("price"))
    as_of = None
    sources: list[Source] = []
    warnings: list[str] = []
    if price is None:
        valuation = build_valuation(code)
        if valuation.get("available"):
            price = _as_number((valuation.get("price") or {}).get("close"))
            as_of = valuation.get("as_of") or None
            if price is not None and as_of:
                sources.append(Source(
                    id=f"valuation:{code}:{as_of}",
                    label="估值",
                    detail=f"{valuation.get('name') or code} {as_of} 估值",
                    as_of=as_of,
                    fetched_at=valuation.get("fetched_at") or "",
                ))
        if price is None:
            warnings.append("没有当前股价，未计算相对当前价的空间")
    elif price <= 0:
        return ToolResult(ok=False, company=code, error="price 必须大于 0")

    base_value = _as_number(arguments.get("base_value"))
    if base_value is None:
        return ToolResult(
            ok=False, company=code,
            error="scenario 需要 base_value（每股基准价值）",
        )
    base_label = arguments.get("base_label") or "EPS"
    base_label = base_label.strip() if isinstance(base_label, str) else "EPS"
    if not base_label:
        base_label = "EPS"

    growths = _as_numbers(arguments.get("growth_pct"))
    if growths is None:
        return ToolResult(
            ok=False, company=code,
            error="growth_pct 需要 3 个数字（悲观/中性/乐观）",
        )
    pes = _as_numbers(arguments.get("exit_pe"))
    if pes is None:
        return ToolResult(
            ok=False, company=code,
            error="exit_pe 需要 3 个数字（悲观/中性/乐观）",
        )

    years_raw = arguments.get("years")
    if years_raw is None:
        years = 3
    else:
        try:
            years = int(years_raw)
        except (TypeError, ValueError):
            return ToolResult(ok=False, company=code, error="years 必须是整数")
        if years < 1 or years > 10:
            return ToolResult(
                ok=False, company=code, error="years 需要在 1–10 之间"
            )

    labels = ("悲观", "中性", "乐观")
    scenarios: list[dict] = []
    for label, growth, pe in zip(labels, growths, pes):
        try:
            result = scenario(base_value, growth, pe, years, price)
        except ValuationInputError as error:
            return ToolResult(ok=False, company=code, error=str(error))
        scenarios.append({
            "label": label,
            "growth_pct": growth,
            "exit_pe": pe,
            "future_base_per_share": _num(result["future_value"], 2),
            "value_per_share": _num(result["future_price"], 2),
            "upside_pct": _num(result["upside_pct"], 1),
        })

    if any(pe > 50 for pe in pes):
        warnings.append("有退出 PE 高于 50 倍，确认假设依据，不要把它当成合理估值")
    if any(growth > 30 for growth in growths):
        warnings.append("有增速假设高于 30%，确认不是把历史增速线性外推")

    data = {
        "mode": "scenario",
        "assumptions": {
            "base_value": base_value,
            "base_label": base_label,
            "years": years,
            "growth_pct": growths,
            "exit_pe": pes,
            "price": price,
        },
        "scenarios": scenarios,
        "warnings": warnings,
        "notes": [
            "情景结果是明示假设下的算术结果，不是目标价，也不是买卖建议。",
            "基准值与退出 PE 由调用方给出，工具不替你选择参数。",
        ],
    }
    return ToolResult(
        ok=True, company=code, as_of=as_of, sources=tuple(sources), data=data
    )


def _valuation_terminal(code: str, arguments: dict) -> ToolResult:
    inputs: dict[str, float] = {}
    for key in ("roic_pct", "terminal_g_pct", "discount_rate_pct"):
        value = _as_number(arguments.get(key))
        if value is None:
            return ToolResult(
                ok=False, company=code, error=f"terminal 需要 {key}"
            )
        inputs[key] = value

    try:
        result = terminal_pe(
            inputs["roic_pct"],
            inputs["terminal_g_pct"],
            inputs["discount_rate_pct"],
        )
    except ValuationInputError as error:
        return ToolResult(ok=False, company=code, error=str(error))

    data = {
        "mode": "terminal",
        "inputs": inputs,
        "terminal_pe": _num(result["terminal_pe"], 1),
        "payout_ratio_pct": _num(result["payout_ratio_pct"], 1),
        "valid": result["valid"],
        "status": result["status"],
        "checks": result["checks"],
        "warnings": [
            check["message"]
            for check in result["checks"]
            if check["status"] in ("warn", "fail")
        ],
        "notes": ["终值 PE 只用于检查永续增长假设是否自洽，不是目标价。"],
    }
    return ToolResult(ok=True, company=code, data=data)


def _ttm(rows: list[dict], fields: tuple[str, ...]):
    """最新报告期的 TTM 值：本期累计 + 上年年报 − 上年同期累计。

    最新一期就是年报时，后两项相消，TTM 等于年报值。
    """
    latest = rows[-1]
    period = latest["report_date"]
    year = int(period[:4])
    by_date = {row["report_date"]: row for row in rows}
    previous_year_end = by_date.get(f"{year - 1}-12-31")
    same_last_year = by_date.get(f"{year - 1}{period[4:]}")

    current = _field(latest, fields)
    annual = _field(previous_year_end, fields)
    same_quarter = _field(same_last_year, fields)
    if current is None or annual is None or same_quarter is None:
        return None
    return current + annual - same_quarter


def _field(row: dict | None, fields: tuple[str, ...]):
    if row is None:
        return None
    for name in fields:
        value = row.get(name)
        if value is not None:
            return value
    return None


def _as_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_numbers(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    numbers = [_as_number(item) for item in value]
    if any(item is None for item in numbers):
        return None
    return numbers  # type: ignore[return-value]


def _num(value, digits: int):
    return None if value is None else round(float(value), digits)


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


def _money_detail(values, fields) -> dict:
    """把一组元单位的报表科目统一换算成 ``*_yi``。"""
    values = values or {}
    return {f"{field}_yi": _yi(values.get(field)) for field in fields}


def _pct(value):
    """百分比保留一位；加 0.0 把 -0.0 归一成 0.0。"""
    return None if value is None else round(value, 1) + 0.0


def _wan(value):
    """元 -> 万元，保留两位；薪酬和持股数用这个口径比"亿"更易读。"""
    return None if value is None else round(value / 1e4, 2)


def _buyback_plan(item: dict) -> dict:
    """回购计划一行：金额换亿元，股数换万股，价格与比例保留两位。"""
    item = item or {}
    return {
        "plan_id": item.get("plan_id"),
        "announce_date": item.get("announce_date"),
        "year": item.get("year"),
        "status": item.get("status"),
        "progress_code": item.get("progress_code"),
        "is_active": item.get("is_active", False),
        "pending": item.get("pending", False),
        "completed": item.get("completed", False),
        "stopped": item.get("stopped", False),
        "start_date": item.get("start_date"),
        "end_date": item.get("end_date"),
        "update_date": item.get("update_date"),
        "finish_date": item.get("finish_date"),
        "advanced_date": item.get("advanced_date"),
        "planned_amount_low_yi": _yi(item.get("planned_amount_low")),
        "planned_amount_high_yi": _yi(item.get("planned_amount_high")),
        "planned_shares_low_wan": _wan(item.get("planned_shares_low")),
        "planned_shares_high_wan": _wan(item.get("planned_shares_high")),
        "planned_price_low": _round(item.get("planned_price_low"), 2),
        "planned_price_high": _round(item.get("planned_price_high"), 2),
        "planned_ratio_low_pct": _round(item.get("planned_ratio_low_pct"), 2),
        "planned_ratio_high_pct": _round(item.get("planned_ratio_high_pct"), 2),
        "actual_amount_yi": _yi(item.get("actual_amount")),
        "actual_shares_wan": _wan(item.get("actual_shares")),
        "actual_price_low": _round(item.get("actual_price_low"), 2),
        "actual_price_high": _round(item.get("actual_price_high"), 2),
        "actual_ratio_total_pct": _round(
            item.get("actual_ratio_total_pct"), 2
        ),
        "actual_ratio_float_pct": _round(
            item.get("actual_ratio_float_pct"), 2
        ),
        "amount_completion_pct": _round(
            item.get("amount_completion_pct"), 1
        ),
        "shares_completion_pct": _round(
            item.get("shares_completion_pct"), 1
        ),
        "purpose": item.get("purpose"),
        "purpose_short": item.get("purpose_short"),
        "purpose_tags": item.get("purpose_tags") or [],
        "cancel": item.get("cancel", False),
        "incentive": item.get("incentive", False),
    }


def _buyback_year(item: dict) -> dict:
    """年度回购汇总：金额换亿元，股数换万股。"""
    item = item or {}
    return {
        "year": item.get("year"),
        "plans": item.get("plans"),
        "active": item.get("active"),
        "pending": item.get("pending"),
        "completed": item.get("completed"),
        "stopped": item.get("stopped"),
        "actual_amount_yi": _yi(item.get("actual_amount")),
        "actual_shares_wan": _wan(item.get("actual_shares")),
        "planned_amount_low_yi": _yi(item.get("planned_amount_low")),
        "planned_amount_high_yi": _yi(item.get("planned_amount_high")),
        "completion_pct": _round(item.get("completion_pct"), 1),
        "cancel_plans": item.get("cancel_plans"),
        "incentive_plans": item.get("incentive_plans"),
        "amount_complete": item.get("amount_complete", True),
    }


def _buyback_counts(item: dict) -> dict:
    """回购总览：金额换亿元，股数换万股。"""
    item = item or {}
    return {
        "plans": item.get("plans", 0),
        "active": item.get("active", 0),
        "pending": item.get("pending", 0),
        "completed": item.get("completed", 0),
        "stopped": item.get("stopped", 0),
        "cancel_plans": item.get("cancel_plans", 0),
        "incentive_plans": item.get("incentive_plans", 0),
        "actual_amount_yi": _yi(item.get("actual_amount")),
        "actual_shares_wan": _wan(item.get("actual_shares")),
        "planned_amount_low_yi": _yi(item.get("planned_amount_low")),
        "planned_amount_high_yi": _yi(item.get("planned_amount_high")),
        "completion_pct": _round(item.get("completion_pct"), 1),
        "missing_actual": item.get("missing_actual", 0),
        "amount_complete": item.get("amount_complete", True),
        "planned_amount_complete": item.get(
            "planned_amount_complete", True
        ),
        "latest_update": item.get("latest_update"),
    }


def _holder_row(item: dict, free_float: bool = False) -> dict:
    """十大股东的一行：股数换成万股，比例保留一位。"""
    row = {
        "rank": item.get("rank"),
        "name": item.get("name"),
        "share_type": item.get("share_type"),
        "shares_wan": _wan(item.get("shares")),
        "ratio": _pct(item.get("ratio")),
        "change_wan": _wan(item.get("change")),
        "change_text": item.get("change_text"),
        "change_ratio": _pct(item.get("change_ratio")),
    }
    if free_float:
        row["holder_type"] = item.get("holder_type")
    return row


def _holder_count_row(item: dict) -> dict:
    """股东户数的一期：户数和户均持股保持原单位。"""
    avg_free_shares = item.get("avg_free_shares")
    return {
        "period": item.get("period"),
        "holders": item.get("holders"),
        "holders_change_pct": _pct(item.get("holders_change_pct")),
        "avg_free_shares": (
            None if avg_free_shares is None else int(round(avg_free_shares))
        ),
        "avg_free_shares_change_pct": _pct(
            item.get("avg_free_shares_change_pct")
        ),
        "focus": item.get("focus"),
        "top10_ratio": _pct(item.get("top10_ratio")),
        "free_float_top10_ratio": _pct(item.get("free_float_top10_ratio")),
    }


def _dividend_event(item: dict) -> dict:
    """分红方案一行：每 10 股口径保留两位，金额换亿元。"""
    item = item or {}
    return {
        "report_date": item.get("report_date"),
        "fiscal_year": item.get("fiscal_year"),
        "period_label": item.get("period_label"),
        "plan": item.get("plan"),
        "plan_short": item.get("plan_short"),
        "status": item.get("status"),
        "cash_per_10": _round(item.get("cash_per_10"), 2),
        "cash_per_share": _round(item.get("cash_per_share"), 3),
        "cash_total_yi": _yi(item.get("cash_total")),
        "bonus_per_10": _round(item.get("bonus_per_10"), 2),
        "transfer_per_10": _round(item.get("transfer_per_10"), 2),
        "share_bonus_per_10": _round(item.get("share_bonus_per_10"), 2),
        "total_shares_wan": _wan(item.get("total_shares")),
        "yield_pct": _round(item.get("yield_pct"), 2),
        "eps": _round(item.get("eps"), 2),
        "plan_notice_date": item.get("plan_notice_date"),
        "notice_date": item.get("notice_date"),
        "record_date": item.get("record_date"),
        "ex_date": item.get("ex_date"),
    }


def _dividend_year(item: dict) -> dict:
    """年度分红汇总：分红率保留一位，金额换亿元。"""
    item = item or {}
    return {
        "year": item.get("year"),
        "periods": item.get("periods") or [],
        "report_count": item.get("report_count"),
        "cash_total_yi": _yi(item.get("cash_total")),
        "cash_complete": item.get("cash_complete", True),
        "net_profit_yi": _yi(item.get("net_profit")),
        "payout_ratio": _pct(item.get("payout_ratio")),
        "payout_basis": item.get("payout_basis"),
        "share_bonus_count": item.get("share_bonus_count"),
    }


def _insider_event(item: dict) -> dict:
    """增减持明细一行：股数保持原单位，金额换万元，比例保留四位。"""
    item = item or {}
    return {
        "change_date": item.get("change_date"),
        "year": item.get("year"),
        "person": item.get("person"),
        "executive": item.get("executive"),
        "position": item.get("position"),
        "relation": item.get("relation"),
        "kind": item.get("kind"),
        "direction": item.get("direction"),
        "shares": item.get("shares"),
        "average_price": _round(item.get("average_price"), 2),
        "amount_wan": _wan(item.get("amount")),
        "amount_estimated": item.get("amount_estimated", False),
        "reason": item.get("reason"),
        "change_ratio_pct": _round(item.get("change_ratio_pct"), 4),
        "hold_after": item.get("hold_after"),
        "begin_hold": item.get("begin_hold"),
        "end_hold": item.get("end_hold"),
        "hold_type": item.get("hold_type"),
    }


def _insider_year(item: dict) -> dict:
    """年度增减持汇总：股数保持原单位，金额换万元。"""
    item = item or {}
    return {
        "year": item.get("year"),
        "events": item.get("events"),
        "buy_events": item.get("buy_events"),
        "sell_events": item.get("sell_events"),
        "buy_shares": item.get("buy_shares"),
        "sell_shares": item.get("sell_shares"),
        "net_shares": item.get("net_shares"),
        "buy_amount_wan": _wan(item.get("buy_amount")),
        "sell_amount_wan": _wan(item.get("sell_amount")),
        "net_amount_wan": _wan(item.get("net_amount")),
        "missing_amount_events": item.get("missing_amount_events"),
        "amount_complete": item.get("amount_complete", True),
    }


def _insider_person(item: dict) -> dict:
    """个人增减持汇总：保留关系与最新持股，金额换万元。"""
    item = item or {}
    return {
        "person": item.get("person"),
        "executive": item.get("executive"),
        "position": item.get("position"),
        "relation": item.get("relation"),
        "kind": item.get("kind"),
        "events": item.get("events"),
        "buy_shares": item.get("buy_shares"),
        "sell_shares": item.get("sell_shares"),
        "net_shares": item.get("net_shares"),
        "buy_amount_wan": _wan(item.get("buy_amount")),
        "sell_amount_wan": _wan(item.get("sell_amount")),
        "net_amount_wan": _wan(item.get("net_amount")),
        "missing_amount_events": item.get("missing_amount_events"),
        "amount_complete": item.get("amount_complete", True),
        "first_date": item.get("first_date"),
        "latest_date": item.get("latest_date"),
        "latest_hold": item.get("latest_hold"),
        "latest_price": _round(item.get("latest_price"), 2),
    }


def _insider_counts(item: dict) -> dict:
    """增减持总览：股数保持原单位，金额换万元。"""
    item = item or {}
    return {
        "events": item.get("events", 0),
        "buy_events": item.get("buy_events", 0),
        "sell_events": item.get("sell_events", 0),
        "people": item.get("people", 0),
        "insiders": item.get("insiders", 0),
        "plans": item.get("plans", 0),
        "plan_events": item.get("plan_events", 0),
        "buy_shares": item.get("buy_shares", 0),
        "sell_shares": item.get("sell_shares", 0),
        "net_shares": item.get("net_shares", 0),
        "buy_amount_wan": _wan(item.get("buy_amount")),
        "sell_amount_wan": _wan(item.get("sell_amount")),
        "net_amount_wan": _wan(item.get("net_amount")),
        "missing_amount_events": item.get("missing_amount_events", 0),
        "estimated_amount_events": item.get("estimated_amount_events", 0),
        "latest_date": item.get("latest_date"),
    }


_PEER_AMOUNT_METRICS = {"revenue", "net_profit", "market_cap"}
_PEER_RATIO_METRICS = {
    "revenue_yoy", "profit_yoy", "gross_margin", "net_margin", "roe",
}


def _peer_row(item: dict) -> dict:
    """同业一行：金额换亿元，比例保留一位，倍数保留两位。"""
    item = item or {}
    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "market": item.get("market"),
        "revenue_yi": _yi(item.get("revenue")),
        "net_profit_yi": _yi(item.get("net_profit")),
        "revenue_yoy": _pct(item.get("revenue_yoy")),
        "profit_yoy": _pct(item.get("profit_yoy")),
        "gross_margin": _pct(item.get("gross_margin")),
        "net_margin": _pct(item.get("net_margin")),
        "roe": _pct(item.get("roe")),
        "eps": _round(item.get("eps"), 2),
        "bps": _round(item.get("bps"), 2),
        "ocf_per_share": _round(item.get("ocf_per_share"), 2),
        "pe_ttm": _round(item.get("pe_ttm"), 2),
        "pb_mrq": _round(item.get("pb_mrq"), 2),
        "market_cap_yi": _yi(item.get("market_cap")),
        "revenue_ratio": _round(item.get("revenue_ratio"), 2),
        "market_cap_ratio": _round(item.get("market_cap_ratio"), 2),
    }


def _peer_stat(metric: str, stat: dict) -> dict:
    """统计值按指标口径换算；rank=1 表示数值最高。"""
    stat = stat or {}
    return {
        "value": _peer_metric(metric, stat.get("value")),
        "median": _peer_metric(metric, stat.get("median")),
        "rank": stat.get("rank"),
        "percentile": _pct(stat.get("percentile")),
        "count": stat.get("count"),
    }


def _peer_metric(metric: str, value):
    if value is None:
        return None
    if metric in _PEER_AMOUNT_METRICS:
        return _yi(value)
    if metric in _PEER_RATIO_METRICS:
        return _pct(value)
    return _round(value, 2)


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
