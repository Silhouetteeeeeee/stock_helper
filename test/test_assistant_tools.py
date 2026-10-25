"""研究工具验证：schema、公司解析、概况/财务工具、来源与单位。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from common.stock import Stock
from stock.finance_history import history_from_rows
from stock.valuation import valuation_from_rows


def fake_snapshot(code: str, **kwargs) -> dict:
    return {
        "available": True,
        "code": code,
        "name": "东鹏饮料" if code == "605499" else "贵州茅台",
        "latest": {
            "report_date": "2026-06-30",
            "fetched_at": "2026-10-24 15:57:57",
        },
        "metrics": {
            "revenue": {"value": 12443036555.29, "yoy": 15.8933},
            "net_profit": {"value": 2867192851.22, "yoy": 20.7485},
            "gross_margin": {"value": 48.3649},
            "net_margin": {"value": 23.0425},
            "roe": {"value": 40.0128},
            "debt_ratio": {"value": 45.472},
            "cash": {
                "operating_cash_flow": 2492600974.2,
                "free_cash_flow": 1357440211.5,
                "ocf_to_net_profit": 0.8694,
                "net_debt": -14869506200.2,
                "cash_balance": 14474702969.6,
            },
            "safety": {
                "interest_debt": 3300000000.0,
                "short_term_debt": 3000000000.0,
                "interest_coverage": 8.5,
                "interest_coverage_basis": "利息费用",
                "goodwill": 500000000.0,
                "goodwill_ratio": 6.2,
                "accounts_receivable": 800000000.0,
                "accounts_receivable_yoy": 100.0,
                "inventory": 600000000.0,
                "inventory_yoy": 9.1,
                "cash_to_short_term_debt": 4.82,
            },
            "detail": {
                "basic_eps": 2.15,
                "diluted_eps": 2.12,
                "equity_parent": 8.0e9,
                "minority_interest": 0.2e9,
                "total_equity": 8.2e9,
                "total_assets": 20.0e9,
                "total_liabilities": 11.8e9,
                "fixed_assets": 3.0e9,
                "fixed_assets_yoy": 12.5,
                "construction_in_progress": 1.0e9,
                "construction_in_progress_yoy": 40.0,
                "intangible_assets": 0.5e9,
                "long_term_equity_invest": 0.3e9,
                "notes_payable": 0.2e9,
                "accounts_payable": 1.2e9,
                "advances_from_customers": 0.1e9,
                "contract_liabilities": 0.4e9,
                "investment_income": -0.05e9,
                "fair_value_change": 0.01e9,
                "asset_impairment": -0.03e9,
                "credit_impairment": -0.02e9,
                "treasury_stock": 0.2e9,
                "rd_expense": 0.4e9,
                "capex": 0.8e9,
                "notes_receivable_yoy": 50.0,
                "prepayments_yoy": -20.0,
                "other_receivables_yoy": 100.0,
                "accounts_payable_yoy": 25.0,
                "contract_liabilities_yoy": 80.0,
                "development_expenditure_yoy": 30.0,
                "balance": {
                    "notes_receivable": 0.6e9,
                    "paid_in_capital": 1.0e9,
                    "total_assets": 20.0e9,
                },
                "income": {
                    "operating_profit": 3.5e9,
                    "selling_expense": 2.0e9,
                    "net_profit_attributable": 2.8e9,
                },
                "cashflow": {
                    "cash_from_sales": 8.0e9,
                    "ending_cash": 2.0e9,
                },
            },
        },
        "trend": [
            {"report_date": "2026-03-31", "revenue_sq": 4848071704.63,
             "revenue_sq_yoy": 39.2, "gross_margin": 44.5, "net_margin": 20.2},
            {"report_date": "2026-06-30", "revenue_sq": 6554787693.92,
             "revenue_sq_yoy": 11.3, "gross_margin": 45.2, "net_margin": 22.3},
        ],
        "sources": {"statement": "新浪财经"},
    }


def fake_valuation(code: str, **kwargs) -> dict:
    return {
        "available": True,
        "code": code,
        "name": "东鹏饮料",
        "as_of": "2026-10-23",
        "fetched_at": "2026-10-24 16:27:38",
        "price": {"close": 74.0, "market_cap": 37.0e9},
        "valuation": {
            "pe_ttm": {"value": 10.2},
            "pb_mrq": {"value": 4.6},
        },
    }


def fake_financial_rows() -> list[dict]:
    return [
        {"report_date": "2025-06-30", "cost_of_revenue": 5e9,
         "net_profit_attributable": 1.8e9, "net_profit": 1.9e9,
         "operating_cash_flow": 2.2e9, "capex": 0.7e9,
         "equity_parent": 7e9, "minority_interest": 0.2e9,
         "fetched_at": "2026-10-24 15:57:57"},
        {"report_date": "2025-12-31", "cost_of_revenue": 5e9,
         "net_profit_attributable": 3.5e9, "net_profit": 3.6e9,
         "operating_cash_flow": 4.0e9, "capex": 1.0e9,
         "equity_parent": 7.5e9, "minority_interest": 0.2e9,
         "fetched_at": "2026-10-24 15:57:57"},
        {"report_date": "2026-06-30", "cost_of_revenue": 5e9,
         "net_profit_attributable": 2.0e9, "net_profit": 2.1e9,
         "operating_cash_flow": 2.5e9, "capex": 0.8e9,
         "equity_parent": 8e9, "minority_interest": 0.2e9,
         "fetched_at": "2026-10-24 15:57:57"},
    ]


def test_schemas() -> None:
    names = [item["function"]["name"] for item in tools.schemas()]
    assert names == [
        "load_skill", "resolve_company", "get_company_profile", "get_finance",
        "get_financial_history", "get_segments", "get_management",
        "get_shareholders", "get_peers", "get_dividends", "get_insider_trades",
        "get_buybacks", "get_lockup_expiries", "get_pledges",
        "get_announcements",
        "get_valuation", "calc_valuation",
    ], names
    for item in tools.schemas():
        assert item["type"] == "function"
        assert item["function"]["parameters"]["type"] == "object"


def test_resolve_company() -> None:
    originals = (tools.get_stock_by_name, tools.get_all_stocks, tools.get_stock)
    tools.get_stock_by_name = lambda name: Stock("605499", "东鹏饮料") \
        if name == "东鹏饮料" else None  # type: ignore[assignment]
    tools.get_all_stocks = lambda: [  # type: ignore[assignment]
        Stock("605499", "东鹏饮料"), Stock("600519", "贵州茅台"),
    ]
    tools.get_stock = lambda code: Stock(code, "东鹏饮料")  # type: ignore[assignment]
    try:
        code, error, candidates = tools.resolve_code("东鹏饮料", None)
        assert code == "605499" and error is None and candidates == []

        code, error, _ = tools.resolve_code(None, "605499")
        assert code == "605499" and error is None

        code, error, candidates = tools.resolve_code("茅台", None)
        assert code is None and "没有找到" in error
        assert candidates == [{"code": "600519", "name": "贵州茅台"}], candidates

        result = tools.run_tool("resolve_company", {"name_or_code": "东鹏饮料"})
        assert result.ok is True and result.data["code"] == "605499", result

        missing = tools.run_tool("get_finance", {})
        assert missing.ok is False and "未指定公司" in missing.error, missing

        # 带前缀/后缀的代码也是用户会直接输入的写法
        for value in ("SH605499", "sh605499", "605499.SH"):
            code, error, _ = tools.resolve_code(value, None)
            assert code == "605499" and error is None, (value, code, error)
        code, error, _ = tools.resolve_code("60549", None)
        assert code is None, (code, error)
    finally:
        tools.get_stock_by_name, tools.get_all_stocks, tools.get_stock = originals  # type: ignore[assignment]


def test_finance_units_and_sources() -> None:
    original = tools.build_snapshot
    tools.build_snapshot = fake_snapshot  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_finance", {}, default_company="605499")
    finally:
        tools.build_snapshot = original  # type: ignore[assignment]

    assert result.ok is True, result
    assert result.company == "605499"
    assert result.data["revenue_yi"] == 124.43, result.data
    assert result.data["net_profit_yi"] == 28.67, result.data
    assert result.data["revenue_yoy"] == 15.9, result.data
    assert result.data["trend"][0]["revenue_sq_yi"] == 48.48, result.data["trend"]
    # 财务安全字段：有息负债、利息保障、商誉、应收/存货同比
    assert result.data["interest_debt_yi"] == 33.0, result.data
    assert result.data["short_term_debt_yi"] == 30.0, result.data
    assert result.data["interest_coverage"] == 8.5, result.data
    assert result.data["interest_coverage_basis"] == "利息费用", result.data
    assert result.data["goodwill_yi"] == 5.0, result.data
    assert result.data["goodwill_ratio"] == 6.2, result.data
    assert result.data["accounts_receivable_yi"] == 8.0, result.data
    assert result.data["accounts_receivable_yoy"] == 100.0, result.data
    assert result.data["inventory_yi"] == 6.0, result.data
    assert result.data["inventory_yoy"] == 9.1, result.data
    assert result.data["cash_to_short_term_debt"] == 4.82, result.data
    # 报表明细：每股收益、权益、产能投入与营运资金科目
    assert result.data["basic_eps"] == 2.15, result.data
    assert result.data["diluted_eps"] == 2.12, result.data
    assert result.data["equity_parent_yi"] == 80.0, result.data
    assert result.data["minority_interest_yi"] == 2.0, result.data
    assert result.data["total_equity_yi"] == 82.0, result.data
    assert result.data["total_assets_yi"] == 200.0, result.data
    assert result.data["total_liabilities_yi"] == 118.0, result.data
    assert result.data["fixed_assets_yi"] == 30.0, result.data
    assert result.data["fixed_assets_yoy"] == 12.5, result.data
    assert result.data["construction_in_progress_yi"] == 10.0, result.data
    assert result.data["construction_in_progress_yoy"] == 40.0, result.data
    assert result.data["intangible_assets_yi"] == 5.0, result.data
    assert result.data["long_term_equity_invest_yi"] == 3.0, result.data
    assert result.data["notes_payable_yi"] == 2.0, result.data
    assert result.data["accounts_payable_yi"] == 12.0, result.data
    assert result.data["advances_from_customers_yi"] == 1.0, result.data
    assert result.data["contract_liabilities_yi"] == 4.0, result.data
    assert result.data["investment_income_yi"] == -0.5, result.data
    assert result.data["fair_value_change_yi"] == 0.1, result.data
    assert result.data["asset_impairment_yi"] == -0.3, result.data
    assert result.data["credit_impairment_yi"] == -0.2, result.data
    assert result.data["treasury_stock_yi"] == 2.0, result.data
    assert result.data["rd_expense_yi"] == 4.0, result.data
    assert result.data["capex_yi"] == 8.0, result.data
    # 营运资金同比与三张表完整科目
    assert result.data["notes_receivable_yoy"] == 50.0, result.data
    assert result.data["prepayments_yoy"] == -20.0, result.data
    assert result.data["accounts_payable_yoy"] == 25.0, result.data
    assert result.data["balance_detail"]["notes_receivable_yi"] == 6.0, result.data
    assert result.data["balance_detail"]["paid_in_capital_yi"] == 10.0, result.data
    assert result.data["balance_detail"]["accounts_receivable_yi"] is None, result.data
    assert result.data["income_detail"]["operating_profit_yi"] == 35.0, result.data
    assert result.data["income_detail"]["selling_expense_yi"] == 20.0, result.data
    assert result.data["cashflow_detail"]["cash_from_sales_yi"] == 80.0, result.data
    assert result.data["cashflow_detail"]["ending_cash_yi"] == 20.0, result.data
    source = result.sources[0]
    assert source.id == "finance:605499:2026-06-30"
    assert source.as_of == "2026-06-30"
    assert source.fetched_at == "2026-10-24 15:57:57"
    assert "finance:605499:2026-06-30" in result.to_message()

    other = tools.run_tool("get_finance", {"company": "600519"}, default_company="605499")
    assert other.company == "600519", other
    assert other.sources[0].id.startswith("finance:600519:"), other.sources


def test_finance_without_safety() -> None:
    """金融机构没有安全字段时工具不能报错，字段应为 None。"""
    original = tools.build_snapshot

    def bare_snapshot(code: str, **kwargs) -> dict:
        snapshot = fake_snapshot(code, **kwargs)
        snapshot["metrics"].pop("safety")
        return snapshot

    tools.build_snapshot = bare_snapshot  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_finance", {}, default_company="600519")
    finally:
        tools.build_snapshot = original  # type: ignore[assignment]

    assert result.ok is True, result
    for key in ("interest_debt_yi", "short_term_debt_yi", "interest_coverage",
                "goodwill_yi", "goodwill_ratio", "accounts_receivable_yi",
                "accounts_receivable_yoy", "inventory_yi", "inventory_yoy",
                "cash_to_short_term_debt"):
        assert result.data[key] is None, (key, result.data[key])


def test_finance_without_detail() -> None:
    """旧缓存或异常快照没有 detail 时，明细字段应为 None 而不是报错。"""
    original = tools.build_snapshot

    def bare_snapshot(code: str, **kwargs) -> dict:
        snapshot = fake_snapshot(code, **kwargs)
        snapshot["metrics"].pop("detail")
        return snapshot

    tools.build_snapshot = bare_snapshot  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_finance", {}, default_company="600519")
    finally:
        tools.build_snapshot = original  # type: ignore[assignment]

    assert result.ok is True, result
    for key in (
        "basic_eps", "diluted_eps", "equity_parent_yi", "minority_interest_yi",
        "total_equity_yi", "total_assets_yi", "total_liabilities_yi",
        "fixed_assets_yi", "fixed_assets_yoy", "construction_in_progress_yi",
        "construction_in_progress_yoy", "intangible_assets_yi",
        "long_term_equity_invest_yi", "notes_payable_yi", "accounts_payable_yi",
        "advances_from_customers_yi", "contract_liabilities_yi",
        "investment_income_yi", "fair_value_change_yi", "asset_impairment_yi",
        "credit_impairment_yi", "treasury_stock_yi", "rd_expense_yi", "capex_yi",
    ):
        assert result.data[key] is None, (key, result.data[key])
    assert result.data["balance_detail"]["notes_receivable_yi"] is None, result.data
    assert result.data["income_detail"]["operating_profit_yi"] is None, result.data
    assert result.data["cashflow_detail"]["cash_from_sales_yi"] is None, result.data


def test_profile_tool() -> None:
    original = tools.build_profile
    tools.build_profile = lambda code, **kwargs: {
        "available": True, "code": code, "name": "东鹏饮料",
        "industry": "食品饮料", "profile": "能量饮料",
        "scope": "食品生产…", "employees": 12376,
        "as_of": "2026-10-24", "fetched_at": "2026-10-24 09:31:02",
        "sources": {"东方财富 F10 公司概况": "2026-10-24"},
    }  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_company_profile", {}, default_company="605499")
    finally:
        tools.build_profile = original  # type: ignore[assignment]
    assert result.ok is True
    assert result.sources[0].id == "profile:605499"
    assert result.sources[0].fetched_at == "2026-10-24 09:31:02", result.sources[0]


def fake_segments(rows, kind=None, code=None, name=None, stale=False,
                  years=5) -> dict:
    if kind == "3" and not REGION:
        return {"available": False}
    items = ([{"name": "能量饮料", "income": 8936615605.76, "share": 71.82,
               "yoy": 6.89, "gross_margin": 54.67, "is_other": False}]
             if kind == "2" else
             [{"name": "华南", "income": 5000000000.0, "share": 40.2,
               "yoy": 9.1, "gross_margin": None, "is_other": False}])
    return {
        "available": True, "code": code, "name": name,
        "as_of": "2026-06-30", "kind": kind,
        "kind_label": "产品" if kind == "2" else "地区",
        "total_income": 12443036555.29, "items": items,
        "history": [
            {
                "period": "2025-06-30",
                "total_income": 8000000000.0,
                "items": [
                    {"name": items[0]["name"], "income": 6000000000.0,
                     "share": 75.0, "yoy": None, "gross_margin": 50.0},
                ],
            },
            {
                "period": "2026-06-30",
                "total_income": 12443036555.29,
                "items": [
                    {"name": items[0]["name"], "income": items[0]["income"],
                     "share": items[0]["share"], "yoy": items[0]["yoy"],
                     "gross_margin": items[0]["gross_margin"]},
                ],
            },
        ],
    }


REGION = True


def test_load_skill_tool() -> None:
    # 没有默认公司也必须能加载 skill
    result = tools.run_tool("load_skill", {"name": "moat"})
    assert result.ok is True, result
    assert result.data["name"] == "moat"
    assert "可证伪" in result.data["instructions"]

    third = tools.run_tool("load_skill", {"name": "operating_history"})
    assert third.ok is True, third
    assert "自由现金流" in third.data["instructions"]

    fourth = tools.run_tool("load_skill", {"name": "financial_safety"})
    assert fourth.ok is True, fourth
    assert "利息保障" in fourth.data["instructions"]

    fifth = tools.run_tool("load_skill", {"name": "growth_outlook"})
    assert fifth.ok is True, fifth
    assert "市场空间" in fifth.data["instructions"]

    sixth = tools.run_tool("load_skill", {"name": "management"})
    assert sixth.ok is True, sixth
    assert "关联交易" in sixth.data["instructions"]

    seventh = tools.run_tool("load_skill", {"name": "valuation"})
    assert seventh.ok is True, seventh
    assert "历史分位" in seventh.data["instructions"]

    eighth = tools.run_tool("load_skill", {"name": "margin_of_safety"})
    assert eighth.ok is True, eighth
    assert "安全边际" in eighth.data["instructions"]

    missing = tools.run_tool("load_skill", {"name": "q9"})
    assert missing.ok is False
    assert "business_model" in missing.error and "moat" in missing.error, missing

    schema = next(
        item for item in tools.schemas()
        if item["function"]["name"] == "load_skill"
    )
    assert schema["function"]["parameters"]["properties"]["name"]["enum"] == [
        "business_model", "moat", "operating_history", "financial_safety",
        "growth_outlook", "management", "valuation", "margin_of_safety",
    ]


def test_financial_history_tool() -> None:
    rows = [
        {"report_date": f"{year}-12-31", "revenue": (80 + 10 * i) * 1e8,
         "revenue_yoy": 10.0, "net_profit": 20e8, "gross_margin": 45.0,
         "fetched_at": "2026-10-24 15:00:00"}
        for i, year in enumerate(range(2016, 2026))
    ]

    def fake_history(code, years=10):
        data = history_from_rows(rows, years=years)
        return {
            "available": data["available"], "code": code, "name": "东鹏饮料",
            "as_of": data["as_of"], "fetched_at": data["fetched_at"],
            "span": data["span"], "years": data["years"], "notes": data["notes"],
        }

    original = getattr(tools, "build_history", None)
    tools.build_history = fake_history  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_financial_history", {"years": 5}, default_company="605499"
        )
    finally:
        tools.build_history = original  # type: ignore[assignment]
    assert result.ok is True, result
    assert result.data["span"] == "2021-2025", result.data
    assert len(result.data["years"]) == 5, result.data
    assert result.sources[0].id == "finance_history:605499:2021-2025", result.sources
    assert result.sources[0].as_of == "2025-12-31", result.sources


def test_financial_history_missing() -> None:
    original = getattr(tools, "build_history", None)
    tools.build_history = lambda code, years=10: {"available": False}  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_financial_history", {}, default_company="000001")
    finally:
        tools.build_history = original  # type: ignore[assignment]
    assert result.ok is False and "年报" in result.error, result


def test_segments_two_slices() -> None:
    rows = [{"code": "605499", "fetched_at": "2026-10-24 15:57:57"}]
    originals = (tools.ensure_segments, tools.segments_from_rows, tools.get_stock)
    tools.ensure_segments = lambda code, **kw: {"rows": rows, "stale": False}  # type: ignore[assignment]
    tools.segments_from_rows = fake_segments  # type: ignore[assignment]
    tools.get_stock = lambda code: Stock(code, "东鹏饮料")  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_segments", {}, default_company="605499")
    finally:
        tools.ensure_segments, tools.segments_from_rows, tools.get_stock = originals  # type: ignore[assignment]

    assert result.ok is True, result
    ids = [source.id for source in result.sources]
    assert ids == [
        "segments:605499:2026-06-30:product",
        "segments:605499:2026-06-30:region",
    ], ids
    assert [slice_["label"] for slice_ in result.data["slices"]] == ["产品", "地区"], result.data
    product = result.data["slices"][0]
    assert [period["period"] for period in product["history"]] == [
        "2025-06-30", "2026-06-30",
    ], product
    latest = product["history"][-1]
    assert latest["total_income_yi"] == 124.43, latest
    assert latest["items"][0]["income_yi"] == 89.37, latest
    assert latest["items"][0]["yoy"] == 6.9, latest
    assert product["history"][0]["items"][0]["yoy"] is None, product


def test_management_tool() -> None:
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-10-10", "salary_year": "2025", "count": 2,
        "fetched_at": "2026-10-25 10:00:00", "stale": False,
        "groups": {
            "directors": {
                "label": "董事会", "count": 1, "salary_total": 5316100,
                "hold_ratio": 46.7751, "direct_ratio": 45.8235,
                "indirect_ratio": 0.9516,
                "members": [{
                    "name": "林木勤", "duties": ["董事长", "董事"],
                    "tenure": "2018-01-01 至今", "age": "62岁",
                    "education": "硕士", "salary": 5316100,
                    "hold": 343422644.12, "hold_ratio": 46.7751,
                    "direct_ratio": 45.8235, "indirect_ratio": 0.9516,
                    "is_control": True, "is_legal_person": True,
                    "resume": "林木勤先生，中国国籍，硕士学历。",
                }],
            },
            "auditors": {
                "label": "审计委员会", "count": 1, "salary_total": 112500,
                "hold_ratio": None, "direct_ratio": None,
                "indirect_ratio": None,
                "members": [{
                    "name": "王五", "duties": ["审计委员会主任委员"],
                    "tenure": "2025-04-02 至今", "age": "70岁",
                    "education": "博士", "salary": 112500, "hold": None,
                    "hold_ratio": None, "direct_ratio": None,
                    "indirect_ratio": None, "is_control": False,
                    "is_legal_person": False, "resume": "",
                }],
            },
        },
    }
    original = tools.build_management
    tools.build_management = lambda code, **kwargs: data  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_management", {"resume_chars": 5}, default_company="605499"
        )
    finally:
        tools.build_management = original  # type: ignore[assignment]

    assert result.ok is True, result
    assert result.as_of == "2026-10-10", result
    assert result.sources[0].id == "management:605499", result.sources
    assert result.sources[0].url == "https://basic.10jqka.com.cn/605499/company.html", (
        result.sources
    )
    directors = result.data["groups"][0]
    assert directors["label"] == "董事会", directors
    assert directors["salary_total_wan"] == 531.61, directors
    leader = directors["members"][0]
    assert leader["duties"] == ["董事长", "董事"], leader
    assert leader["salary_wan"] == 531.61, leader
    assert leader["hold_wan"] == 34342.26, leader
    assert leader["hold_ratio"] == 46.8, leader
    assert leader["resume"] == "林木勤先生…", leader
    assert result.data["groups"][1]["members"][0]["resume"] is None, result.data


def test_shareholders_tool() -> None:
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-06-30", "fetched_at": "2026-10-25 10:00:00",
        "stale": False,
        "controller": {"name": "林木勤", "ratio": 45.8235},
        "top10": [{
            "period": "2026-06-30", "rank": 1, "name": "林木勤",
            "share_type": "流通A股,限售流通A股", "shares": 343422644.12,
            "ratio": 45.8235, "change": None, "change_text": "不变",
            "change_ratio": None,
        }],
        "free_float_top10": [{
            "period": "2026-06-30", "rank": 1, "name": "林木勤",
            "share_type": "流通A股", "shares": 336435820.81,
            "ratio": 45.0, "change": 1234567, "change_text": None,
            "change_ratio": 2.12, "holder_type": "个人",
        }],
        "holder_count": {
            "latest": {
                "period": "2026-06-30", "holders": 46264,
                "holders_change_pct": 52.42, "avg_free_shares": 15304,
                "avg_free_shares_change_pct": -34.4, "focus": "非常分散",
                "top10_ratio": 66.5, "free_float_top10_ratio": 55.2,
            },
            "history": [{
                "period": "2026-06-30", "holders": 46264,
                "holders_change_pct": 52.42, "avg_free_shares": 15304,
                "avg_free_shares_change_pct": -34.4, "focus": "非常分散",
                "top10_ratio": 66.5, "free_float_top10_ratio": 55.2,
            }],
        },
        "counts": {"top10": 1, "free_float_top10": 1, "holder_periods": 1},
    }
    original = tools.build_shareholders
    tools.build_shareholders = lambda code, **kwargs: data  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_shareholders", {"periods": 4}, default_company="605499"
        )
    finally:
        tools.build_shareholders = original  # type: ignore[assignment]

    assert result.ok is True, result
    assert result.as_of == "2026-06-30", result
    assert result.sources[0].id == "shareholders:605499:2026-06-30", result.sources
    assert result.sources[0].url == (
        "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/"
        "Index?type=web&code=SH605499"
    ), result.sources
    assert result.data["controller"] == {"name": "林木勤", "ratio": 45.8}, result.data
    top = result.data["top10"][0]
    assert top["shares_wan"] == 34342.26, top
    assert top["ratio"] == 45.8 and top["change_text"] == "不变", top
    free = result.data["free_float_top10"][0]
    assert free["holder_type"] == "个人", free
    assert free["change_wan"] == 123.46 and free["change_ratio"] == 2.1, free
    latest = result.data["holder_count"]["latest"]
    assert latest["holders"] == 46264, latest
    assert latest["holders_change_pct"] == 52.4, latest
    assert latest["avg_free_shares"] == 15304, latest
    assert result.data["counts"]["holder_periods"] == 1, result.data


def test_peers_tool() -> None:
    subject = {
        "code": "605499", "name": "东鹏饮料", "market": "上交所主板",
        "revenue": 12443036555.29, "net_profit": 2866801858.34,
        "revenue_yoy": 15.8933, "profit_yoy": 20.72,
        "gross_margin": 48.3649, "net_margin": 23.038,
        "roe": 19.03, "eps": 5.5, "bps": 30.0, "ocf_per_share": 6.0,
        "pe_ttm": 25.0, "pb_mrq": 8.0, "market_cap": 100_000_000_000.0,
        "revenue_ratio": 1.0, "market_cap_ratio": 1.0,
    }
    peer = {
        "code": "600597", "name": "光明乳业", "market": "上交所主板",
        "revenue": 11870000000.0, "net_profit": 120000000.0,
        "revenue_yoy": -4.8076, "profit_yoy": -30.0,
        "gross_margin": 17.5729, "net_margin": 1.011,
        "roe": 2.64, "eps": 0.1, "bps": 5.0, "ocf_per_share": 0.5,
        "pe_ttm": -80.17, "pb_mrq": 0.9577,
        "market_cap": 9_300_000_000.0,
        "revenue_ratio": 0.954, "market_cap_ratio": 0.093,
    }
    leader = {
        **peer, "code": "600887", "name": "伊利股份",
        "revenue": 64489715236.16, "revenue_ratio": 5.18,
        "pe_ttm": 18.0, "pb_mrq": 4.0,
        "market_cap": 600_000_000_000.0, "market_cap_ratio": 6.0,
    }
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-06-30", "fetched_at": "2026-10-25 10:00:00",
        "stale": False,
        "industry": {"name": "食品饮料", "code": "BK1282", "count": 10},
        "report": {
            "period": "2026-06-30", "qdate": "2026年二季度",
            "datatype": "2026年半年报",
        },
        "valuation_date": "2026-10-23", "valuation_stale": False,
        "valuation_error": None,
        "counts": {
            "industry": 10, "valuation": 9, "selected": 1, "leaders": 1,
        },
        "subject": subject,
        "stats": {
            "revenue": {
                "value": 12443036555.29, "median": 3485000000.0,
                "rank": 2, "percentile": 90.0, "count": 10,
            },
            "pe_ttm": {
                "value": 25.0, "median": 22.5,
                "rank": 3, "percentile": 75.0, "count": 8,
            },
        },
        "peers": [peer], "leaders": [leader],
        "notes": ["估值日期与财报期不同"],
        "url": "https://data.eastmoney.com/bbsj/202606/yjbb.html",
    }

    captured = {}

    def fake_build(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return data

    original = tools.build_peers
    tools.build_peers = fake_build  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_peers", {"limit": 99}, default_company="605499"
        )
    finally:
        tools.build_peers = original  # type: ignore[assignment]

    assert captured["limit"] == 20, captured
    assert result.ok is True, result
    assert result.as_of == "2026-06-30", result
    assert result.sources[0].id == "peers:605499:2026-06-30", result.sources
    assert result.sources[0].label == "同业对比", result.sources
    assert result.sources[0].url == (
        "https://data.eastmoney.com/bbsj/202606/yjbb.html"
    ), result.sources
    assert "食品饮料 10 家" in result.sources[0].detail, result.sources

    subject_row = result.data["subject"]
    assert subject_row["revenue_yi"] == 124.43, subject_row
    assert subject_row["net_margin"] == 23.0, subject_row
    assert subject_row["gross_margin"] == 48.4, subject_row
    assert subject_row["pe_ttm"] == 25.0, subject_row

    revenue = result.data["stats"]["revenue"]
    assert revenue["value"] == 124.43 and revenue["median"] == 34.85, revenue
    assert revenue["rank"] == 2 and revenue["percentile"] == 90.0, revenue
    pe_stat = result.data["stats"]["pe_ttm"]
    assert pe_stat["median"] == 22.5 and pe_stat["count"] == 8, pe_stat

    row = result.data["peers"][0]
    assert row["name"] == "光明乳业", row
    assert row["revenue_yi"] == 118.7, row
    assert row["pe_ttm"] == -80.17 and row["pb_mrq"] == 0.96, row
    assert row["revenue_ratio"] == 0.95, row
    assert result.data["leaders"][0]["name"] == "伊利股份", result.data
    assert result.data["valuation_date"] == "2026-10-23", result.data
    assert result.data["counts"]["industry"] == 10, result.data
    assert result.data["report"]["qdate"] == "2026年二季度", result.data
    assert result.data["notes"] == ["估值日期与财报期不同"], result.data


def test_dividends_tool() -> None:
    latest = {
        "report_date": "2026-06-30", "fiscal_year": 2026,
        "period_label": "中报", "plan": "10派30.00元(含税,扣税后27.00元)",
        "plan_short": "10派30.00元", "status": "实施分配",
        "cash_per_10": 30.0, "cash_per_share": 3.0,
        "cash_total": 2_202_597_930.0, "bonus_per_10": None,
        "transfer_per_10": None, "share_bonus_per_10": None,
        "total_shares": 734_199_310, "yield_pct": 2.621231979,
        "eps": 3.9949, "plan_notice_date": "2026-07-31",
        "notice_date": "2026-09-18", "record_date": "2026-09-23",
        "ex_date": "2026-09-24",
    }
    year = {
        "year": 2025, "periods": ["2025-06-30", "2025-12-31"],
        "report_count": 2, "cash_total": 2_745_521_025.0,
        "cash_complete": True, "net_profit": 4_415_263_147.79,
        "payout_ratio": 62.2, "payout_basis": "归母净利润",
        "share_bonus_count": 1,
    }
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-06-30", "fetched_at": "2026-10-25 10:00:00",
        "stale": False, "has_dividends": True,
        "latest": latest, "annual": [year], "events": [latest],
        "counts": {
            "events": 9, "years": 6, "cash_years": 6,
            "consecutive_years": 5, "latest_cash_year": 2026,
        },
        "notes": ["分红总额按税前每 10 股派息估算"],
    }
    captured = {}

    def fake_build(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return data

    original = tools.build_dividends
    tools.build_dividends = fake_build  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_dividends", {"years": 99}, default_company="605499"
        )
    finally:
        tools.build_dividends = original  # type: ignore[assignment]

    assert captured["years"] == 20, captured
    assert result.ok is True, result
    assert result.as_of == "2026-06-30", result
    assert result.sources[0].id == "dividends:605499:2026-06-30", result.sources
    assert result.sources[0].label == "分红历史", result.sources
    assert result.sources[0].url == (
        "https://data.eastmoney.com/yjfp/detail/605499.html"
    ), result.sources
    assert result.sources[0].detail == "东鹏饮料 分红 2026-06-30", result.sources

    event = result.data["latest"]
    assert event["cash_per_10"] == 30.0, event
    assert event["cash_per_share"] == 3.0, event
    assert event["cash_total_yi"] == 22.03, event
    assert event["total_shares_wan"] == 73419.93, event
    assert event["yield_pct"] == 2.62, event
    assert event["eps"] == 3.99, event
    assert event["plan_short"] == "10派30.00元", event

    annual = result.data["annual"][0]
    assert annual["cash_total_yi"] == 27.46, annual
    assert annual["net_profit_yi"] == 44.15, annual
    assert annual["payout_ratio"] == 62.2, annual
    assert annual["payout_basis"] == "归母净利润", annual
    assert result.data["counts"]["consecutive_years"] == 5, result.data
    assert result.data["notes"] == ["分红总额按税前每 10 股派息估算"], result.data


def test_insider_trades_tool() -> None:
    latest = {
        "change_date": "2026-05-29", "year": 2026, "person": "林木勤",
        "executive": "林木勤", "position": "董事长,董事,总裁",
        "relation": "本人", "kind": "individual", "direction": "增持",
        "shares": 49_800, "average_price": 129.7958,
        "amount": 6_463_830.84, "amount_estimated": False,
        "reason": "二级市场买卖", "change_ratio_pct": 0.0068,
        "hold_after": 336_304_724, "begin_hold": None, "end_hold": None,
        "hold_type": "A股",
    }
    annual = {
        "year": 2025, "events": 2, "buy_events": 1, "sell_events": 1,
        "buy_shares": 1_000, "sell_shares": 99_000, "net_shares": -98_000,
        "buy_amount": 10_000, "sell_amount": 19_192_239,
        "net_amount": -19_182_239, "missing_amount_events": 0,
        "amount_complete": True,
    }
    person = {
        "person": "2026年度核心人员持股计划", "executive": None,
        "position": None, "relation": "其他组织", "kind": "plan",
        "events": 1, "buy_shares": 1_000_000, "sell_shares": 0,
        "net_shares": 1_000_000, "buy_amount": 50_000_000,
        "sell_amount": 0, "net_amount": 50_000_000,
        "missing_amount_events": 0, "amount_complete": True,
        "first_date": "2026-05-26", "latest_date": "2026-05-26",
        "latest_hold": 1_000_000, "latest_price": 50.0,
    }
    counts = {
        "events": 7, "buy_events": 4, "sell_events": 3, "people": 7,
        "insiders": 6, "plans": 1, "plan_events": 1,
        "buy_shares": 1_052_800, "sell_shares": 112_800,
        "net_shares": 940_000, "buy_amount": 56_513_830.84,
        "sell_amount": 21_712_408.5, "net_amount": 34_801_422.34,
        "missing_amount_events": 0, "estimated_amount_events": 1,
        "latest_date": "2026-05-29",
    }
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-05-29", "fetched_at": "2026-10-25 10:00:00",
        "stale": False, "window_start": "2024-01-01",
        "window_end": "2026-10-25", "years": 3, "has_trades": True,
        "latest": latest, "annual": [annual], "people": [person],
        "events": [latest], "events_truncated": 0, "counts": counts,
        "notes": ["持股计划/其他组织单列，金额不代表个人增持"],
    }
    captured = {}

    def fake_build(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return data

    original = tools.build_insider_trades
    tools.build_insider_trades = fake_build  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_insider_trades", {"years": 99}, default_company="605499"
        )
    finally:
        tools.build_insider_trades = original  # type: ignore[assignment]

    assert captured["years"] == 10, captured
    assert result.ok is True, result
    assert result.as_of == "2026-05-29", result
    assert result.sources[0].id == "insider:605499:2026-05-29", result.sources
    assert result.sources[0].label == "高管增减持", result.sources
    assert result.sources[0].url == (
        "https://data.eastmoney.com/executive/605499.html"
    ), result.sources
    assert result.sources[0].detail == "东鹏饮料 高管增减持 2026-05-29", result.sources

    event = result.data["latest"]
    assert event["shares"] == 49_800, event
    assert event["average_price"] == 129.8, event
    assert event["amount_wan"] == 646.38, event
    assert event["change_ratio_pct"] == 0.0068, event
    assert event["hold_after"] == 336_304_724, event
    assert event["relation"] == "本人", event

    year = result.data["annual"][0]
    assert year["buy_amount_wan"] == 1.0, year
    assert year["sell_amount_wan"] == 1919.22, year
    assert year["net_amount_wan"] == -1918.22, year
    assert year["amount_complete"] is True, year

    plan = result.data["people"][0]
    assert plan["kind"] == "plan", plan
    assert plan["net_amount_wan"] == 5000.0, plan
    assert plan["latest_price"] == 50.0, plan

    counts = result.data["counts"]
    assert counts["net_shares"] == 940_000, counts
    assert counts["buy_amount_wan"] == 5651.38, counts
    assert counts["sell_amount_wan"] == 2171.24, counts
    assert counts["net_amount_wan"] == 3480.14, counts
    assert counts["estimated_amount_events"] == 1, counts
    assert result.data["window_start"] == "2024-01-01", result.data
    assert result.data["window_end"] == "2026-10-25", result.data
    assert result.data["has_trades"] is True, result.data
    assert result.data["notes"] == ["持股计划/其他组织单列，金额不代表个人增持"], result.data


def test_buybacks_tool() -> None:
    latest = {
        "plan_id": "15569", "announce_date": "2026-04-02", "year": 2026,
        "status": "实施中", "progress_code": "004", "is_active": True,
        "pending": False, "completed": False, "stopped": False,
        "start_date": "2026-04-30", "end_date": "2027-04-29",
        "update_date": "2026-09-18", "finish_date": None,
        "advanced_date": None,
        "planned_amount_low": 1_000_000_000,
        "planned_amount_high": 2_000_000_000,
        "planned_shares_low": 5_000_000, "planned_shares_high": 10_000_000,
        "planned_price_low": 134.2, "planned_price_high": 185.88,
        "planned_ratio_low_pct": 0.95257, "planned_ratio_high_pct": 1.90514,
        "actual_amount": 1_038_719_867.57, "actual_shares": 7_122_551,
        "actual_price_low": 134.2, "actual_price_high": 154.9,
        "actual_ratio_total_pct": 1.42885,
        "actual_ratio_float_pct": 1.55183,
        "amount_completion_pct": 103.9, "shares_completion_pct": 142.5,
        "purpose": "不低于90%用于注销，剩余用于股权激励",
        "purpose_short": "不低于90%用于注销，剩余用于股权激励",
        "purpose_tags": ["注销", "股权激励"],
        "cancel": True, "incentive": True,
    }
    annual = {
        "year": 2026, "plans": 2, "active": 1, "pending": 1,
        "completed": 0, "stopped": 0,
        "actual_amount": 1_038_719_867.57, "actual_shares": 7_122_551,
        "planned_amount_low": 1_500_000_000,
        "planned_amount_high": 3_000_000_000,
        "completion_pct": 69.2, "cancel_plans": 1,
        "incentive_plans": 1, "amount_complete": True,
    }
    counts = {
        "plans": 4, "active": 2, "pending": 1, "completed": 1, "stopped": 1,
        "cancel_plans": 4, "incentive_plans": 1,
        "actual_amount": 4_438_646_984.14, "actual_shares": 11_188_165,
        "planned_amount_low": 4_900_000_000.0,
        "planned_amount_high": 6_400_000_000.0,
        "completion_pct": 90.6, "missing_actual": 1,
        "amount_complete": False, "planned_amount_complete": True,
        "latest_update": "2026-09-18",
    }
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-09-18", "fetched_at": "2026-10-25 10:00:00",
        "stale": False, "window_start": "2022-01-01",
        "window_end": "2026-10-25", "years": 5, "has_buybacks": True,
        "latest": latest, "annual": [annual], "plans": [latest],
        "plans_truncated": 0, "counts": counts,
        "notes": ["用于注销的回购减少总股本"],
        "url": "https://data.eastmoney.com/gphg/detail/605499-2026-04-02.html",
    }
    captured = {}

    def fake_build(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return data

    original = tools.build_buybacks
    tools.build_buybacks = fake_build  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_buybacks", {"years": 99}, default_company="605499"
        )
    finally:
        tools.build_buybacks = original  # type: ignore[assignment]

    assert captured["years"] == 10, captured
    assert result.ok is True, result
    assert result.as_of == "2026-09-18", result
    assert result.sources[0].id == "buybacks:605499:2026-09-18", result.sources
    assert result.sources[0].label == "股票回购", result.sources
    assert result.sources[0].url == (
        "https://data.eastmoney.com/gphg/detail/605499-2026-04-02.html"
    ), result.sources
    assert result.sources[0].detail == "东鹏饮料 股票回购 2026-09-18", result.sources

    plan = result.data["latest"]
    assert plan["status"] == "实施中", plan
    assert plan["planned_amount_low_yi"] == 10.0, plan
    assert plan["planned_amount_high_yi"] == 20.0, plan
    assert plan["planned_shares_low_wan"] == 500.0, plan
    assert plan["planned_shares_high_wan"] == 1000.0, plan
    assert plan["planned_ratio_low_pct"] == 0.95, plan
    assert plan["actual_amount_yi"] == 10.39, plan
    assert plan["actual_shares_wan"] == 712.26, plan
    assert plan["actual_ratio_total_pct"] == 1.43, plan
    assert plan["amount_completion_pct"] == 103.9, plan
    assert plan["shares_completion_pct"] == 142.5, plan
    assert plan["cancel"] is True and plan["incentive"] is True, plan

    year = result.data["annual"][0]
    assert year["actual_amount_yi"] == 10.39, year
    assert year["planned_amount_low_yi"] == 15.0, year
    assert year["completion_pct"] == 69.2, year
    assert year["cancel_plans"] == 1, year

    counts = result.data["counts"]
    assert counts["actual_amount_yi"] == 44.39, counts
    assert counts["actual_shares_wan"] == 1118.82, counts
    assert counts["planned_amount_low_yi"] == 49.0, counts
    assert counts["planned_amount_high_yi"] == 64.0, counts
    assert counts["completion_pct"] == 90.6, counts
    assert counts["missing_actual"] == 1, counts
    assert counts["amount_complete"] is False, counts
    assert result.data["window_start"] == "2022-01-01", result.data
    assert result.data["window_end"] == "2026-10-25", result.data
    assert result.data["has_buybacks"] is True, result.data
    assert result.data["notes"] == ["用于注销的回购减少总股本"], result.data


def test_lockup_expiries_tool() -> None:
    next_event = {
        "free_date": "2026-11-20", "year": 2026, "is_upcoming": True,
        "days_until": 26, "shares": 10_000_000,
        "market_cap": 5_000_000_000.0, "price": 50.0,
        "ratio_total_pct": 2.5, "ratio_free_pct": 10.0, "holders": 5,
        "shares_type": "股权激励限售股份", "market_type": "sh",
        "free_shares_after": None, "non_free_shares_after": None,
        "able_free_shares": None, "update_date": "2026-10-20",
    }
    latest = {
        "free_date": "2024-05-27", "year": 2024, "is_upcoming": False,
        "days_until": -516, "shares": 240_739_143,
        "market_cap": 53_680_014_106.14, "price": 222.98,
        "ratio_total_pct": 60.183281168,
        "ratio_free_pct": 60.183281168, "holders": 3,
        "shares_type": "首发原股东限售股份", "market_type": "sh",
        "free_shares_after": 400_010_000, "non_free_shares_after": 0,
        "able_free_shares": 240_739_143, "update_date": "2024-11-18",
    }
    annual = {
        "year": 2022, "events": 1, "history_events": 1,
        "upcoming_events": 0, "shares": 36_000_000,
        "market_cap": 4_953_600_000.0, "missing_market_cap": 0,
    }
    types = {
        "type": "首发原股东限售股份", "events": 3, "upcoming": 0,
        "shares": 360_000_000, "market_cap": 72_563_155_482.24,
    }
    counts = {
        "events": 5, "history_events": 3, "upcoming_events": 2,
        "next_date": "2026-11-20", "next_days": 26,
        "next_shares": 10_000_000, "next_market_cap": 5_000_000_000.0,
        "next_ratio_pct": 2.5, "next_holders": 5,
        "next_type": "股权激励限售股份",
        "upcoming_shares": 30_000_000,
        "upcoming_market_cap": 13_000_000_000.0,
        "upcoming_ratio_pct": 7.5, "history_shares": 360_000_000,
        "history_market_cap": 72_563_155_482.24,
        "total_shares": 390_000_000,
        "total_market_cap": 85_563_155_482.24,
        "latest_date": "2024-05-27", "latest_type": "首发原股东限售股份",
        "missing_market_cap": 0,
    }
    data = {
        "available": True, "code": "605499", "name": "东鹏饮料",
        "as_of": "2026-10-21", "fetched_at": "2026-10-25 10:00:00",
        "stale": False, "window_start": "2022-01-01",
        "window_end": "2027-10-25", "years": 5, "ahead_months": 12,
        "has_lockups": True, "latest": latest, "next": next_event,
        "upcoming": [next_event], "history": [latest], "annual": [annual],
        "types": [types], "events_truncated": 0, "counts": counts,
        "notes": ["解禁不等于减持"], "url": (
            "https://data.eastmoney.com/dxf/detail.html?code=605499"
        ),
    }
    captured = {}

    def fake_build(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return data

    original = tools.build_lockups
    tools.build_lockups = fake_build  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_lockup_expiries",
            {"years": 99, "ahead_months": 99},
            default_company="605499",
        )
    finally:
        tools.build_lockups = original  # type: ignore[assignment]

    assert captured["years"] == 10, captured
    assert captured["ahead_months"] == 36, captured
    assert result.ok is True, result
    assert result.as_of == "2026-10-21", result
    assert result.sources[0].id == "lockups:605499:2026-10-21", result.sources
    assert result.sources[0].label == "限售解禁", result.sources
    assert result.sources[0].url == (
        "https://data.eastmoney.com/dxf/detail.html?code=605499"
    ), result.sources
    assert result.sources[0].detail == "东鹏饮料 限售解禁 2026-10-21", result.sources

    event = result.data["next"]
    assert event["shares_wan"] == 1000.0, event
    assert event["market_cap_yi"] == 50.0, event
    assert event["ratio_total_pct"] == 2.5, event
    assert event["days_until"] == 26, event

    latest_row = result.data["latest"]
    assert latest_row["shares_wan"] == 24073.91, latest_row
    assert latest_row["market_cap_yi"] == 536.8, latest_row
    assert latest_row["free_shares_after_wan"] == 40001.0, latest_row
    assert latest_row["non_free_shares_after_wan"] == 0.0, latest_row

    year = result.data["annual"][0]
    assert year["shares_wan"] == 3600.0, year
    assert year["market_cap_yi"] == 49.54, year

    type_row = result.data["types"][0]
    assert type_row["shares_wan"] == 36000.0, type_row
    assert type_row["market_cap_yi"] == 725.63, type_row

    counts = result.data["counts"]
    assert counts["next_shares_wan"] == 1000.0, counts
    assert counts["upcoming_market_cap_yi"] == 130.0, counts
    assert counts["history_shares_wan"] == 36000.0, counts
    assert counts["latest_date"] == "2024-05-27", counts
    assert result.data["has_lockups"] is True, result.data
    assert result.data["notes"] == ["解禁不等于减持"], result.data


def test_pledges_tool() -> None:
    latest = {
        "trade_date": "2026-10-23", "year": 2026, "ratio_pct": 28.5,
        "balance_shares": 1_000_000_000, "market_cap": 5_000_000_000.0,
        "deal_num": 5, "unlimited_shares": 1_000_000_000,
        "limited_shares": 0, "industry": "软件开发",
    }
    series = {
        "month": "2026-10", "trade_date": "2026-10-23",
        "ratio_pct": 28.5, "balance_shares": 1_000_000_000,
        "market_cap": 5_000_000_000.0, "deal_num": 5,
    }
    annual = {
        "year": 2026, "points": 3, "latest_date": "2026-10-23",
        "latest_ratio_pct": 28.5, "balance_shares": 1_000_000_000,
        "market_cap": 5_000_000_000.0, "peak_ratio_pct": 35.0,
        "low_ratio_pct": 28.5,
    }
    counts = {
        "points": 6, "latest_date": "2026-10-23",
        "latest_ratio_pct": 28.5,
        "latest_balance_shares": 1_000_000_000,
        "latest_market_cap": 5_000_000_000.0, "latest_deal_num": 5,
        "latest_unlimited_shares": 1_000_000_000,
        "latest_limited_shares": 0, "latest_industry": "软件开发",
        "year_ago_date": "2025-10-24", "year_ago_ratio_pct": 31.0,
        "change_pp": -2.5, "direction": "下降", "peak_ratio_pct": 40.0,
        "peak_date": "2024-06-28", "low_ratio_pct": 28.5,
        "low_date": "2026-10-23", "avg_ratio_pct": 32.75,
        "high_pledge": False, "watch_pledge": False,
    }
    data = {
        "available": True, "code": "002230", "name": "科大讯飞",
        "as_of": "2026-10-23", "fetched_at": "2026-10-25 10:00:00",
        "stale": False, "window_start": "2024-01-01",
        "window_end": "2026-10-25", "years": 3, "has_pledges": True,
        "latest": latest, "series": [series], "annual": [annual],
        "counts": counts, "notes": ["质押比例不等于违约"],
        "url": "https://data.eastmoney.com/gpzy/detail/002230.html",
    }
    captured = {}

    def fake_build(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return data

    original = tools.build_pledges
    tools.build_pledges = fake_build  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_pledges", {"years": 99}, default_company="002230"
        )
    finally:
        tools.build_pledges = original  # type: ignore[assignment]

    assert captured["years"] == 10, captured
    assert result.ok is True, result
    assert result.as_of == "2026-10-23", result
    assert result.sources[0].id == "pledges:002230:2026-10-23", result.sources
    assert result.sources[0].label == "股权质押", result.sources
    assert result.sources[0].url == (
        "https://data.eastmoney.com/gpzy/detail/002230.html"
    ), result.sources
    assert result.sources[0].detail == "科大讯飞 股权质押 2026-10-23", result.sources

    point = result.data["latest"]
    assert point["ratio_pct"] == 28.5, point
    assert point["balance_shares_wan"] == 100000.0, point
    assert point["market_cap_yi"] == 50.0, point
    assert point["deal_num"] == 5, point

    month = result.data["series"][0]
    assert month["month"] == "2026-10", month
    assert month["balance_shares_wan"] == 100000.0, month

    year = result.data["annual"][0]
    assert year["latest_ratio_pct"] == 28.5, year
    assert year["peak_ratio_pct"] == 35.0, year
    assert year["market_cap_yi"] == 50.0, year

    counts = result.data["counts"]
    assert counts["latest_ratio_pct"] == 28.5, counts
    assert counts["latest_balance_shares_wan"] == 100000.0, counts
    assert counts["latest_market_cap_yi"] == 50.0, counts
    assert counts["change_pp"] == -2.5, counts
    assert counts["direction"] == "下降", counts
    assert counts["high_pledge"] is False, counts
    assert result.data["has_pledges"] is True, result.data
    assert result.data["notes"] == ["质押比例不等于违约"], result.data


def test_segments_region_missing() -> None:
    global REGION
    REGION = False
    rows = [{"code": "605499", "fetched_at": "2026-10-24 15:57:57"}]
    originals = (tools.ensure_segments, tools.segments_from_rows, tools.get_stock)
    tools.ensure_segments = lambda code, **kw: {"rows": rows, "stale": False}  # type: ignore[assignment]
    tools.segments_from_rows = fake_segments  # type: ignore[assignment]
    tools.get_stock = lambda code: Stock(code, "东鹏饮料")  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_segments", {}, default_company="605499")
    finally:
        REGION = True
        tools.ensure_segments, tools.segments_from_rows, tools.get_stock = originals  # type: ignore[assignment]
    assert [source.id.split(":")[-1] for source in result.sources] == ["product"], result.sources


def test_announcements_sources() -> None:
    original = tools.build_announcements
    captured = {}

    def fake_announcements(code: str, **kwargs) -> dict:
        captured.update(kwargs)
        return {
            "available": True, "code": code, "name": "东鹏饮料",
            "days": kwargs.get("days", 180), "count": 2,
            "items": [
                {"date": "2026-10-10", "title": "关于回购的公告", "tags": ["回购"],
                 "level": "info", "url": "https://example.invalid/a1",
                 "art_code": "AN1"},
                {"date": "2026-09-01", "title": "关于减持的公告", "tags": ["减持"],
                 "level": "warning", "url": "https://example.invalid/a2",
                 "art_code": "AN2"},
            ],
        }

    tools.build_announcements = fake_announcements  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "get_announcements", {"days": 90}, default_company="605499"
        )
    finally:
        tools.build_announcements = original  # type: ignore[assignment]

    assert captured["days"] == 90, captured
    assert [source.id for source in result.sources] == [
        "announcement:605499:2026-10-10:AN1",
        "announcement:605499:2026-09-01:AN2",
    ], result.sources
    assert result.sources[0].url == "https://example.invalid/a1"
    assert result.data["items"][1]["level"] == "warning"


def test_valuation() -> None:
    # 用真实 valuation_from_rows 产出喂给工具：分位是 0~100 的百分数
    history = [
        {"code": "605499", "name": "东鹏饮料", "trade_date": "2026-10-20",
         "pe_ttm": 40.0, "pb_mrq": 4.0, "close": 95.0, "total_mv": 7.0e10,
         "fetched_at": "2026-10-24 16:27:38"},
        {"code": "605499", "name": "东鹏饮料", "trade_date": "2026-10-21",
         "pe_ttm": 10.0, "pb_mrq": 1.0, "close": 99.0, "total_mv": 7.2e10,
         "fetched_at": "2026-10-24 16:27:38"},
        {"code": "605499", "name": "东鹏饮料", "trade_date": "2026-10-22",
         "pe_ttm": 20.0, "pb_mrq": 2.0, "close": 104.0, "total_mv": 7.6e10,
         "fetched_at": "2026-10-24 16:27:38"},
        {"code": "605499", "name": "东鹏饮料", "trade_date": "2026-10-23",
         "pe_ttm": 30.0, "pb_mrq": 3.0, "close": 104.48, "total_mv": 76709143908.8,
         "fetched_at": "2026-10-24 16:27:38"},
    ]
    reference = valuation_from_rows(history, name="东鹏饮料")
    expected = reference["valuation"]["pe_ttm"]["percentile"]
    assert expected == 75.0, reference["valuation"]

    original = tools.build_valuation
    tools.build_valuation = lambda code, **kwargs: valuation_from_rows(
        history, name="东鹏饮料"
    )  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_valuation", {}, default_company="605499")
    finally:
        tools.build_valuation = original  # type: ignore[assignment]
    assert result.ok is True
    assert result.data["pe_ttm"] == 30.0
    assert result.data["pe_percentile"] == expected, result.data
    assert result.data["pb_percentile"] == expected, result.data
    assert result.sources[0].id == "valuation:605499:2026-10-23"


def test_calc_valuation_check() -> None:
    originals = (tools.build_valuation, tools.ensure_financials)
    tools.build_valuation = fake_valuation  # type: ignore[assignment]
    tools.ensure_financials = lambda code: fake_financial_rows()  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "calc_valuation", {"mode": "check"}, default_company="605499"
        )
    finally:
        tools.build_valuation, tools.ensure_financials = originals  # type: ignore[assignment]

    assert result.ok is True, result
    assert result.data["market_cap_yi"] == 370.0, result.data
    assert result.data["financials"]["ttm_net_profit_yi"] == 37.0, result.data
    assert result.data["financials"]["equity_parent_yi"] == 80.0, result.data
    assert result.data["financials"]["ttm_fcf_yi"] == 32.0, result.data
    assert result.data["checks"][0]["name"] == "PE(TTM)"
    assert result.data["checks"][0]["status"] == "warn", result.data["checks"]
    assert result.data["checks"][1]["status"] == "pass", result.data["checks"]
    assert result.data["fcf_yield_pct"] == 8.65, result.data
    assert any("PE(TTM)" in item for item in result.data["warnings"]), result.data
    assert [source.id for source in result.sources] == [
        "valuation:605499:2026-10-23",
        "finance:605499:2026-06-30",
    ], result.sources


def test_calc_valuation_check_edge_cases() -> None:
    originals = (tools.build_valuation, tools.ensure_financials)
    try:
        # 亏损：PE 不适用，PB 仍可校验
        loss_rows = fake_financial_rows()
        # TTM = -2.0 + 3.5 - 1.8 = -0.3e9，确保是负利润
        loss_rows[-1]["net_profit_attributable"] = -2.0e9
        tools.build_valuation = fake_valuation  # type: ignore[assignment]
        tools.ensure_financials = lambda code: loss_rows  # type: ignore[assignment]
        loss = tools.run_tool(
            "calc_valuation", {"mode": "check"}, default_company="605499"
        )
        assert loss.ok is True, loss
        assert loss.data["checks"][0]["status"] == "skip", loss.data["checks"]
        assert any("净利润为负" in item for item in loss.data["warnings"]), loss.data

        # 金融机构：自由现金流收益率口径不可比
        bank_rows = fake_financial_rows()
        for row in bank_rows:
            row["cost_of_revenue"] = None
        tools.ensure_financials = lambda code: bank_rows  # type: ignore[assignment]
        bank = tools.run_tool(
            "calc_valuation", {"mode": "check"}, default_company="600036"
        )
        assert bank.ok is True, bank
        assert bank.data["financials"]["is_financial"] is True, bank.data
        assert bank.data["fcf_yield_pct"] is None, bank.data
        assert any("金融机构" in item for item in bank.data["warnings"]), bank.data

        # 没有估值或财报时给出可读的失败原因
        tools.build_valuation = lambda code, **kwargs: {"available": False}  # type: ignore[assignment]
        missing_valuation = tools.run_tool(
            "calc_valuation", {"mode": "check"}, default_company="605499"
        )
        assert missing_valuation.ok is False, missing_valuation
        assert "没有估值数据" in missing_valuation.error, missing_valuation

        tools.build_valuation = fake_valuation  # type: ignore[assignment]
        tools.ensure_financials = lambda code: []  # type: ignore[assignment]
        missing_finance = tools.run_tool(
            "calc_valuation", {"mode": "check"}, default_company="605499"
        )
        assert missing_finance.ok is False, missing_finance
        assert "财报" in missing_finance.error, missing_finance
    finally:
        tools.build_valuation, tools.ensure_financials = originals  # type: ignore[assignment]


def test_calc_valuation_scenario() -> None:
    original = tools.build_valuation
    tools.build_valuation = fake_valuation  # type: ignore[assignment]
    try:
        result = tools.run_tool(
            "calc_valuation",
            {
                "mode": "scenario",
                "base_value": 3.0,
                "growth_pct": [0, 10, 20],
                "exit_pe": [12, 15, 18],
            },
            default_company="605499",
        )
    finally:
        tools.build_valuation = original  # type: ignore[assignment]

    assert result.ok is True, result
    scenarios = result.data["scenarios"]
    assert [item["label"] for item in scenarios] == ["悲观", "中性", "乐观"]
    assert scenarios[0]["value_per_share"] == 36.0, scenarios
    assert scenarios[0]["upside_pct"] == -51.4, scenarios
    assert abs(scenarios[1]["value_per_share"] - 59.9) < 0.01, scenarios
    assert scenarios[1]["upside_pct"] == -19.1, scenarios
    assert scenarios[2]["value_per_share"] == 93.31, scenarios
    assert scenarios[2]["upside_pct"] == 26.1, scenarios
    assert result.data["assumptions"] == {
        "base_value": 3.0,
        "base_label": "EPS",
        "years": 3,
        "growth_pct": [0.0, 10.0, 20.0],
        "exit_pe": [12.0, 15.0, 18.0],
        "price": 74.0,
    }, result.data["assumptions"]
    assert result.sources[0].id == "valuation:605499:2026-10-23", result.sources
    assert "不是目标价" in result.data["notes"][0], result.data

    # 显式股价优先，且不再依赖估值数据
    override = tools.run_tool(
        "calc_valuation",
        {
            "mode": "scenario",
            "price": 50,
            "base_value": 3,
            "growth_pct": [0, 10, 20],
            "exit_pe": [12, 15, 18],
        },
        default_company="605499",
    )
    assert override.ok is True, override
    assert override.data["assumptions"]["price"] == 50.0, override.data
    assert override.sources == (), override.sources

    # 缺参数、基准非正、年数越界都折成观察结果
    for arguments, phrase in (
        ({"mode": "scenario"}, "base_value"),
        ({"mode": "scenario", "base_value": 3, "growth_pct": [1, 2],
          "exit_pe": [1, 2, 3]}, "growth_pct"),
        ({"mode": "scenario", "base_value": 0, "growth_pct": [1, 2, 3],
          "exit_pe": [1, 2, 3]}, "基准每股价值"),
        ({"mode": "scenario", "base_value": 3, "growth_pct": [1, 2, 3],
          "exit_pe": [1, 2, 3], "years": 0}, "1–10"),
        ({"mode": "unknown"}, "未知 mode"),
    ):
        broken = tools.run_tool(
            "calc_valuation", arguments, default_company="605499"
        )
        assert broken.ok is False, (arguments, broken)
        assert phrase in broken.error, (arguments, broken.error)


def test_calc_valuation_terminal() -> None:
    normal = tools.run_tool(
        "calc_valuation",
        {"mode": "terminal", "roic_pct": 20,
         "terminal_g_pct": 2, "discount_rate_pct": 8},
        default_company="605499",
    )
    assert normal.ok is True, normal
    assert normal.data["terminal_pe"] == 15.0, normal.data
    assert normal.data["payout_ratio_pct"] == 90.0, normal.data
    assert normal.data["valid"] is True and normal.data["status"] == "pass", normal.data

    narrow = tools.run_tool(
        "calc_valuation",
        {"mode": "terminal", "roic_pct": 20,
         "terminal_g_pct": 2, "discount_rate_pct": 6},
        default_company="605499",
    )
    assert narrow.data["terminal_pe"] == 22.5, narrow.data
    assert narrow.data["status"] == "warn", narrow.data
    assert any("r-g" in item for item in narrow.data["warnings"]), narrow.data

    broken = tools.run_tool(
        "calc_valuation",
        {"mode": "terminal", "roic_pct": 2,
         "terminal_g_pct": 2, "discount_rate_pct": 8},
        default_company="605499",
    )
    assert broken.ok is True, broken
    assert broken.data["valid"] is False, broken.data
    assert broken.data["terminal_pe"] is None, broken.data
    assert broken.data["status"] == "fail", broken.data

    missing = tools.run_tool(
        "calc_valuation",
        {"mode": "terminal", "terminal_g_pct": 2, "discount_rate_pct": 8},
        default_company="605499",
    )
    assert missing.ok is False and "roic_pct" in missing.error, missing


def test_segments_kind_fallback_not_relabeled() -> None:
    """只有产品口径时，地区切片不能被回退出来的产品数据冒充。"""
    rows = [
        {"code": "605499", "report_date": "2026-06-30", "kind": "2",
         "item_name": "能量饮料", "income": 8936615605.76, "share": 71.82,
         "gross_margin": 54.67, "fetched_at": "2026-10-24 15:57:57"},
        {"code": "605499", "report_date": "2026-06-30", "kind": "2",
         "item_name": "其他饮料", "income": 3506420950.0, "share": 28.18,
         "gross_margin": 30.1, "fetched_at": "2026-10-24 15:57:57"},
    ]
    originals = (tools.ensure_segments, tools.get_stock)
    tools.ensure_segments = lambda code, **kw: {"rows": rows, "stale": False}  # type: ignore[assignment]
    tools.get_stock = lambda code: Stock(code, "东鹏饮料")  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_segments", {}, default_company="605499")
    finally:
        tools.ensure_segments, tools.get_stock = originals  # type: ignore[assignment]

    assert result.ok is True, result
    assert [slice_["kind"] for slice_ in result.data["slices"]] == ["product"], result.data
    assert [source.id for source in result.sources] == [
        "segments:605499:2026-06-30:product",
    ], result.sources


def test_malformed_arguments_are_observations() -> None:
    # schema 说是 string，模型偶尔给数字：能认出来就照常解析
    assert tools.resolve_code(600519, None) == ("600519", None, [])

    # 其他类型不能把循环炸掉，要变成给模型的观察结果
    result = tools.run_tool(
        "get_finance", {"company": ["600519"]}, default_company="605499"
    )
    assert result.ok is False and "必须是字符串" in result.error, result

    # arguments 本身不是对象时同样按空参数处理
    result = tools.run_tool("resolve_company", ["not", "a", "dict"])
    assert result.ok is False and result.error, result


def test_tool_error_is_observation() -> None:
    original = tools.build_snapshot

    def broken(code: str, **kwargs):
        raise RuntimeError("没有财报数据")

    tools.build_snapshot = broken  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_finance", {}, default_company="000001")
    finally:
        tools.build_snapshot = original  # type: ignore[assignment]
    assert result.ok is False
    assert result.company == "000001" and "没有财报" in result.error


def main() -> None:
    test_schemas()
    test_resolve_company()
    test_finance_units_and_sources()
    test_finance_without_safety()
    test_finance_without_detail()
    test_load_skill_tool()
    test_financial_history_tool()
    test_financial_history_missing()
    test_profile_tool()
    test_segments_two_slices()
    test_management_tool()
    test_shareholders_tool()
    test_peers_tool()
    test_dividends_tool()
    test_insider_trades_tool()
    test_buybacks_tool()
    test_lockup_expiries_tool()
    test_pledges_tool()
    test_segments_region_missing()
    test_announcements_sources()
    test_valuation()
    test_calc_valuation_check()
    test_calc_valuation_check_edge_cases()
    test_calc_valuation_scenario()
    test_calc_valuation_terminal()
    test_segments_kind_fallback_not_relabeled()
    test_malformed_arguments_are_observations()
    test_tool_error_is_observation()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
