"""按需读取验证：缓存新鲜时不抓、过期或漏报告时自动补抓。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import stock.disclosure_service as ds
import stock.finance_service as fs

CODE = "605499"

PERIODS = {
    "20250331": {
        "balance": {"cash": 90.0, "total_assets": 1050.0},
        "income": {
            "total_revenue": 120.0, "revenue": 120.0,
            "net_profit": 12.0, "net_profit_attributable": 12.0,
        },
        "cashflow": {"operating_cash_flow": 10.0, "capex": 5.0},
    },
    "20250630": {
        "balance": {"cash": 100.0, "total_assets": 1100.0},
        "income": {
            "total_revenue": 270.0, "revenue": 270.0,
            "net_profit": 27.0, "net_profit_attributable": 27.0,
        },
        "cashflow": {"operating_cash_flow": 30.0, "capex": 20.0},
    },
    "20250930": {
        "balance": {"cash": 110.0, "total_assets": 1200.0},
        "income": {
            "total_revenue": 430.0, "revenue": 430.0,
            "net_profit": 43.0, "net_profit_attributable": 43.0,
        },
        "cashflow": {"operating_cash_flow": 45.0, "capex": 25.0},
    },
}

# 数据源当前能拿到哪些报告期，用来模拟"已披露但接口还没更新"
available = {"20250331", "20250630"}
calls = {"count": 0}


def fake_frame(spec, rows: dict) -> pd.DataFrame:
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
    calls["count"] += 1
    rows = {
        date: values[spec.key]
        for date, values in PERIODS.items()
        if date in available
    }
    return fake_frame(spec, rows)


def publish(code: str, period: str, actual: str) -> None:
    """把某只股票某个报告期标记为已实际披露。"""
    frame = pd.DataFrame([{
        "股票代码": code,
        "股票简称": "测试股份",
        "首次预约时间": pd.Timestamp(actual),
        "一次变更日期": pd.NaT,
        "二次变更日期": pd.NaT,
        "三次变更日期": pd.NaT,
        "实际披露时间": pd.Timestamp(actual),
    }])
    ds._fetch_calendar = lambda _period: frame  # type: ignore[assignment]
    ds.refresh_period(period)


def age_cache(db_path: str, minutes: int) -> None:
    """把本地缓存的时间戳往前拨，模拟数据放久了。"""
    stamp = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute("update fin_cashflow set fetched_at = ? where code = ?", (stamp, CODE))


def main() -> None:
    db_path = os.path.join(tempfile.mkdtemp(), "ensure_test.db")
    fs.DB_PATH = db_path
    fs.fetch_statement = fake_statement  # type: ignore[assignment]
    ds.DB_PATH = db_path
    ds._fetch_calendar = lambda _period: pd.DataFrame()  # type: ignore[assignment]

    # -- 本地没有数据：抓一次 -------------------------------------------------
    history = fs.ensure_financials(CODE)
    assert calls["count"] == 3, calls
    assert [row["report_date"] for row in history] == ["2025-03-31", "2025-06-30"]
    assert history[-1]["revenue"] == 270.0

    # -- 刚刚同步过：直接用缓存 -----------------------------------------------
    fs.ensure_financials("SH605499")
    assert calls["count"] == 3, calls

    # -- 日历说报告已披露，但刚同步过：先不重试 -------------------------------
    publish(CODE, "2025-09-30", "2025-10-28")
    history = fs.ensure_financials(CODE)
    assert calls["count"] == 3, calls
    assert ds.latest_disclosed_period(CODE) == "2025-09-30"

    # -- 隔了足够久：重试；数据源还没更新，结果不变 ---------------------------
    age_cache(db_path, 120)
    history = fs.ensure_financials(CODE)
    assert calls["count"] == 6, calls
    assert len(history) == 2, history

    # -- 数据源跟上后：新报告期自动补进来 -------------------------------------
    available.add("20250930")
    age_cache(db_path, 120)
    history = fs.ensure_financials(CODE)
    assert calls["count"] == 9, calls
    assert history[-1]["report_date"] == "2025-09-30", history[-1]
    assert history[-1]["revenue"] == 430.0
    # 三季报单季 = 累计 430 - 半年报 270
    assert history[-1]["revenue_sq"] == 160.0, history[-1]["revenue_sq"]

    # -- 缓存整体过期（超过 max_age_days）-------------------------------------
    age_cache(db_path, 60 * 24 * 30)
    fs.ensure_financials(CODE)
    assert calls["count"] == 12, calls

    # -- force 无视缓存 -------------------------------------------------------
    fs.ensure_financials(CODE, force=True)
    assert calls["count"] == 15, calls

    # -- 非法代码 -------------------------------------------------------------
    try:
        fs.ensure_financials("")
    except fs.FinanceError:
        pass
    else:
        raise AssertionError("空代码没有报错")

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
