"""高管增减持验证：增减持方向、关联人、持股计划与本地缓存。

纯函数部分直接喂构造好的接口返回；缓存部分把数据库换到临时目录，
并替换 ``fetch_insider_trades``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.insider_trades as insider

CODE = "605499"
NAME = "东鹏饮料"
TODAY = "2026-10-25"


def _row(
    date: str,
    person: str,
    shares: int,
    price: float | None,
    amount: float | None,
    *,
    executive: str | None = None,
    position: str | None = "董事长",
    relation: str = "本人",
    reason: str = "二级市场买卖",
    ratio: float | None = None,
    hold_after: int | None = None,
    begin: int | None = None,
    end: int | None = None,
    gid: int | None = None,
) -> dict:
    return {
        "SECURITY_CODE": CODE,
        "SECURITY_NAME": NAME,
        "CHANGE_DATE": f"{date} 00:00:00",
        "PERSON_NAME": person,
        "DSE_PERSON_NAME": executive or person,
        "POSITION_NAME": position,
        "PERSON_DSE_RELATION": relation,
        "CHANGE_SHARES": shares,
        "AVERAGE_PRICE": price,
        "CHANGE_AMOUNT": amount,
        "CHANGE_REASON": reason,
        "CHANGE_RATIO": ratio,
        "CHANGE_AFTER_HOLDNUM": hold_after,
        "BEGIN_HOLD_NUM": begin,
        "END_HOLD_NUM": end,
        "HOLD_TYPE": "A股",
        "GGEID": gid,
    }


ROWS = [
    _row("2026-05-29", "林木勤", 49_800, 129.7958, 6_463_830.84,
         position="董事长,董事,总裁", ratio=0.0068, hold_after=336_304_724,
         gid=1001),
    # 同一披露编号重复出现，只应算一笔
    _row("2026-05-29", "林木勤", 49_800, 129.7958, 6_463_830.84,
         position="董事长,董事,总裁", ratio=0.0068, hold_after=336_304_724,
         gid=1001),
    _row("2025-11-10", "刘丽华", -99_000, 193.861, -19_192_239,
         position="董事,高级管理人员", ratio=0.0247, hold_after=600_900,
         begin=699_900, end=600_900, gid=1002),
    _row("2024-10-31", "彭得新", -13_300, 188.565, -2_507_914.5,
         position="高级管理人员", ratio=0.0033, hold_after=2_239_752,
         gid=1003),
    # 关联人交易：本人是配偶黄均红，高管是彭智辅
    _row("2024-03-15", "黄均红", -500, 24.51, -12_255,
         executive="彭智辅", position="董秘、高管", relation="配偶", gid=1004),
    # 三年窗口之外
    _row("2023-06-01", "刘丽华", 10_000, 150.0, 1_500_000,
         position="董事,高级管理人员", gid=1005),
    # 持股计划：同一笔因归属不同高管出现两行，应合并成一行
    _row("2026-05-26", "2026年度核心人员持股计划", 1_000_000, 50.0,
         50_000_000, executive="付欣", position="执行董事", relation="其他组织",
         reason="二级市场购股", hold_after=1_000_000, gid=2001),
    _row("2026-05-26", "2026年度核心人员持股计划", 1_000_000, 50.0,
         50_000_000, executive="谢永林", position="总经理", relation="其他组织",
         reason="二级市场购股", hold_after=1_000_000, gid=2002),
    # 缺少成交金额，但有用均价，可以估算
    _row("2025-06-20", "张三", 1_000, 10.0, None, gid=1006),
    # 子女交易
    _row("2024-07-01", "李四", 2_000, 20.0, 40_000,
         executive="李五", position="监事", relation="子女", gid=1007),
    # 0 股记录应忽略
    _row("2024-01-01", "无效记录", 0, None, None, gid=1008),
]


def test_from_rows() -> None:
    data = insider.insider_trades_from_rows(ROWS, CODE, years=3, today=TODAY)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-05-29", data
    assert data["window_start"] == "2024-01-01", data
    assert data["window_end"] == TODAY, data
    assert data["years"] == 3, data
    assert data["has_trades"] is True, data
    assert data["events_truncated"] == 0, data

    counts = data["counts"]
    assert counts == {
        "events": 7,
        "buy_events": 4,
        "sell_events": 3,
        "people": 7,
        "insiders": 6,
        "plans": 1,
        "plan_events": 1,
        "buy_shares": 1_052_800,
        "sell_shares": 112_800,
        "net_shares": 940_000,
        "buy_amount": 56_513_830.84,
        "sell_amount": 21_712_408.5,
        "net_amount": 34_801_422.34,
        "missing_amount_events": 0,
        "estimated_amount_events": 1,
        "latest_date": "2026-05-29",
    }, counts

    latest = data["latest"]
    assert latest["change_date"] == "2026-05-29", latest
    assert latest["person"] == "林木勤", latest
    assert latest["direction"] == "增持", latest
    assert latest["shares"] == 49_800, latest
    assert latest["amount"] == 6_463_830.84, latest
    assert latest["change_ratio_pct"] == 0.0068, latest
    assert data["latest_window"]["change_date"] == "2026-05-29", data

    events = data["events"]
    assert [event["change_date"] for event in events] == [
        "2026-05-29", "2026-05-26", "2025-11-10", "2025-06-20",
        "2024-10-31", "2024-07-01", "2024-03-15",
    ], events
    assert events[0]["amount_estimated"] is False, events[0]
    assert events[3]["amount"] == 10_000, events[3]
    assert events[3]["amount_estimated"] is True, events[3]
    assert events[1]["kind"] == "plan", events[1]
    assert events[1]["executive"] is None, events[1]
    assert events[1]["position"] is None, events[1]
    assert events[2]["begin_hold"] == 699_900, events[2]
    assert events[2]["end_hold"] == 600_900, events[2]
    assert events[6]["relation"] == "配偶", events[6]
    assert events[6]["executive"] == "彭智辅", events[6]

    annual = {row["year"]: row for row in data["annual"]}
    assert sorted(annual) == [2024, 2025, 2026], annual
    assert annual[2024]["events"] == 3, annual[2024]
    assert annual[2024]["buy_shares"] == 2_000, annual[2024]
    assert annual[2024]["sell_shares"] == 13_800, annual[2024]
    assert annual[2024]["net_shares"] == -11_800, annual[2024]
    assert annual[2024]["net_amount"] == -2_480_169.5, annual[2024]
    assert annual[2025]["net_shares"] == -98_000, annual[2025]
    assert annual[2025]["net_amount"] == -19_182_239, annual[2025]
    assert annual[2026]["net_shares"] == 1_049_800, annual[2026]
    assert annual[2026]["net_amount"] == 56_463_830.84, annual[2026]
    assert all(row["amount_complete"] for row in annual.values()), annual

    people = data["people"]
    assert [row["person"] for row in people[:3]] == [
        "2026年度核心人员持股计划", "刘丽华", "林木勤",
    ], people
    assert people[0]["kind"] == "plan", people[0]
    assert people[0]["net_amount"] == 50_000_000, people[0]
    assert people[1]["net_shares"] == -99_000, people[1]
    assert people[2]["net_shares"] == 49_800, people[2]
    assert people[2]["latest_hold"] == 336_304_724, people[2]

    notes = data["notes"]
    assert any("只统计已披露" in note for note in notes), notes
    assert any("关联人" in note for note in notes), notes
    assert any("持股计划" in note for note in notes), notes
    assert any("按均价估算" in note for note in notes), notes


def test_window() -> None:
    two = insider.insider_trades_from_rows(ROWS, CODE, years=2, today=TODAY)
    assert two["window_start"] == "2025-01-01", two
    assert two["counts"]["events"] == 4, two["counts"]
    assert two["counts"]["buy_shares"] == 1_050_800, two["counts"]
    assert two["counts"]["sell_shares"] == 99_000, two["counts"]
    assert two["counts"]["net_shares"] == 951_800, two["counts"]
    assert two["as_of"] == "2026-05-29", two

    one = insider.insider_trades_from_rows(ROWS, CODE, years=1, today=TODAY)
    assert one["counts"]["events"] == 2, one["counts"]
    assert one["counts"]["net_shares"] == 1_049_800, one["counts"]

    # 超过上限按十年算
    wide = insider.insider_trades_from_rows(ROWS, CODE, years=99, today=TODAY)
    assert wide["years"] == 10, wide
    assert wide["counts"]["events"] == 8, wide["counts"]

    # 参数非法回落到默认三年
    fallback = insider.insider_trades_from_rows(ROWS, CODE, years="bad", today=TODAY)
    assert fallback["years"] == 3, fallback


def test_empty_window() -> None:
    old = [_row("2023-06-01", "刘丽华", 10_000, 150.0, 1_500_000,
                position="董事,高级管理人员", gid=1005)]
    data = insider.insider_trades_from_rows(old, CODE, years=3, today=TODAY)
    assert data["as_of"] == "2023-06-01", data
    assert data["latest"]["change_date"] == "2023-06-01", data
    assert data["latest_window"] is None, data
    assert data["has_trades"] is False, data
    assert data["counts"]["events"] == 0, data["counts"]
    assert data["events"] == [], data
    assert any("近3年没有披露" in note for note in data["notes"]), data["notes"]


def test_empty_and_errors() -> None:
    data = insider.insider_trades_from_rows([], CODE, years=3, today=TODAY)
    assert data["available"] is True, data
    assert data["has_trades"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None, data
    assert data["events"] == [], data
    assert any("没有取到" in note for note in data["notes"]), data["notes"]

    try:
        insider.insider_trades_from_rows("bad", CODE)  # type: ignore[arg-type]
    except insider.InsiderTradeError:
        pass
    else:
        raise AssertionError("非列表输入应抛 InsiderTradeError")

    try:
        insider.fetch_insider_trades("")
    except insider.InsiderTradeError:
        pass
    else:
        raise AssertionError("空代码应抛 InsiderTradeError")


def test_fetch_error() -> None:
    original = insider._get_page
    insider._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "接口超时",
    }
    try:
        insider.fetch_insider_trades(CODE)
    except insider.InsiderTradeError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 InsiderTradeError")
    finally:
        insider._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = insider.insider_trades_from_rows(ROWS, CODE, years=3, today=TODAY)
    text = insider.format_insider_trades(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "近3年" in text, text
    assert "增持 4 笔 105.28万股 / 5651.38万元" in text, text
    assert "减持 3 笔 11.28万股 / 2171.24万元" in text, text
    assert "净增持 94.00万股，金额 3480.14万元" in text, text
    assert "年度净增减：" in text, text
    assert "2024 -1.18万股" in text, text
    assert "2025 -9.80万股" in text, text
    assert "2026 +104.98万股" in text, text
    assert "主要变动：" in text, text
    assert "2026年度核心人员持股计划（持股计划） 净增持 100.00万股" in text, text
    assert "数据来源：" in text, text

    empty = insider.insider_trades_from_rows([], CODE, years=3, today=TODAY)
    empty_text = insider.format_insider_trades(empty, code=CODE, name=NAME)
    assert "暂无高管增减持记录" in empty_text, empty_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="insider_cache_")
    insider.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}
    all_events = insider.insider_trades_from_rows(
        ROWS, CODE, years=10, today=TODAY
    )["events"]

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {"code": code, "events": all_events, "count": len(all_events)}

    insider.fetch_insider_trades = fake_fetch  # type: ignore[assignment]

    first = insider.ensure_insider_trades(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(all_events), first
    assert first["data"]["fetched_at"], first

    second = insider.ensure_insider_trades(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    insider.ensure_insider_trades(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = insider._age_hours
    insider._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    insider.ensure_insider_trades(CODE)
    assert calls["fetch"] == 3, calls
    insider._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise insider.InsiderTradeError("接口超时")

    insider.fetch_insider_trades = broken  # type: ignore[assignment]
    stale = insider.ensure_insider_trades(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(all_events), stale

    try:
        insider.ensure_insider_trades("600000")
    except insider.InsiderTradeError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 InsiderTradeError")

    try:
        insider.ensure_insider_trades("")
    except insider.InsiderTradeError:
        pass
    else:
        raise AssertionError("空代码应该抛 InsiderTradeError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="insider_build_")
    insider.DB_PATH = os.path.join(tmpdir, "stock.db")

    all_events = insider.insider_trades_from_rows(
        ROWS, CODE, years=10, today=TODAY
    )["events"]
    insider.fetch_insider_trades = lambda code: {  # type: ignore[assignment]
        "code": code, "events": all_events, "count": len(all_events),
    }

    class FakeStock:
        name = NAME

    original_stock = insider.get_stock
    insider.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = insider.build_insider_trades(CODE, years=3, today=TODAY)
    finally:
        insider.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["events"] == 7, data["counts"]
    assert data["sources"] == {insider.SOURCE_LABEL: "2026-05-29"}, data
    assert data["url"] == "https://data.eastmoney.com/executive/605499.html", data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]


def test_page_url() -> None:
    assert insider.PAGE_URL.format(code=CODE) == (
        "https://data.eastmoney.com/executive/605499.html"
    )


def main() -> None:
    test_from_rows()
    test_window()
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
