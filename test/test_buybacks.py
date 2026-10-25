"""股票回购验证：计划区间、实施进度、完成度、用途与本地缓存。

纯函数部分直接喂构造好的接口返回；缓存部分把数据库换到临时目录，
并替换 ``fetch_buybacks``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.buybacks as buyback

CODE = "605499"
NAME = "东鹏饮料"
TODAY = "2026-10-25"


def _row(
    announce: str,
    plan_id: str,
    status: str,
    *,
    actual_amount: float | None = None,
    actual_shares: int | None = None,
    planned_low: float | None = None,
    planned_high: float | None = None,
    shares_low: int | None = None,
    shares_high: int | None = None,
    price_low: float | None = None,
    price_high: float | None = None,
    actual_price_low: float | None = None,
    actual_price_high: float | None = None,
    start: str | None = None,
    end: str | None = None,
    update: str | None = None,
    finish: str | None = None,
    purpose: str = "",
    ratio_low: float | None = None,
    ratio_high: float | None = None,
    actual_ratio_total: float | None = None,
    actual_ratio_float: float | None = None,
) -> dict:
    return {
        "DIM_SCODE": CODE,
        "SECURITYSHORTNAME": NAME,
        "DIM_DATE": f"{announce} 00:00:00",
        "REPURCODE": plan_id,
        "REPURPROGRESS": status,
        "REPURAMOUNT": actual_amount,
        "REPURNUM": actual_shares,
        "REPURAMOUNTLOWER": planned_low,
        "REPURAMOUNTLIMIT": planned_high,
        "REPURNUMLOWER": shares_low,
        "REPURNUMCAP": shares_high,
        "REPURPRICELOWER": price_low,
        "REPURPRICECAP": price_high,
        "REPURPRICELOWER1": actual_price_low,
        "REPURPRICECAP1": actual_price_high,
        "REPURSTARTDATE": f"{start} 00:00:00" if start else None,
        "REPURENDDATE": f"{end} 00:00:00" if end else None,
        "UPD": f"{update} 00:00:00" if update else None,
        "FINISHDATE": f"{finish} 00:00:00" if finish else None,
        "REPURADVANCEDATE": None,
        "REPUROBJECTIVE": purpose,
        "ZSZXX": ratio_low,
        "ZSZSX": ratio_high,
        "ZJSZBL": actual_ratio_total,
        "ZJLTBL": actual_ratio_float,
        "SFFHSP": None,
    }


ROWS = [
    _row(
        "2026-04-02", "15569", "004",
        actual_amount=1_038_719_867.57, actual_shares=7_122_551,
        planned_low=1_000_000_000, planned_high=2_000_000_000,
        shares_low=5_000_000, shares_high=10_000_000,
        price_low=134.2, price_high=185.88,
        actual_price_low=134.2, actual_price_high=154.9,
        start="2026-04-30", end="2027-04-29", update="2026-09-18",
        purpose=(
            "本次回购的股份中，不低于90%将用于注销并减少注册资本，"
            "剩余部分用于股权激励计划"
        ),
        ratio_low=0.95257, ratio_high=1.90514,
        actual_ratio_total=1.42885, actual_ratio_float=1.55183,
    ),
    # 同一计划较旧的公告快照，应被最新 UPD 覆盖
    _row(
        "2026-04-02", "15569", "004",
        actual_amount=500_000_000, actual_shares=3_500_000,
        planned_low=1_000_000_000, planned_high=2_000_000_000,
        start="2026-04-30", end="2027-04-29", update="2026-05-12",
        purpose="用于注销并减少注册资本",
    ),
    _row(
        "2025-11-06", "15239", "006",
        actual_amount=2_999_933_749.57, actual_shares=2_188_614,
        planned_low=3_000_000_000, planned_high=3_000_000_000,
        shares_low=2_188_614, shares_high=2_188_614,
        price_low=1_800.0, price_high=1_863.67,
        actual_price_low=1_800.0, actual_price_high=1_863.67,
        start="2025-11-28", end="2026-05-28", update="2026-05-28",
        finish="2026-05-28", purpose="用于注销并减少注册资本",
    ),
    _row(
        "2024-03-07", "11013", "005",
        actual_amount=399_993_367, actual_shares=1_877_000,
        planned_low=400_000_000, planned_high=400_000_000,
        shares_low=1_877_000, shares_high=1_877_000,
        price_low=270.0, price_high=270.0,
        start="2024-04-19", end="2025-04-19", update="2024-05-14",
        purpose="用于注销以减少注册资本",
    ),
    _row(
        "2023-06-01", "9001", "001",
        planned_low=500_000_000, planned_high=1_000_000_000,
        shares_low=2_000_000, shares_high=4_000_000,
        purpose="用于注销",
    ),
    # 五年窗口之外
    _row(
        "2020-01-01", "7001", "006",
        actual_amount=100_000_000, actual_shares=1_000_000,
        planned_low=100_000_000, planned_high=100_000_000,
        update="2020-12-01", purpose="用于员工持股计划",
    ),
]


def test_from_rows() -> None:
    data = buyback.buybacks_from_rows(ROWS, CODE, years=5, today=TODAY)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-09-18", data
    assert data["window_start"] == "2022-01-01", data
    assert data["window_end"] == TODAY, data
    assert data["years"] == 5, data
    assert data["has_buybacks"] is True, data
    assert data["plans_truncated"] == 0, data

    counts = data["counts"]
    assert counts == {
        "plans": 4,
        "active": 2,
        "pending": 1,
        "completed": 1,
        "stopped": 1,
        "cancel_plans": 4,
        "incentive_plans": 1,
        "actual_amount": 4_438_646_984.14,
        "actual_shares": 11_188_165,
        "planned_amount_low": 4_900_000_000.0,
        "planned_amount_high": 6_400_000_000.0,
        "completion_pct": 90.6,
        "missing_actual": 1,
        "amount_complete": False,
        "planned_amount_complete": True,
        "latest_update": "2026-09-18",
    }, counts

    latest = data["latest"]
    assert latest["announce_date"] == "2026-04-02", latest
    assert latest["plan_id"] == "15569", latest
    assert latest["status"] == "实施中", latest
    assert latest["progress_code"] == "004", latest
    assert latest["is_active"] is True, latest
    assert latest["completed"] is False, latest
    assert latest["actual_amount"] == 1_038_719_867.57, latest
    assert latest["actual_shares"] == 7_122_551, latest
    assert latest["planned_amount_low"] == 1_000_000_000, latest
    assert latest["planned_amount_high"] == 2_000_000_000, latest
    assert latest["amount_completion_pct"] == 103.9, latest
    assert latest["shares_completion_pct"] == 142.5, latest
    assert latest["purpose_tags"] == ["注销", "股权激励"], latest
    assert latest["cancel"] is True and latest["incentive"] is True, latest
    assert latest["planned_ratio_low_pct"] == 0.95257, latest
    assert latest["actual_ratio_total_pct"] == 1.42885, latest

    plans = data["plans"]
    assert [plan["plan_id"] for plan in plans] == [
        "15569", "15239", "11013", "9001",
    ], plans
    assert plans[0]["actual_amount"] == 1_038_719_867.57, plans[0]
    assert plans[1]["completed"] is True, plans[1]
    assert plans[2]["stopped"] is True, plans[2]
    assert plans[3]["pending"] is True, plans[3]
    assert plans[3]["actual_amount"] is None, plans[3]
    assert plans[3]["amount_completion_pct"] is None, plans[3]

    annual = {row["year"]: row for row in data["annual"]}
    assert sorted(annual) == [2023, 2024, 2025, 2026], annual
    assert annual[2023]["plans"] == 1, annual[2023]
    assert annual[2023]["actual_amount"] is None, annual[2023]
    assert annual[2023]["amount_complete"] is False, annual[2023]
    assert annual[2024]["actual_amount"] == 399_993_367, annual[2024]
    assert annual[2024]["stopped"] == 1, annual[2024]
    assert annual[2025]["actual_amount"] == 2_999_933_749.57, annual[2025]
    assert annual[2025]["completed"] == 1, annual[2025]
    assert annual[2026]["actual_amount"] == 1_038_719_867.57, annual[2026]
    assert annual[2026]["active"] == 1, annual[2026]
    assert annual[2026]["incentive_plans"] == 1, annual[2026]

    notes = data["notes"]
    assert any("累计已回购" in note for note in notes), notes
    assert any("完成度" in note for note in notes), notes
    assert any("注销" in note and "员工持股" in note for note in notes), notes
    assert any("缺少已回购金额" in note for note in notes), notes


def test_purpose_keywords() -> None:
    rows = [
        _row(
            "2026-01-01", "1", "004",
            planned_low=100_000_000, planned_high=200_000_000,
            purpose="回购股份将用于减少公司注册资本",
        ),
        _row(
            "2025-01-01", "2", "006",
            planned_low=100_000_000, planned_high=100_000_000,
            purpose="用于员工持股计划及股权激励",
        ),
    ]
    data = buyback.buybacks_from_rows(rows, CODE, years=5, today=TODAY)
    plans = {plan["plan_id"]: plan for plan in data["plans"]}
    assert plans["1"]["cancel"] is True, plans["1"]
    assert plans["1"]["purpose_tags"] == ["注销"], plans["1"]
    assert plans["2"]["incentive"] is True, plans["2"]
    assert plans["2"]["purpose_tags"] == ["员工持股", "股权激励"], plans["2"]
    assert data["counts"]["cancel_plans"] == 1, data["counts"]
    assert data["counts"]["incentive_plans"] == 1, data["counts"]


def test_window_and_clamp() -> None:
    two = buyback.buybacks_from_rows(ROWS, CODE, years=2, today=TODAY)
    assert two["window_start"] == "2025-01-01", two
    assert two["counts"]["plans"] == 2, two["counts"]
    assert two["counts"]["active"] == 1, two["counts"]
    assert two["counts"]["completed"] == 1, two["counts"]
    assert two["counts"]["actual_amount"] == 4_038_653_617.14, two["counts"]

    one = buyback.buybacks_from_rows(ROWS, CODE, years=1, today=TODAY)
    assert one["counts"]["plans"] == 1, one["counts"]
    assert one["counts"]["actual_amount"] == 1_038_719_867.57, one["counts"]

    wide = buyback.buybacks_from_rows(ROWS, CODE, years=99, today=TODAY)
    assert wide["years"] == 10, wide
    assert wide["counts"]["plans"] == 5, wide["counts"]

    fallback = buyback.buybacks_from_rows(ROWS, CODE, years="bad", today=TODAY)
    assert fallback["years"] == 5, fallback


def test_empty_window() -> None:
    old = [
        _row(
            "2020-01-01", "7001", "006",
            actual_amount=100_000_000, actual_shares=1_000_000,
            planned_low=100_000_000, planned_high=100_000_000,
            update="2020-12-01", purpose="用于员工持股计划",
        )
    ]
    data = buyback.buybacks_from_rows(old, CODE, years=5, today=TODAY)
    assert data["as_of"] == "2020-12-01", data
    assert data["latest"]["announce_date"] == "2020-01-01", data
    assert data["latest_window"] is None, data
    assert data["has_buybacks"] is False, data
    assert data["counts"]["plans"] == 0, data["counts"]
    assert data["plans"] == [], data
    assert any("近5年没有披露" in note for note in data["notes"]), data["notes"]


def test_empty_and_errors() -> None:
    data = buyback.buybacks_from_rows([], CODE, years=5, today=TODAY)
    assert data["available"] is True, data
    assert data["has_buybacks"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None, data
    assert data["plans"] == [], data
    assert any("没有取到" in note for note in data["notes"]), data["notes"]

    try:
        buyback.buybacks_from_rows("bad", CODE)  # type: ignore[arg-type]
    except buyback.BuybackError:
        pass
    else:
        raise AssertionError("非列表输入应抛 BuybackError")

    try:
        buyback.fetch_buybacks("")
    except buyback.BuybackError:
        pass
    else:
        raise AssertionError("空代码应抛 BuybackError")


def test_fetch_error() -> None:
    original = buyback._get_page
    buyback._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "接口超时",
    }
    try:
        buyback.fetch_buybacks(CODE)
    except buyback.BuybackError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 BuybackError")
    finally:
        buyback._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = buyback.buybacks_from_rows(ROWS, CODE, years=5, today=TODAY)
    text = buyback.format_buybacks(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "近5年" in text, text
    assert "回购计划 4 个：在途 2（待实施 1） / 完成 1 / 停止 1" in text, text
    assert (
        "已回购 44.39亿元 / 1118.82万股；计划金额 "
        "49.00亿元~64.00亿元，下限完成度 90.6%"
    ) in text, text
    assert "用途：注销 4 个 / 员工持股或股权激励 1 个" in text, text
    assert "年度已回购：" in text, text
    assert "2023 —" in text, text
    assert "2026 10.39亿元" in text, text
    assert (
        "最新计划：2026-04-02 计划金额 10.00亿元~20.00亿元，"
        "已回购 10.39亿元（实施中，下限完成度 103.9%）"
    ) in text, text
    assert "数据来源：" in text, text

    empty = buyback.buybacks_from_rows([], CODE, years=5, today=TODAY)
    empty_text = buyback.format_buybacks(empty, code=CODE, name=NAME)
    assert "暂无股票回购记录" in empty_text, empty_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="buyback_cache_")
    buyback.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}
    all_plans = buyback.buybacks_from_rows(
        ROWS, CODE, years=10, today=TODAY
    )["plans"]

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {"code": code, "plans": all_plans, "count": len(all_plans)}

    buyback.fetch_buybacks = fake_fetch  # type: ignore[assignment]

    first = buyback.ensure_buybacks(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(all_plans), first
    assert first["data"]["fetched_at"], first

    second = buyback.ensure_buybacks(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    buyback.ensure_buybacks(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = buyback._age_hours
    buyback._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    buyback.ensure_buybacks(CODE)
    assert calls["fetch"] == 3, calls
    buyback._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise buyback.BuybackError("接口超时")

    buyback.fetch_buybacks = broken  # type: ignore[assignment]
    stale = buyback.ensure_buybacks(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(all_plans), stale

    try:
        buyback.ensure_buybacks("600000")
    except buyback.BuybackError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 BuybackError")

    try:
        buyback.ensure_buybacks("")
    except buyback.BuybackError:
        pass
    else:
        raise AssertionError("空代码应该抛 BuybackError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="buyback_build_")
    buyback.DB_PATH = os.path.join(tmpdir, "stock.db")

    all_plans = buyback.buybacks_from_rows(
        ROWS, CODE, years=10, today=TODAY
    )["plans"]
    buyback.fetch_buybacks = lambda code: {  # type: ignore[assignment]
        "code": code, "plans": all_plans, "count": len(all_plans),
    }

    class FakeStock:
        name = NAME

    original_stock = buyback.get_stock
    buyback.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = buyback.build_buybacks(CODE, years=5, today=TODAY)
    finally:
        buyback.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["plans"] == 4, data["counts"]
    assert data["sources"] == {buyback.SOURCE_LABEL: "2026-09-18"}, data
    assert data["url"] == (
        "https://data.eastmoney.com/gphg/detail/605499-2026-04-02.html"
    ), data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]


def test_page_url() -> None:
    assert buyback.PAGE_URL.format(code=CODE, date="2026-04-02") == (
        "https://data.eastmoney.com/gphg/detail/605499-2026-04-02.html"
    )


def main() -> None:
    test_from_rows()
    test_purpose_keywords()
    test_window_and_clamp()
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
