"""限售解禁验证：未来解禁批次、历史解禁、股东户数与本地缓存。

纯函数部分直接喂构造好的接口返回；缓存部分把数据库换到临时目录，
并替换 ``fetch_lockups``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.lockups as lockup

CODE = "605499"
NAME = "东鹏饮料"
TODAY = "2026-10-25"


def _row(
    free_date: str,
    shares_wan: float | None,
    *,
    market_cap_wan: float | None = None,
    shares_type: str = "首发原股东限售股份",
    holders: int | None = None,
    price: float | None = None,
    ratio_total: float | None = None,
    ratio_free: float | None = None,
    free_shares_wan: float | None = None,
    non_free_shares_wan: float | None = None,
    able_free_shares_wan: float | None = None,
    update: str | None = None,
    market: str = "sh",
) -> dict:
    return {
        "SECURITY_CODE": CODE,
        "SECURITY_NAME_ABBR": NAME,
        "FREE_DATE": f"{free_date} 00:00:00",
        "CURRENT_FREE_SHARES": shares_wan,
        "LIFT_MARKET_CAP": market_cap_wan,
        "FREE_SHARES_TYPE": shares_type,
        "BATCH_HOLDER_NUM": holders,
        "NEW": price,
        "TOTAL_RATIO": ratio_total,
        "TOTALSHARES_RATIO": ratio_total,
        "FREE_RATIO": ratio_free,
        "FREE_SHARES": free_shares_wan,
        "NON_FREE_SHARES": non_free_shares_wan,
        "ABLE_FREE_SHARES": able_free_shares_wan,
        "EUTIME": f"{update} 00:00:00" if update else None,
        "MARKET_TYPE": market,
    }


ROWS = [
    _row(
        "2024-05-27", 24073.9143,
        market_cap_wan=5368001.410614, shares_type="首发原股东限售股份",
        holders=3, price=222.98, ratio_total=0.60183281168,
        ratio_free=0.60183281168, free_shares_wan=40001.0,
        non_free_shares_wan=0.0, able_free_shares_wan=24073.9143,
        update="2024-11-18",
    ),
    _row(
        "2023-05-29", 8326.0857,
        market_cap_wan=1392954.13761, shares_type="首发原股东限售股份",
        holders=20, price=167.3, ratio_total=0.208146938827,
        ratio_free=0.522762660843, free_shares_wan=15927.0857,
        non_free_shares_wan=24073.9143, able_free_shares_wan=8326.0857,
        update="2023-05-11",
    ),
    _row(
        "2022-05-27", 3600.0,
        market_cap_wan=495360.0, shares_type="首发原股东限售股份",
        holders=1, price=137.6, ratio_total=0.089997750056,
        ratio_free=0.473621891856, free_shares_wan=7601.0,
        non_free_shares_wan=32400.0, able_free_shares_wan=3600.0,
        update="2022-05-20",
    ),
    _row(
        "2026-11-20", 1000.0,
        market_cap_wan=500000.0, shares_type="股权激励限售股份",
        holders=5, price=50.0, ratio_total=0.025, ratio_free=0.10,
        update="2026-10-20",
    ),
    _row(
        "2027-05-20", 2000.0,
        market_cap_wan=800000.0, shares_type="定向增发机构配售股份",
        holders=10, price=40.0, ratio_total=0.05, ratio_free=0.20,
        update="2026-10-21",
    ),
    # 默认五年窗口外，十年窗口内
    _row(
        "2020-01-01", 100.0,
        market_cap_wan=10000.0, holders=1, update="2020-12-01",
    ),
]


def test_from_rows() -> None:
    data = lockup.lockups_from_rows(
        ROWS, CODE, years=5, ahead_months=12, today=TODAY
    )

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-10-21", data
    assert data["window_start"] == "2022-01-01", data
    assert data["window_end"] == "2027-10-25", data
    assert data["years"] == 5, data
    assert data["ahead_months"] == 12, data
    assert data["has_lockups"] is True, data
    assert data["events_truncated"] == 0, data

    counts = data["counts"]
    assert counts == {
        "events": 5,
        "history_events": 3,
        "upcoming_events": 2,
        "next_date": "2026-11-20",
        "next_days": 26,
        "next_shares": 10_000_000,
        "next_market_cap": 5_000_000_000.0,
        "next_ratio_pct": 2.5,
        "next_holders": 5,
        "next_type": "股权激励限售股份",
        "upcoming_shares": 30_000_000,
        "upcoming_market_cap": 13_000_000_000.0,
        "upcoming_ratio_pct": 7.5,
        "history_shares": 360_000_000,
        "history_market_cap": 72_563_155_482.24,
        "total_shares": 390_000_000,
        "total_market_cap": 85_563_155_482.24,
        "latest_date": "2024-05-27",
        "latest_type": "首发原股东限售股份",
        "missing_market_cap": 0,
    }, counts

    next_event = data["next"]
    assert next_event["free_date"] == "2026-11-20", next_event
    assert next_event["is_upcoming"] is True, next_event
    assert next_event["days_until"] == 26, next_event
    assert next_event["shares"] == 10_000_000, next_event
    assert next_event["market_cap"] == 5_000_000_000.0, next_event
    assert next_event["ratio_total_pct"] == 2.5, next_event
    assert next_event["holders"] == 5, next_event
    assert next_event["shares_type"] == "股权激励限售股份", next_event

    latest = data["latest"]
    assert latest["free_date"] == "2024-05-27", latest
    assert latest["is_upcoming"] is False, latest
    assert latest["days_until"] < 0, latest
    assert latest["shares"] == 240_739_143, latest
    assert latest["market_cap"] == 53_680_014_106.14, latest
    assert latest["ratio_total_pct"] == 60.183281168, latest
    assert latest["ratio_free_pct"] == 60.183281168, latest
    assert latest["free_shares_after"] == 400_010_000, latest
    assert latest["non_free_shares_after"] == 0, latest
    assert latest["able_free_shares"] == 240_739_143, latest
    assert latest["update_date"] == "2024-11-18", latest

    assert [event["free_date"] for event in data["upcoming"]] == [
        "2026-11-20", "2027-05-20",
    ], data["upcoming"]
    assert [event["free_date"] for event in data["history"]] == [
        "2024-05-27", "2023-05-29", "2022-05-27",
    ], data["history"]

    annual = {row["year"]: row for row in data["annual"]}
    assert sorted(annual) == [2022, 2023, 2024, 2026, 2027], annual
    assert annual[2022]["shares"] == 36_000_000, annual[2022]
    assert annual[2023]["shares"] == 83_260_857, annual[2023]
    assert annual[2024]["market_cap"] == 53_680_014_106.14, annual[2024]
    assert annual[2026]["upcoming_events"] == 1, annual[2026]
    assert annual[2027]["upcoming_events"] == 1, annual[2027]

    types = {row["type"]: row for row in data["types"]}
    assert types["首发原股东限售股份"]["events"] == 3, types
    assert types["首发原股东限售股份"]["shares"] == 360_000_000, types
    assert types["股权激励限售股份"]["upcoming"] == 1, types
    assert types["定向增发机构配售股份"]["market_cap"] == 8_000_000_000.0, types

    notes = data["notes"]
    assert any("解禁不等于减持" in note for note in notes), notes
    assert any("当前价" in note for note in notes), notes
    assert any("未来12个月" in note for note in notes), notes


def test_default_and_window() -> None:
    default = lockup.lockups_from_rows(ROWS, CODE, today=TODAY)
    assert default["years"] == 3, default
    assert default["window_start"] == "2024-01-01", default
    assert default["counts"]["events"] == 3, default["counts"]
    assert default["counts"]["history_events"] == 1, default["counts"]
    assert default["counts"]["upcoming_events"] == 2, default["counts"]

    wide = lockup.lockups_from_rows(
        ROWS, CODE, years=99, ahead_months=99, today=TODAY
    )
    assert wide["years"] == 10, wide
    assert wide["ahead_months"] == 36, wide
    assert wide["counts"]["events"] == 6, wide["counts"]

    bad = lockup.lockups_from_rows(
        ROWS, CODE, years="bad", ahead_months="bad", today=TODAY
    )
    assert bad["years"] == 3, bad
    assert bad["ahead_months"] == 12, bad

    no_future = lockup.lockups_from_rows(
        ROWS, CODE, years=5, ahead_months=0, today=TODAY
    )
    assert no_future["counts"]["upcoming_events"] == 0, no_future["counts"]
    assert no_future["next"] is None, no_future
    assert no_future["counts"]["next_date"] is None, no_future["counts"]


def test_empty_window() -> None:
    old = [
        _row(
            "2020-01-01", 100.0,
            market_cap_wan=10000.0, holders=1, update="2020-12-01",
        )
    ]
    data = lockup.lockups_from_rows(
        old, CODE, years=5, ahead_months=12, today=TODAY
    )
    assert data["as_of"] == "2020-12-01", data
    assert data["latest"]["free_date"] == "2020-01-01", data
    assert data["next"] is None, data
    assert data["has_lockups"] is False, data
    assert data["counts"]["events"] == 0, data["counts"]
    assert data["history"] == [] and data["upcoming"] == [], data
    assert any("近5年" in note and "未来12个月" in note for note in data["notes"]), data["notes"]


def test_empty_and_errors() -> None:
    data = lockup.lockups_from_rows([], CODE, today=TODAY)
    assert data["available"] is True, data
    assert data["has_lockups"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None and data["next"] is None, data
    assert data["history"] == [] and data["upcoming"] == [], data
    assert any("没有取到" in note for note in data["notes"]), data["notes"]

    try:
        lockup.lockups_from_rows("bad", CODE)  # type: ignore[arg-type]
    except lockup.LockupError:
        pass
    else:
        raise AssertionError("非列表输入应抛 LockupError")

    try:
        lockup.fetch_lockups("")
    except lockup.LockupError:
        pass
    else:
        raise AssertionError("空代码应抛 LockupError")


def test_fetch_error() -> None:
    original = lockup._get_page
    lockup._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "接口超时",
    }
    try:
        lockup.fetch_lockups(CODE)
    except lockup.LockupError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 LockupError")
    finally:
        lockup._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = lockup.lockups_from_rows(
        ROWS, CODE, years=5, ahead_months=12, today=TODAY
    )
    text = lockup.format_lockups(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "历史近5年" in text and "未来12个月" in text, text
    assert "未来 12 个月：2 批 / 3000.00万股 / 130.00亿元" in text, text
    assert (
        "下一批：2026-11-20 股权激励限售股份 1000.00万股 / "
        "50.00亿元 / 占总股本 2.5%（5 名股东）"
    ) in text, text
    assert "历史：近5年 3 批 / 3.60亿股 / 725.63亿元" in text, text
    assert "最近 2024-05-27 2.41亿股 / 536.80亿元" in text, text
    assert "年度解禁：" in text, text
    assert "2022 3600.00万股" in text, text
    assert "2027 2000.00万股" in text, text
    assert "数据来源：" in text, text

    no_future = lockup.lockups_from_rows(
        ROWS, CODE, years=5, ahead_months=0, today=TODAY
    )
    no_future_text = lockup.format_lockups(
        no_future, code=CODE, name=NAME
    )
    assert "未来0个月：无已披露解禁" in no_future_text, no_future_text

    empty = lockup.lockups_from_rows([], CODE, today=TODAY)
    empty_text = lockup.format_lockups(empty, code=CODE, name=NAME)
    assert "暂无已披露的解禁记录" in empty_text, empty_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="lockup_cache_")
    lockup.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}
    all_events = lockup.lockups_from_rows(
        ROWS, CODE, years=10, ahead_months=36, today=TODAY
    )["history"] + lockup.lockups_from_rows(
        ROWS, CODE, years=10, ahead_months=36, today=TODAY
    )["upcoming"]

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {"code": code, "events": all_events, "count": len(all_events)}

    lockup.fetch_lockups = fake_fetch  # type: ignore[assignment]

    first = lockup.ensure_lockups(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(all_events), first
    assert first["data"]["fetched_at"], first

    second = lockup.ensure_lockups(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    lockup.ensure_lockups(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = lockup._age_hours
    lockup._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    lockup.ensure_lockups(CODE)
    assert calls["fetch"] == 3, calls
    lockup._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise lockup.LockupError("接口超时")

    lockup.fetch_lockups = broken  # type: ignore[assignment]
    stale = lockup.ensure_lockups(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(all_events), stale

    try:
        lockup.ensure_lockups("600000")
    except lockup.LockupError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 LockupError")

    try:
        lockup.ensure_lockups("")
    except lockup.LockupError:
        pass
    else:
        raise AssertionError("空代码应该抛 LockupError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="lockup_build_")
    lockup.DB_PATH = os.path.join(tmpdir, "stock.db")

    all_events = lockup.lockups_from_rows(
        ROWS, CODE, years=10, ahead_months=36, today=TODAY
    )
    lockup.fetch_lockups = lambda code: {  # type: ignore[assignment]
        "code": code,
        "events": all_events["history"] + all_events["upcoming"],
        "count": len(all_events["history"]) + len(all_events["upcoming"]),
    }

    class FakeStock:
        name = NAME

    original_stock = lockup.get_stock
    lockup.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = lockup.build_lockups(
            CODE, years=5, ahead_months=12, today=TODAY
        )
    finally:
        lockup.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["upcoming_events"] == 2, data["counts"]
    assert data["sources"] == {lockup.SOURCE_LABEL: "2026-10-21"}, data
    assert data["url"] == (
        "https://data.eastmoney.com/dxf/detail.html?code=605499"
    ), data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]


def test_page_url() -> None:
    assert lockup.PAGE_URL.format(code=CODE) == (
        "https://data.eastmoney.com/dxf/detail.html?code=605499"
    )


def main() -> None:
    test_from_rows()
    test_default_and_window()
    test_empty_window()
    test_empty_and_errors()
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
