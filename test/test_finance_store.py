"""财报入库链路验证：字段映射、覆盖写入、合并读取、数据源改列名报警。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import stock.finance_service as fs

PERIODS = {
    "20241231": {
        "balance": {
            "cash": 80.0, "accounts_receivable": 60.0, "inventory": 180.0,
            "current_assets_total": 500.0, "total_assets": 1000.0,
            "current_liabilities_total": 250.0, "total_liabilities": 500.0,
            "equity_parent": 500.0, "total_equity": 500.0,
        },
        "income": {
            "total_revenue": 520.0, "revenue": 520.0, "cost_of_revenue": 300.0,
            "net_profit": 52.0, "net_profit_attributable": 52.0,
        },
        "cashflow": {
            "cash_from_sales": 560.0, "operating_cash_flow": 60.0, "capex": 30.0,
        },
    },
    "20250331": {
        "balance": {"cash": 90.0, "total_assets": 1050.0},
        "income": {
            "total_revenue": 120.0, "revenue": 120.0, "cost_of_revenue": 65.0,
            "net_profit": 12.0, "net_profit_attributable": 12.0,
        },
        "cashflow": {
            "cash_from_sales": 130.0, "operating_cash_flow": 10.0, "capex": 5.0,
        },
    },
    "20250630": {
        "balance": {
            "cash": 100.0, "accounts_receivable": 80.0, "inventory": 200.0,
            "current_assets_total": 600.0, "total_assets": 1100.0,
            "current_liabilities_total": 300.0, "total_liabilities": 540.0,
            "equity_parent": 560.0, "total_equity": 560.0,
            "short_term_borrowings": 50.0,
        },
        "income": {
            "total_revenue": 270.0, "revenue": 270.0, "cost_of_revenue": 150.0,
            "net_profit": 27.0, "net_profit_attributable": 27.0, "rd_expense": 10.0,
        },
        "cashflow": {
            "cash_from_sales": 300.0, "operating_cash_flow": 30.0, "capex": 20.0,
        },
    },
}


def fake_frame(spec, rows: dict) -> pd.DataFrame:
    """按数据源的列名构造宽表，用于离线测试。"""
    columns = ["报告日"] + [aliases[0] for aliases in spec.fields.values()]
    records = []
    for report_date, fields in rows.items():
        record = {column: None for column in columns}
        record["报告日"] = report_date
        for field, value in fields.items():
            record[spec.fields[field][0]] = value
        records.append(record)
    return pd.DataFrame(records, columns=columns)


def fake_statement(code, spec) -> pd.DataFrame:
    rows = {date: values[spec.key] for date, values in PERIODS.items()}
    return fake_frame(spec, rows)


def main() -> None:
    db_path = os.path.join(tempfile.mkdtemp(), "finance_test.db")
    fs.DB_PATH = db_path
    fs.fetch_statement = fake_statement  # type: ignore[assignment]

    counts = fs.sync_statements("SH605499")
    assert counts == {"balance": 3, "income": 3, "cashflow": 3}, counts

    rows = fs.get_financials("605499")
    assert len(rows) == 3, len(rows)
    assert rows[0]["report_date"] == "2024-12-31", rows[0]["report_date"]
    latest = rows[-1]

    assert latest["revenue"] == 270.0, latest["revenue"]
    assert latest["gross_margin"] is not None
    assert latest["total_assets"] == 1100.0, latest["total_assets"]
    assert latest["operating_cash_flow"] == 30.0, latest["operating_cash_flow"]

    # 单季差分：2025Q2 = 半年报 270 - 一季报 120
    assert latest["revenue_sq"] == 150.0, latest["revenue_sq"]
    assert latest["net_profit_sq"] == 15.0, latest["net_profit_sq"]

    # 重复同步必须幂等，不能产生重复行
    fs.sync_statements("605499")
    assert len(fs.get_financials("605499")) == 3

    # 单表读取和带前缀的代码
    income = fs.get_statement("sh605499", "income")
    assert len(income) == 3 and income[-1]["net_profit"] == 27.0
    assert fs.synced_codes() == ["605499"], fs.synced_codes()

    # 数据源改列名时必须报错，而不是静默写空
    original = fs.fetch_statement

    def broken(code, spec):
        df = fake_statement(code, spec)
        if spec.key == "income":
            df = df.rename(columns={"营业总收入": "营业收入合计"})
            df = df.drop(columns=["营业收入"])
        return df

    fs.fetch_statement = broken  # type: ignore[assignment]
    try:
        fs.sync_statements("605499")
    except fs.FinanceError as exc:
        assert "缺少字段" in str(exc), exc
    else:
        raise AssertionError("列名变化没有报警")
    fs.fetch_statement = original  # type: ignore[assignment]

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
