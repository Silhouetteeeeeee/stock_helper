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
    original = tools.build_valuation
    tools.build_valuation = lambda code, **kwargs: {
        "available": True, "code": code, "name": "东鹏饮料",
        "as_of": "2026-10-23", "fetched_at": "2026-10-24 16:27:38",
        "price": {"close": 104.48, "market_cap": 76709143908.8},
        "valuation": {
            "pe_ttm": {"value": 15.63, "percentile": 0.2475, "years": 5},
            "pb_mrq": {"value": 3.94, "percentile": 0.2475, "years": 5},
        },
        "industry": {"board": "饮料乳品", "count": 28, "median_pe": 24.0,
                     "rank_pe": 4, "rank_total": 21},
    }  # type: ignore[assignment]
    try:
        result = tools.run_tool("get_valuation", {}, default_company="605499")
    finally:
        tools.build_valuation = original  # type: ignore[assignment]
    assert result.ok is True
    assert result.data["pe_ttm"] == 15.63
    assert result.data["pe_percentile"] == 24.8, result.data
    assert result.sources[0].id == "valuation:605499:2026-10-23"


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
    test_profile_tool()
    test_segments_two_slices()
    test_segments_region_missing()
    test_announcements_sources()
    test_valuation()
    test_tool_error_is_observation()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
