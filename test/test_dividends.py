"""分红历史验证：现金分红、送转、年度分红率与本地缓存。

纯函数部分直接喂构造好的接口返回；缓存部分把数据库换到临时目录，
并替换 ``fetch_dividends``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.dividends as div

CODE = "605499"
NAME = "东鹏饮料"


def _row(
    report_date: str,
    cash: float | None,
    shares: int | None,
    *,
    bonus: float | None = None,
    transfer: float | None = None,
    share_bonus: float | None = None,
    notice: str | None = None,
    plan: str | None = None,
    status: str = "实施分配",
    dividend_ratio: float | None = None,
    eps: float | None = None,
) -> dict:
    return {
        "REPORT_DATE": f"{report_date} 00:00:00",
        "PRETAX_BONUS_RMB": cash,
        "BONUS_RATIO": bonus,
        "IT_RATIO": transfer,
        "BONUS_IT_RATIO": share_bonus,
        "TOTAL_SHARES": shares,
        "ASSIGN_PROGRESS": status,
        "IMPL_PLAN_PROFILE": plan,
        "DIVIDENT_RATIO": dividend_ratio,
        "BASIC_EPS": eps,
        "PLAN_NOTICE_DATE": f"{report_date} 00:00:00" if notice is None else notice,
        "NOTICE_DATE": notice,
        "EQUITY_RECORD_DATE": None,
        "EX_DIVIDEND_DATE": "2026-09-24 00:00:00" if report_date == "2026-06-30" else None,
    }


ROWS = [
    _row("2026-06-30", 30, 734199310, notice="2026-09-18 00:00:00",
         plan="10派30.00元(含税,扣税后27.00元)", dividend_ratio=0.02621231979,
         eps=3.9949),
    _row("2025-12-31", 25, 578195410, transfer=3, share_bonus=3,
         notice="2026-05-12 00:00:00", plan="10转3股派25.00元(含税,扣税后22.50元)",
         eps=8.4907),
    _row("2025-06-30", 25, 520013000, notice="2025-08-19 00:00:00",
         plan="10派25.00元(含税,扣税后22.50元)", eps=4.5667),
    _row("2024-12-31", 25, 520013000, notice="2025-04-12 00:00:00",
         plan="10派25.00元(含税,扣税后22.50元)", eps=6.3974),
    _row("2024-06-30", 25, 400010000, transfer=3, share_bonus=3,
         notice="2024-09-26 00:00:00", plan="10转3股派25.00元(含税,扣税后22.50元)",
         eps=4.3264),
    _row("2023-12-31", 20, 400010000, notice="2024-06-04 00:00:00",
         plan="10派20.00元(含税,扣税后18.00元)", eps=5.0993),
    _row("2022-12-31", 15, 400010000, notice="2023-06-06 00:00:00",
         plan="10派15.00元(含税,扣税后13.50元)", eps=3.6012),
    _row("2021-12-31", 15, 400010000, notice="2022-03-29 00:00:00",
         plan="10派15.00元(含税,扣税后13.50元)", eps=3.112),
    _row("2021-06-30", 15, 400010000, notice="2021-08-20 00:00:00",
         plan="10派15.00元(含税,扣税后13.50元)", eps=1.8432),
]

FINANCIAL = [
    {"report_date": "2021-12-31", "net_profit_attributable": 1192960407.59},
    {"report_date": "2022-12-31", "net_profit_attributable": 1440520571.36},
    {"report_date": "2023-12-31", "net_profit_attributable": 2039772803.92},
    {"report_date": "2024-12-31", "net_profit_attributable": 3326708852.44},
    {"report_date": "2025-12-31", "net_profit_attributable": 4415263147.79},
]


def test_from_rows() -> None:
    data = div.dividends_from_rows(ROWS, CODE, financial_rows=FINANCIAL)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-06-30", data
    assert data["has_dividends"] is True, data
    assert data["counts"] == {
        "events": 9,
        "years": 6,
        "cash_years": 6,
        "consecutive_years": 5,
        "latest_cash_year": 2026,
    }, data["counts"]

    latest = data["latest"]
    assert latest["report_date"] == "2026-06-30", latest
    assert latest["fiscal_year"] == 2026, latest
    assert latest["period_label"] == "中报", latest
    assert latest["plan_short"] == "10派30.00元", latest
    assert latest["cash_per_10"] == 30.0, latest
    assert latest["cash_per_share"] == 3.0, latest
    assert latest["cash_total"] == 2202597930.0, latest
    assert latest["total_shares"] == 734199310, latest
    assert latest["yield_pct"] == 2.621231979, latest
    assert latest["ex_date"] == "2026-09-24", latest

    annual = {row["year"]: row for row in data["annual"]}
    assert sorted(annual) == [2021, 2022, 2023, 2024, 2025, 2026], annual
    assert annual[2025]["cash_total"] == 2745521025.0, annual[2025]
    assert annual[2025]["net_profit"] == 4415263147.79, annual[2025]
    assert annual[2025]["payout_ratio"] == 62.2, annual[2025]
    assert annual[2025]["payout_basis"] == "归母净利润", annual[2025]
    assert annual[2025]["periods"] == ["2025-06-30", "2025-12-31"], annual[2025]
    assert annual[2024]["payout_ratio"] == 69.1, annual[2024]
    assert annual[2021]["payout_ratio"] == 100.6, annual[2021]
    assert annual[2026]["net_profit"] is None, annual[2026]
    assert annual[2026]["payout_ratio"] is None, annual[2026]

    notes = data["notes"]
    assert any("按税前每 10 股派息" in note for note in notes), notes
    assert any("按报告期归年" in note for note in notes), notes
    assert any("公告时点股价" in note for note in notes), notes
    assert any("2026 年度缺少年报净利润" in note for note in notes), notes
    assert any("分红率超过 100%：2021 年 100.6%" in note for note in notes), notes


def test_window_and_dedupe() -> None:
    data = div.dividends_from_rows(
        ROWS, CODE, years=2, financial_rows=FINANCIAL
    )
    assert [row["year"] for row in data["annual"]] == [2025, 2026], data["annual"]
    assert [row["fiscal_year"] for row in data["events"]] == [
        2026, 2025, 2025
    ], data["events"]

    duplicate = _row(
        "2025-12-31", 10, 578195410,
        notice="2025-01-01 00:00:00", plan="旧方案",
    )
    kept = div._dedupe([
        {"report_date": "2025-12-31", "notice_date": "2026-05-12",
         "plan": "新方案"},
        {"report_date": "2025-12-31", "notice_date": "2025-01-01",
         "plan": "旧方案"},
    ])
    assert len(kept) == 1 and kept[0]["plan"] == "新方案", kept
    assert duplicate["NOTICE_DATE"] == "2025-01-01 00:00:00", duplicate


def test_share_bonus() -> None:
    rows = [
        _row(
            "2024-12-31", 39.74, 1_000_000_000,
            bonus=8, transfer=12, share_bonus=20,
            notice="2025-03-20 00:00:00",
            plan="10送8股转12股派39.74元(含税,扣税后34.966元)",
        )
    ]
    data = div.dividends_from_rows(rows, "002594")
    event = data["latest"]
    assert event["bonus_per_10"] == 8, event
    assert event["transfer_per_10"] == 12, event
    assert event["share_bonus_per_10"] == 20, event
    assert event["cash_total"] == 3_974_000_000.0, event
    assert event["plan_short"] == "10送8股转12股派39.74元", event
    assert data["annual"][0]["share_bonus_count"] == 1, data["annual"]


def test_no_financial() -> None:
    data = div.dividends_from_rows(ROWS, CODE)
    annual = {row["year"]: row for row in data["annual"]}
    assert annual[2025]["net_profit"] is None, annual[2025]
    assert annual[2025]["payout_ratio"] is None, annual[2025]
    assert data["counts"]["consecutive_years"] == 5, data["counts"]
    assert any("2024、2025、2026 等年度缺少年报净利润" in note
               for note in data["notes"]), data["notes"]


def test_empty() -> None:
    data = div.dividends_from_rows([], CODE)
    assert data["available"] is True, data
    assert data["events"] == [] and data["annual"] == [], data
    assert data["has_dividends"] is False, data
    assert data["notes"] == ["没有取到分红记录，可能从未分红或数据源未收录"], data
    assert data["summary"].startswith(f"{CODE} · 暂无分红记录"), data["summary"]


def test_errors() -> None:
    try:
        div.dividends_from_rows("bad", CODE)  # type: ignore[arg-type]
    except div.DividendError:
        pass
    else:
        raise AssertionError("返回格式异常时应该抛 DividendError")

    original = div._get_page
    div._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "报表配置不存在", "result": None,
    }
    try:
        try:
            div.fetch_dividends(CODE)
        except div.DividendError as error:
            assert "报表配置不存在" in str(error), error
        else:
            raise AssertionError("接口失败时应该抛 DividendError")
    finally:
        div._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = div.dividends_from_rows(ROWS, CODE, financial_rows=FINANCIAL)
    text = div.format_dividends(data, code=CODE, name=NAME, as_of=data["as_of"])

    assert text.startswith(
        f"{NAME}({CODE}) · 最新报告期 2026-06-30 · 最近现金分红 2026 年"
        f" · 连续分红 5 年"
    ), text
    assert "2025 年度：现金分红 27.46亿，归母净利润 44.15亿，分红率 62.2%" in text, text
    assert (
        "最新方案：2026-06-30 10派30.00元，合计 22.03亿"
        "（实施分配），除权除息 2026-09-24"
    ) in text, text
    assert (
        "年度现金分红：2022 6.00亿(41.7%) / 2023 8.00亿(39.2%)"
        " / 2024 23.00亿(69.1%) / 2025 27.46亿(62.2%) / 2026 22.03亿"
    ) in text, text
    assert "提示：" in text, text
    assert text.rstrip().endswith(f"数据来源：{div.SOURCE_LABEL}"), text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="dividends_test_")
    div.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = div.dividends_from_rows(ROWS, CODE)["events"]
    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {"code": code, "events": events, "count": len(events)}

    div.fetch_dividends = fake_fetch  # type: ignore[assignment]

    first = div.ensure_dividends(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == 9, first
    assert first["data"]["fetched_at"], first

    second = div.ensure_dividends(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    div.ensure_dividends(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = div._age_hours
    div._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    div.ensure_dividends(CODE)
    assert calls["fetch"] == 3, calls
    div._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise div.DividendError("接口超时")

    div.fetch_dividends = broken  # type: ignore[assignment]
    stale = div.ensure_dividends(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == 9, stale

    try:
        div.ensure_dividends("600000")
    except div.DividendError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 DividendError")

    try:
        div.ensure_dividends("")
    except div.DividendError:
        pass
    else:
        raise AssertionError("空代码应该抛 DividendError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="dividends_build_")
    div.DB_PATH = os.path.join(tmpdir, "stock.db")

    div.fetch_dividends = lambda code: {  # type: ignore[assignment]
        "code": code,
        "events": div.dividends_from_rows(ROWS, code)["events"],
        "count": len(ROWS),
    }

    class FakeStock:
        name = NAME

    original_stock = div.get_stock
    original_financials = div.ensure_financials
    div.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    div.ensure_financials = lambda code: FINANCIAL  # type: ignore[assignment]
    try:
        data = div.build_dividends(CODE, years=5)
    finally:
        div.get_stock = original_stock  # type: ignore[assignment]
        div.ensure_financials = original_financials  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["consecutive_years"] == 5, data["counts"]
    assert data["sources"] == {div.SOURCE_LABEL: "2026-06-30"}, data
    assert data["url"] == "https://data.eastmoney.com/yjfp/detail/605499.html", data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]

    # 财报失败时仍返回分红历史，只是不计算分红率
    div.fetch_dividends = lambda code: {  # type: ignore[assignment]
        "code": code,
        "events": div.dividends_from_rows(ROWS, code)["events"],
        "count": len(ROWS),
    }
    div.ensure_financials = lambda code: (_ for _ in ()).throw(  # type: ignore[assignment]
        div.FinanceError("财报接口超时")
    )
    try:
        fallback = div.build_dividends(CODE, force=True)
    finally:
        div.ensure_financials = original_financials  # type: ignore[assignment]
    assert fallback["annual"][-1]["payout_ratio"] is None, fallback["annual"]
    assert any("缺少年报净利润" in note for note in fallback["notes"]), fallback


def test_page_url() -> None:
    assert div.PAGE_URL.format(code=CODE) == (
        "https://data.eastmoney.com/yjfp/detail/605499.html"
    )


def main() -> None:
    test_from_rows()
    test_window_and_dedupe()
    test_share_bonus()
    test_no_financial()
    test_empty()
    test_errors()
    test_format()
    test_cache()
    test_build()
    test_page_url()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
