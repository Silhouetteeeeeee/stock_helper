"""股权质押验证：最新质押比例、一年变化、峰值与本地缓存。

接口返回的 ``REPURCHASE_BALANCE`` 单位是万股，``PLEDGE_MARKET_CAP``
单位是万元，``PLEDGE_RATIO`` 已经是百分数。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.pledges as pledge

CODE = "002230"
NAME = "科大讯飞"
TODAY = "2026-10-25"


def _row(
    trade_date: str,
    ratio: float | None,
    *,
    balance_wan: float | None = None,
    market_cap_wan: float | None = None,
    deals: int | None = None,
    unlimited_wan: float | None = None,
    limited_wan: float | None = None,
    industry: str = "软件开发",
    y1: float | None = None,
) -> dict:
    return {
        "SECURITY_CODE": CODE,
        "SECURITY_NAME_ABBR": NAME,
        "TRADE_DATE": f"{trade_date} 00:00:00",
        "PLEDGE_RATIO": ratio,
        "REPURCHASE_BALANCE": balance_wan,
        "PLEDGE_MARKET_CAP": market_cap_wan,
        "PLEDGE_DEAL_NUM": deals,
        "REPURCHASE_UNLIMITED_BALANCE": unlimited_wan,
        "REPURCHASE_LIMITED_BALANCE": limited_wan,
        "INDUSTRY": industry,
        "Y1_CLOSE_ADJCHRATE": y1,
    }


ROWS = [
    _row("2026-10-23", 28.5, balance_wan=100000.0, market_cap_wan=500000.0,
         deals=5, unlimited_wan=100000.0, limited_wan=0.0, y1=-10.0),
    _row("2026-09-25", 30.0, balance_wan=105000.0, market_cap_wan=520000.0,
         deals=6, unlimited_wan=105000.0, limited_wan=0.0),
    _row("2026-01-30", 35.0, balance_wan=120000.0, market_cap_wan=600000.0,
         deals=7, unlimited_wan=120000.0, limited_wan=0.0),
    _row("2025-12-26", 32.0, balance_wan=115000.0, market_cap_wan=580000.0,
         deals=4, unlimited_wan=115000.0, limited_wan=0.0),
    _row("2025-10-24", 31.0, balance_wan=110000.0, market_cap_wan=560000.0,
         deals=3, unlimited_wan=110000.0, limited_wan=0.0),
    _row("2024-06-28", 40.0, balance_wan=130000.0, market_cap_wan=700000.0,
         deals=9, unlimited_wan=130000.0, limited_wan=0.0),
    # 三年窗口外
    _row("2023-12-29", 20.0, balance_wan=80000.0, market_cap_wan=400000.0,
         deals=2, unlimited_wan=80000.0, limited_wan=0.0),
]


def test_from_rows() -> None:
    data = pledge.pledges_from_rows(ROWS, CODE, years=3, today=TODAY)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-10-23", data
    assert data["window_start"] == "2024-01-01", data
    assert data["window_end"] == "2026-10-25", data
    assert data["years"] == 3, data
    assert data["has_pledges"] is True, data

    counts = data["counts"]
    assert counts == {
        "points": 6,
        "latest_date": "2026-10-23",
        "latest_ratio_pct": 28.5,
        "latest_balance_shares": 1_000_000_000,
        "latest_market_cap": 5_000_000_000.0,
        "latest_deal_num": 5,
        "latest_unlimited_shares": 1_000_000_000,
        "latest_limited_shares": 0,
        "latest_industry": "软件开发",
        "year_ago_date": "2025-10-24",
        "year_ago_ratio_pct": 31.0,
        "change_pp": -2.5,
        "direction": "下降",
        "peak_ratio_pct": 40.0,
        "peak_date": "2024-06-28",
        "low_ratio_pct": 28.5,
        "low_date": "2026-10-23",
        "avg_ratio_pct": 32.75,
        "high_pledge": False,
        "watch_pledge": False,
    }, counts

    latest = data["latest"]
    assert latest["trade_date"] == "2026-10-23", latest
    assert latest["ratio_pct"] == 28.5, latest
    assert latest["balance_shares"] == 1_000_000_000, latest
    assert latest["market_cap"] == 5_000_000_000.0, latest
    assert latest["deal_num"] == 5, latest
    assert latest["unlimited_shares"] == 1_000_000_000, latest
    assert latest["limited_shares"] == 0, latest
    assert latest["industry"] == "软件开发", latest

    series = data["series"]
    assert [item["month"] for item in series] == [
        "2024-06", "2025-10", "2025-12", "2026-01", "2026-09", "2026-10",
    ], series
    assert series[-1]["ratio_pct"] == 28.5, series

    annual = {item["year"]: item for item in data["annual"]}
    assert sorted(annual) == [2024, 2025, 2026], annual
    assert annual[2024]["points"] == 1, annual[2024]
    assert annual[2024]["peak_ratio_pct"] == 40.0, annual[2024]
    assert annual[2025]["points"] == 2, annual[2025]
    assert annual[2025]["latest_ratio_pct"] == 32.0, annual[2025]
    assert annual[2025]["low_ratio_pct"] == 31.0, annual[2025]
    assert annual[2026]["points"] == 3, annual[2026]
    assert annual[2026]["peak_ratio_pct"] == 35.0, annual[2026]
    assert annual[2026]["latest_ratio_pct"] == 28.5, annual[2026]

    notes = data["notes"]
    assert any("质押比例" in note for note in notes), notes
    assert any("不等于" in note for note in notes), notes
    assert any("参考价" in note for note in notes), notes


def test_default_and_window() -> None:
    default = pledge.pledges_from_rows(ROWS, CODE, today=TODAY)
    assert default["years"] == 3, default
    assert default["window_start"] == "2024-01-01", default
    assert default["counts"]["points"] == 6, default["counts"]

    wide = pledge.pledges_from_rows(ROWS, CODE, years=99, today=TODAY)
    assert wide["years"] == 10, wide
    assert wide["counts"]["points"] == 7, wide["counts"]

    bad = pledge.pledges_from_rows(ROWS, CODE, years="bad", today=TODAY)
    assert bad["years"] == 3, bad


def test_risk_levels() -> None:
    high = pledge.pledges_from_rows(
        [_row("2026-10-23", 55.0, balance_wan=100000.0,
              market_cap_wan=500000.0, deals=5)],
        CODE, today=TODAY,
    )
    assert high["counts"]["high_pledge"] is True, high["counts"]
    assert high["counts"]["watch_pledge"] is False, high["counts"]

    watch = pledge.pledges_from_rows(
        [_row("2026-10-23", 35.0, balance_wan=100000.0,
              market_cap_wan=500000.0, deals=5)],
        CODE, today=TODAY,
    )
    assert watch["counts"]["high_pledge"] is False, watch["counts"]
    assert watch["counts"]["watch_pledge"] is True, watch["counts"]


def test_empty_and_errors() -> None:
    data = pledge.pledges_from_rows([], CODE, today=TODAY)
    assert data["available"] is True, data
    assert data["has_pledges"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None, data
    assert data["series"] == [] and data["annual"] == [], data
    assert data["counts"]["points"] == 0, data["counts"]
    assert any("没有取到" in note for note in data["notes"]), data["notes"]

    try:
        pledge.pledges_from_rows("bad", CODE)  # type: ignore[arg-type]
    except pledge.PledgeError:
        pass
    else:
        raise AssertionError("非列表输入应抛 PledgeError")

    try:
        pledge.fetch_pledges("")
    except pledge.PledgeError:
        pass
    else:
        raise AssertionError("空代码应抛 PledgeError")


def test_window_empty() -> None:
    old = [_row("2023-12-29", 20.0, balance_wan=80000.0,
                market_cap_wan=400000.0, deals=2)]
    data = pledge.pledges_from_rows(old, CODE, years=3, today=TODAY)
    assert data["as_of"] == "2023-12-29", data
    assert data["latest"]["trade_date"] == "2023-12-29", data
    assert data["counts"]["points"] == 0, data["counts"]
    assert data["series"] == [] and data["annual"] == [], data
    assert any("近3年" in note for note in data["notes"]), data["notes"]


def test_fetch_error() -> None:
    original = pledge._get_page
    pledge._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "接口超时",
    }
    try:
        pledge.fetch_pledges(CODE)
    except pledge.PledgeError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 PledgeError")

    pledge._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "返回数据为空",
    }
    try:
        empty = pledge.fetch_pledges(CODE)
        assert empty["events"] == [], empty
    finally:
        pledge._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = pledge.pledges_from_rows(ROWS, CODE, years=3, today=TODAY)
    text = pledge.format_pledges(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "历史近3年" in text, text
    assert (
        "最新：2026-10-23 质押比例 28.5% / 质押 10.00亿股 / "
        "市值 50.00亿元 / 5 笔"
    ) in text, text
    assert (
        "一年前：2025-10-24 31.0%，变化 -2.5 个百分点（下降）"
    ) in text, text
    assert "区间：峰值 40.0% (2024-06-28) / 均值 32.75%" in text, text
    assert "年度：2024 40.0% / 2025 32.0% / 2026 28.5%" in text, text
    assert "数据来源：" in text, text

    empty = pledge.pledges_from_rows([], CODE, today=TODAY)
    empty_text = pledge.format_pledges(empty, code=CODE, name=NAME)
    assert "暂无已披露的股权质押记录" in empty_text, empty_text

    old = pledge.pledges_from_rows(
        [_row("2023-12-29", 20.0, balance_wan=80000.0,
              market_cap_wan=400000.0, deals=2)],
        CODE, years=3, today=TODAY,
    )
    old_text = pledge.format_pledges(old, code=CODE, name=NAME)
    assert "近3年没有披露的质押记录" in old_text, old_text
    assert "最近一次为 2023-12-29" in old_text, old_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="pledge_cache_")
    pledge.DB_PATH = os.path.join(tmpdir, "stock.db")

    all_rows = pledge.pledges_from_rows(
        ROWS, CODE, years=10, today=TODAY
    )
    events = all_rows["series"]
    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {"code": code, "events": events, "count": len(events)}

    pledge.fetch_pledges = fake_fetch  # type: ignore[assignment]

    first = pledge.ensure_pledges(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(events), first
    assert first["data"]["fetched_at"], first

    second = pledge.ensure_pledges(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    pledge.ensure_pledges(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = pledge._age_hours
    pledge._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    pledge.ensure_pledges(CODE)
    assert calls["fetch"] == 3, calls
    pledge._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise pledge.PledgeError("接口超时")

    pledge.fetch_pledges = broken  # type: ignore[assignment]
    stale = pledge.ensure_pledges(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(events), stale

    try:
        pledge.ensure_pledges("600000")
    except pledge.PledgeError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 PledgeError")

    try:
        pledge.ensure_pledges("")
    except pledge.PledgeError:
        pass
    else:
        raise AssertionError("空代码应该抛 PledgeError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="pledge_build_")
    pledge.DB_PATH = os.path.join(tmpdir, "stock.db")

    all_rows = pledge.pledges_from_rows(
        ROWS, CODE, years=10, today=TODAY
    )
    pledge.fetch_pledges = lambda code: {  # type: ignore[assignment]
        "code": code,
        "events": all_rows["series"],
        "count": len(all_rows["series"]),
    }

    class FakeStock:
        name = NAME

    original_stock = pledge.get_stock
    pledge.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = pledge.build_pledges(CODE, years=3, today=TODAY)
    finally:
        pledge.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["latest_ratio_pct"] == 28.5, data["counts"]
    assert data["sources"] == {pledge.SOURCE_LABEL: "2026-10-23"}, data
    assert data["url"] == (
        "https://data.eastmoney.com/gpzy/detail/002230.html"
    ), data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]


def test_page_url() -> None:
    assert pledge.PAGE_URL.format(code=CODE) == (
        "https://data.eastmoney.com/gpzy/detail/002230.html"
    )


def main() -> None:
    test_from_rows()
    test_default_and_window()
    test_risk_levels()
    test_empty_and_errors()
    test_window_empty()
    test_fetch_error()
    test_format()
    test_cache()
    test_build()
    test_page_url()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
