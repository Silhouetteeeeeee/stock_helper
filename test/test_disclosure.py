"""披露日历与待披露队列验证。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import stock.disclosure_service as ds

PERIOD = "2026-09-30"
TODAY = date(2026, 10, 24)


def calendar(extra_actual: dict[str, str] | None = None) -> pd.DataFrame:
    """招商银行默认已披露；extra_actual 用于模拟其它股票在当天发布。"""
    actual = {"600036": "2026-10-24"}
    actual.update(extra_actual or {})

    records = [
        ("605499", "东鹏饮料", "2026-10-30", None, None),
        ("600036", "招商银行", "2026-10-24", None, None),
        ("601318", "中国平安", "2026-11-20", None, None),
        ("601899", "紫金矿业", "2026-10-20", "2026-10-22", None),
    ]
    return pd.DataFrame([
        {
            "股票代码": code,
            "股票简称": name,
            "首次预约时间": pd.Timestamp(first),
            "一次变更日期": pd.Timestamp(change_1) if change_1 else pd.NaT,
            "二次变更日期": pd.Timestamp(change_2) if change_2 else pd.NaT,
            "三次变更日期": pd.NaT,
            "实际披露时间": pd.Timestamp(actual[code]) if code in actual else pd.NaT,
        }
        for code, name, first, change_1, change_2 in records
    ])


def main() -> None:
    ds.DB_PATH = os.path.join(tempfile.mkdtemp(), "disclosure_test.db")
    ds._fetch_calendar = lambda period: calendar()  # type: ignore[assignment]

    assert ds.disclosure_periods(TODAY) == [PERIOD], ds.disclosure_periods(TODAY)
    assert ds.disclosure_periods(date(2026, 4, 15)) == ["2025-12-31", "2026-03-31"]

    assert ds.refresh_period(PERIOD) == 4
    assert ds.get_disclosure("SH605499", PERIOD)["first_scheduled"] == "2026-10-30"

    # 未来 7 天内 + 逾期未披露：东鹏和紫金
    pending = ds.pending_disclosures(today=TODAY, days=7, lookback_days=7)
    assert [item["code"] for item in pending] == ["601899", "605499"], pending

    # 只看指定股票
    only = ds.pending_disclosures(["605499"], today=TODAY)
    assert [item["code"] for item in only] == ["605499"], only

    # 中国平安 11-20 在 7 天窗口外
    assert all(item["code"] != "601318" for item in pending)

    # 预约日已到但未披露的只有紫金（东鹏 10-30 还没到）
    assert [item["code"] for item in ds.due_today(today=TODAY)] == ["601899"]

    # 已披露的最新报告期：招商银行有、东鹏还没有
    assert ds.latest_disclosed_period("600036") == PERIOD
    assert ds.latest_disclosed_period("605499") is None
    assert ds.latest_disclosed_period("600036", periods=["2026-09-30"]) == PERIOD
    assert ds.latest_disclosed_period("600036", periods=["2026-06-30"]) is None

    # 招商银行已实际披露，但本地没有财报数据 → 待同步
    needs_sync = ds.pending_sync(periods=[PERIOD])
    assert [item["code"] for item in needs_sync] == ["600036"], needs_sync

    # 补上招商银行的财报后，待同步队列应清空
    with sqlite3.connect(ds.DB_PATH) as conn:
        conn.execute("create table fin_income (code text, report_date text)")
        conn.execute("insert into fin_income values ('600036', ?)", (PERIOD,))
    assert ds.pending_sync(periods=[PERIOD]) == []

    # 东鹏在披露日当天实际发布后：待披露队列移除，进入待同步
    ds._fetch_calendar = lambda period: calendar({"605499": "2026-10-30"})  # type: ignore[assignment]
    ds.refresh_period(PERIOD)
    codes = [item["code"] for item in ds.pending_disclosures(today=TODAY)]
    assert codes == ["601899"], codes
    synced = [item["code"] for item in ds.pending_sync(periods=[PERIOD])]
    assert synced == ["605499"], synced

    # 报告期必须合法
    try:
        ds.refresh_period("2026-10-25")
    except ds.DisclosureError:
        pass
    else:
        raise AssertionError("非法报告期没有报错")

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
