"""年度财务序列验证：只取年报、窗口截取、比率口径与缺口说明。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.stock import Stock

import stock.finance_history as fh
from stock.finance_history import build_history, history_from_rows


def test_history_from_rows() -> None:
    rows = [
        {"report_date": "2023-12-31", "revenue": 80e8, "gross_margin": 40.0},
        {"report_date": "2024-12-31", "revenue": 100e8, "revenue_yoy": 25.0,
         "net_profit": 20e8, "gross_margin": 45.0, "net_margin": 20.0,
         "roe": 30.0, "selling_expense": 15e8, "admin_expense": 5e8,
         "rd_expense": 1e8, "ocf_to_net_profit": 1.2, "debt_ratio": 40.0,
         "fetched_at": "2026-10-24 15:00:00"},
        {"report_date": "2025-12-31", "revenue": 120e8, "revenue_yoy": 20.0,
         "net_profit": 25e8, "gross_margin": 46.0, "net_margin": 20.8,
         "roe": 32.0, "selling_expense": 16e8, "admin_expense": 6e8,
         "rd_expense": 1.2e8, "ocf_to_net_profit": 1.1, "debt_ratio": 42.0,
         "operating_cash_flow": 30e8, "capex": 8e8, "free_cash_flow": 22e8,
         "fetched_at": "2026-10-24 15:00:00"},
        {"report_date": "2026-03-31", "revenue": 35e8, "gross_margin": 47.0},
    ]
    data = history_from_rows(rows, years=2)
    assert [row["period"] for row in data["years"]] == ["2024-12-31", "2025-12-31"]
    latest = data["years"][-1]
    assert latest["revenue_yi"] == 120.0
    assert latest["selling_ratio"] == 13.3   # 16/120*100，保留一位
    assert latest["admin_ratio"] == 5.0
    assert latest["rd_ratio"] == 1.0
    assert latest["operating_cash_flow_yi"] == 30.0, latest
    assert latest["capex_yi"] == 8.0, latest
    assert latest["free_cash_flow_yi"] == 22.0, latest
    assert data["span"] == "2024-2025"
    assert data["as_of"] == "2025-12-31"
    assert data["fetched_at"] == "2026-10-24 15:00:00"


def test_history_edge_cases() -> None:
    assert history_from_rows([], years=10)["available"] is False
    quarterly = [{"report_date": "2026-06-30", "revenue": 1e8}]
    assert history_from_rows(quarterly)["available"] is False

    zero = [{"report_date": "2025-12-31", "revenue": 0,
             "selling_expense": 1e8, "gross_margin": None}]
    row = history_from_rows(zero)["years"][0]
    assert row["selling_ratio"] is None and row["gross_margin"] is None

    # 费用为负（科目冲回）或收入为负时，不能算出负费用率冒充真实比率
    negative = [{"report_date": "2025-12-31", "revenue": 100e8,
                 "cost_of_revenue": 60e8, "gross_margin": 40.0,
                 "selling_expense": -5e8, "admin_expense": -1e8}]
    row = history_from_rows(negative)["years"][0]
    assert row["selling_ratio"] is None, row
    assert row["admin_ratio"] is None, row
    row = history_from_rows([{**negative[0], "revenue": -100e8,
                              "selling_expense": 5e8}])["years"][0]
    assert row["selling_ratio"] is None, row

    # 自由现金流为负是真实经营状态，要保留负号，不能像费用率那样留空
    burning = [{"report_date": "2025-12-31", "revenue": 100e8,
                "cost_of_revenue": 60e8, "gross_margin": 40.0,
                "operating_cash_flow": 3e8, "capex": 9e8,
                "free_cash_flow": -6e8}]
    row = history_from_rows(burning)["years"][0]
    assert row["free_cash_flow_yi"] == -6.0, row
    assert row["capex_yi"] == 9.0, row

    # 中间缺一年要写进 notes，不能悄悄压缩成连续四年
    gappy = history_from_rows([
        {"report_date": "2022-12-31", "revenue": 1e8},
        {"report_date": "2024-12-31", "revenue": 2e8},
    ], years=10)
    assert gappy["span"] == "2022-2024"
    assert len(gappy["years"]) == 2
    assert any("2023" in note for note in gappy["notes"]), gappy["notes"]
    assert any("只有 2 年" in note for note in gappy["notes"]), gappy["notes"]


def test_build_history_metadata() -> None:
    rows = [
        {"code": "605499", "report_date": f"{year}-12-31", "revenue": 1e8,
         "fetched_at": "2026-10-24 15:00:00"}
        for year in (2023, 2024, 2025)
    ]
    originals = (fh.ensure_financials, fh.get_stock)
    fh.ensure_financials = lambda code, **kwargs: rows  # type: ignore[assignment]
    fh.get_stock = lambda code: Stock(code, "东鹏饮料")  # type: ignore[assignment]
    try:
        data = build_history("605499", years=2)
    finally:
        fh.ensure_financials, fh.get_stock = originals  # type: ignore[assignment]
    assert data["available"] is True
    assert data["code"] == "605499" and data["name"] == "东鹏饮料"
    assert data["span"] == "2024-2025"


def main() -> None:
    test_history_from_rows()
    test_history_edge_cases()
    test_build_history_metadata()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
