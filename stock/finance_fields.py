"""三大报表字段定义：把数据源的中文列名映射为稳定的英文字段名。

数据源（``akshare.stock_financial_report_sina``）的列名是中文，而且会随
会计准则和数据源改版变化，例如"预收款项"逐渐被"合同负债"取代。这里维护
一层别名映射，入库只写英文字段，查询和指标计算不再关心来源列名。

每个字段的别名按优先级排列：先匹配到的非空值生效，因此新准则字段放在
旧准则字段前面，可以同时兼容不同年份的报表。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatementSpec:
    """一张报表的入库定义。

    ``required`` 是每家公司都必须存在的核心字段。银行、保险的报表结构与
    工商企业不同（例如银行没有"流动资产合计"），因此不能要求全部字段
    都在，只校验这几项，缺失的可选字段按 None 入库。
    """

    key: str
    table: str
    symbol: str                         # 数据源接口使用的报表名
    fields: dict[str, tuple[str, ...]]  # 英文字段 -> 数据源列名候选
    required: tuple[str, ...]           # 必须存在的字段


BALANCE_FIELDS: dict[str, tuple[str, ...]] = {
    # 银行以"现金及存放中央银行款项"代替"货币资金"
    "cash": ("货币资金", "现金及存放中央银行款项"),
    "trading_fin_assets": ("交易性金融资产",),
    "notes_receivable": ("应收票据",),
    "accounts_receivable": ("应收账款",),
    "receivables_financing": ("应收款项融资",),
    "prepayments": ("预付款项",),
    "other_receivables": ("其他应收款(合计)", "其他应收款"),
    "inventory": ("存货",),
    "current_assets_total": ("流动资产合计",),
    "long_term_equity_invest": ("长期股权投资",),
    "investment_property": ("投资性房地产",),
    "fixed_assets": ("固定资产净额", "固定资产净值"),
    "construction_in_progress": ("在建工程合计", "在建工程"),
    "right_of_use_assets": ("使用权资产",),
    "intangible_assets": ("无形资产",),
    "development_expenditure": ("开发支出",),
    "goodwill": ("商誉",),
    "deferred_tax_assets": ("递延所得税资产",),
    "other_non_current_assets": ("其他非流动资产",),
    "non_current_assets_total": ("非流动资产合计",),
    "total_assets": ("资产总计",),
    "short_term_borrowings": ("短期借款",),
    "notes_payable": ("应付票据",),
    "accounts_payable": ("应付账款",),
    "advances_from_customers": ("预收款项",),
    "contract_liabilities": ("合同负债",),
    "employee_benefits_payable": ("应付职工薪酬",),
    "taxes_payable": ("应交税费",),
    "other_payables": ("其他应付款合计", "其他应付款"),
    "current_liabilities_total": ("流动负债合计",),
    "long_term_borrowings": ("长期借款",),
    "bonds_payable": ("应付债券",),
    "lease_liabilities": ("租赁负债",),
    "current_portion_lt_debt": ("一年内到期的非流动负债",),
    "non_current_liabilities_total": ("非流动负债合计",),
    "total_liabilities": ("负债合计",),
    "paid_in_capital": ("实收资本(或股本)", "股本"),
    "capital_reserve": ("资本公积",),
    "treasury_stock": ("减:库存股",),
    "surplus_reserve": ("盈余公积",),
    "retained_earnings": ("未分配利润",),
    "equity_parent": ("归属于母公司股东权益合计", "归属于母公司股东的权益"),
    "minority_interest": ("少数股东权益",),
    "total_equity": ("所有者权益(或股东权益)合计",),
    "total_liabilities_and_equity": (
        "负债和所有者权益(或股东权益)总计",
        "负债及股东权益总计",
    ),
}

INCOME_FIELDS: dict[str, tuple[str, ...]] = {
    # 银行、保险没有"营业总收入"，直接以"营业收入"作为总口径
    "total_revenue": ("营业总收入", "营业收入"),
    "revenue": ("营业收入", "营业总收入"),
    "total_cost": ("营业总成本",),
    "cost_of_revenue": ("营业成本",),
    "taxes_and_surcharges": ("营业税金及附加",),
    "rd_expense": ("研发费用",),
    "selling_expense": ("销售费用",),
    "admin_expense": ("管理费用", "业务及管理费用"),
    "finance_expense": ("财务费用",),
    "interest_expense": ("利息费用",),
    "investment_income": ("投资收益",),
    "fair_value_change": ("公允价值变动收益",),
    "other_income": ("其他收益",),
    "asset_impairment": ("资产减值损失",),
    "credit_impairment": ("信用减值损失",),
    "asset_disposal_income": ("资产处置收益",),
    "operating_profit": ("营业利润",),
    "non_operating_income": ("营业外收入", "加:营业外收入"),
    "non_operating_expense": ("营业外支出", "减:营业外支出"),
    "total_profit": ("利润总额",),
    "income_tax": ("所得税费用", "减:所得税", "减:所得税费用"),
    "net_profit": ("净利润",),
    "net_profit_attributable": (
        "归属于母公司所有者的净利润",
        "归属于母公司的净利润",
    ),
    # 银行、保险的利润表用"少数股东权益"表示少数股东损益
    "minority_profit": ("少数股东损益", "少数股东权益"),
    "basic_eps": ("基本每股收益",),
    "diluted_eps": ("稀释每股收益",),
}

CASHFLOW_FIELDS: dict[str, tuple[str, ...]] = {
    "cash_from_sales": ("销售商品、提供劳务收到的现金",),
    "tax_refund": ("收到的税费返还",),
    "operating_cash_inflow": ("经营活动现金流入小计",),
    "cash_paid_for_goods": ("购买商品、接受劳务支付的现金",),
    "cash_paid_to_employees": ("支付给职工以及为职工支付的现金",),
    "taxes_paid": ("支付的各项税费",),
    "operating_cash_outflow": ("经营活动现金流出小计",),
    "operating_cash_flow": ("经营活动产生的现金流量净额",),
    "investment_cash_inflow": ("投资活动现金流入小计",),
    "capex": (
        "购建固定资产、无形资产和其他长期资产所支付的现金",
        "购建固定资产、无形资产和其他长期资产支付的现金",
    ),
    "investment_cash_outflow": ("投资活动现金流出小计",),
    "investment_cash_flow": ("投资活动产生的现金流量净额",),
    "financing_cash_inflow": ("筹资活动现金流入小计",),
    "financing_cash_outflow": ("筹资活动现金流出小计",),
    "financing_cash_flow": ("筹资活动产生的现金流量净额",),
    "fx_effect": ("汇率变动对现金及现金等价物的影响",),
    "net_cash_increase": ("现金及现金等价物净增加额",),
    "beginning_cash": ("期初现金及现金等价物余额",),
    "ending_cash": ("期末现金及现金等价物余额",),
}

BALANCE = StatementSpec(
    "balance", "fin_balance", "资产负债表", BALANCE_FIELDS,
    required=("total_assets", "total_liabilities"),
)
INCOME = StatementSpec(
    "income", "fin_income", "利润表", INCOME_FIELDS,
    required=("total_revenue", "net_profit"),
)
CASHFLOW = StatementSpec(
    "cashflow", "fin_cashflow", "现金流量表", CASHFLOW_FIELDS,
    required=("operating_cash_flow",),
)

STATEMENTS: dict[str, StatementSpec] = {
    spec.key: spec for spec in (BALANCE, INCOME, CASHFLOW)
}

# 三张表共有的元数据列，不参与指标计算
META_COLUMNS: dict[str, str] = {
    "ann_date": "公告日期",
    "currency": "币种",
    "audited": "是否审计",
    "statement_type": "类型",
    "source": "数据源",
}

_EMPTY_VALUES = {"", "-", "--", "－", "—", "nan", "none", "null", "不适用", "无"}


def parse_amount(value: Any) -> float | None:
    """把数据源里的单元格解析成 float。

    兼容千分位逗号、括号负数、空占位符以及 pandas 的 NaN。
    无法解析时返回 None，由调用方决定是缺数据还是真的为 0。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number

    text = str(value).strip().replace(",", "").replace("，", "")
    if text.lower() in _EMPTY_VALUES:
        return None
    # 括号表示负数，例如 (1,234)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_report_date(value: Any) -> str | None:
    """把 20260630 之类的报告期统一成 2026-06-30。"""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        return None
    year, month, day = digits[:4], digits[4:6], digits[6:]
    if not ("1990" <= year <= "2100" and "01" <= month <= "12" and "01" <= day <= "31"):
        return None
    return f"{year}-{month}-{day}"


def validate_columns(columns, spec: StatementSpec) -> list[str]:
    """检查数据源是否还提供核心字段。

    只校验 ``spec.required``：银行、保险没有"流动资产合计"这类工商企业
    科目，属于正常情况，可选字段缺失时按 None 入库。核心字段改名时这里
    会先报警，而不是静默地把整列写成空值。
    """
    available = set(columns)
    return [
        field
        for field in spec.required
        if not any(alias in available for alias in spec.fields[field])
    ]
