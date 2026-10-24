"""研究工具验证：schema、公司解析、概况/财务工具、来源与单位。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from common.stock import Stock


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
        },
        "trend": [
            {"report_date": "2026-03-31", "revenue_sq": 4848071704.63,
             "revenue_sq_yoy": 39.2, "gross_margin": 44.5, "net_margin": 20.2},
            {"report_date": "2026-06-30", "revenue_sq": 6554787693.92,
             "revenue_sq_yoy": 11.3, "gross_margin": 45.2, "net_margin": 22.3},
        ],
        "sources": {"statement": "新浪财经"},
    }


def test_schemas() -> None:
    names = [item["function"]["name"] for item in tools.schemas()]
    assert names == [
        "resolve_company", "get_company_profile", "get_finance",
        "get_segments", "get_announcements", "get_valuation",
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
    source = result.sources[0]
    assert source.id == "finance:605499:2026-06-30"
    assert source.as_of == "2026-06-30"
    assert source.fetched_at == "2026-10-24 15:57:57"
    assert "finance:605499:2026-06-30" in result.to_message()

    other = tools.run_tool("get_finance", {"company": "600519"}, default_company="605499")
    assert other.company == "600519", other
    assert other.sources[0].id.startswith("finance:600519:"), other.sources


def test_profile_tool() -> None:
    original = tools.build_profile
    tools.build_profile = lambda code, **kwargs: {
        "available": True, "code": code, "name": "东鹏饮料",
        "industry": "食品饮料", "profile": "能量饮料",
        "scope": "食品生产…", "employees": 12376,
        "as_of": "2026-10-24",
        "sources": {"东方财富 F10 公司概况": "2026-10-24"},
    }  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_company_profile", {}, default_company="605499")
    finally:
        tools.build_profile = original  # type: ignore[assignment]
    assert result.ok is True
    assert result.sources[0].id == "profile:605499"
    assert result.data["employees"] == 12376


def main() -> None:
    test_schemas()
    test_resolve_company()
    test_finance_units_and_sources()
    test_profile_tool()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
