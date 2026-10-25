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
        "get_financial_history", "get_segments", "get_announcements",
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


def fake_segments(rows, kind=None, code=None, name=None, stale=False) -> dict:
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
