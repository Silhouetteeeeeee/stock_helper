"""业绩预告验证：同一报告期多指标归并、修订去重与本地缓存。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.forecasts as forecast

CODE = "002230"
NAME = "科大讯飞"
TODAY = "2026-10-25"


def _row(
    report: str,
    notice: str,
    finance_code: str,
    label: str,
    *,
    lower: float | None = None,
    upper: float | None = None,
    yoy_low: float | None = None,
    yoy_high: float | None = None,
    predict_type: str = "略增",
    prior: float | None = None,
    mid: float | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "SECURITY_CODE": CODE,
        "SECURITY_NAME_ABBR": NAME,
        "REPORT_DATE": f"{report} 00:00:00",
        "NOTICE_DATE": f"{notice} 00:00:00",
        "PREDICT_FINANCE_CODE": finance_code,
        "PREDICT_FINANCE": label,
        "PREDICT_AMT_LOWER": lower,
        "PREDICT_AMT_UPPER": upper,
        "ADD_AMP_LOWER": yoy_low,
        "ADD_AMP_UPPER": yoy_high,
        "PREDICT_TYPE": predict_type,
        "PREYEAR_SAME_PERIOD": prior,
        "FORECAST_JZ": mid,
        "CHANGE_REASON_EXPLAIN": reason,
    }


ROWS = [
    _row("2026-06-30", "2026-07-15", "004", "归属于上市公司股东的净利润",
         lower=-228_000_000, upper=-180_000_000, yoy_low=5, yoy_high=25,
         predict_type="减亏", prior=-238_927_200, mid=-204_000_000,
         reason="研发投入增加，收入增长"),
    _row("2026-06-30", "2026-07-15", "003", "每股收益",
         lower=-0.0967, upper=-0.0764, predict_type="减亏",
         prior=-0.1034, mid=-0.08655),
    _row("2026-06-30", "2026-07-15", "005", "扣除非经常性损益后的净利润",
         lower=-675_000_000, upper=-600_000_000, yoy_low=-85, yoy_high=-65,
         predict_type="增亏", prior=-364_169_800, mid=-637_500_000),
    # 同一报告期的旧修订，应该被更新的公告覆盖
    _row("2026-06-30", "2026-06-01", "004", "归属于上市公司股东的净利润",
         lower=-300_000_000, upper=-250_000_000, predict_type="首亏"),
    _row("2026-03-31", "2026-04-15", "004", "归属于上市公司股东的净利润",
         lower=100_000_000, upper=120_000_000, yoy_low=10, yoy_high=20,
         predict_type="略增", mid=110_000_000),
    _row("2025-12-31", "2026-01-20", "004", "归属于上市公司股东的净利润",
         lower=500_000_000, upper=600_000_000, yoy_low=30, yoy_high=40,
         predict_type="预增", mid=550_000_000),
    _row("2025-09-30", "2025-10-15", "004", "归属于上市公司股东的净利润",
         lower=50_000_000, upper=60_000_000, yoy_low=-20, yoy_high=-10,
         predict_type="预减", mid=55_000_000),
    _row("2025-06-30", "2025-07-15", "004", "归属于上市公司股东的净利润",
         lower=-238_000_000, upper=-200_000_000, yoy_low=5, yoy_high=10,
         predict_type="减亏", mid=-219_000_000),
    _row("2024-12-31", "2025-01-10", "004", "归属于上市公司股东的净利润",
         lower=400_000_000, upper=450_000_000, yoy_low=15, yoy_high=20,
         predict_type="略增", mid=425_000_000),
    # 三年窗口外
    _row("2023-12-31", "2023-12-20", "004", "归属于上市公司股东的净利润",
         lower=300_000_000, upper=350_000_000, predict_type="略增"),
]


def test_from_rows() -> None:
    data = forecast.forecasts_from_rows(ROWS, CODE, years=3, today=TODAY)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-07-15", data
    assert data["window_start"] == "2024-01-01", data
    assert data["window_end"] == "2026-10-25", data
    assert data["years"] == 3, data
    assert data["has_forecasts"] is True, data

    counts = data["counts"]
    assert counts == {
        "events": 6,
        "latest_report_date": "2026-06-30",
        "latest_notice_date": "2026-07-15",
        "latest_type": "减亏",
        "latest_net_profit_low": -228_000_000,
        "latest_net_profit_high": -180_000_000,
        "latest_net_profit_yoy_low": 5.0,
        "latest_net_profit_yoy_high": 25.0,
        "latest_eps_low": -0.0967,
        "latest_eps_high": -0.0764,
        "latest_revenue_low": None,
        "latest_revenue_high": None,
    }, counts

    latest = data["latest"]
    assert latest["report_date"] == "2026-06-30", latest
    assert latest["notice_date"] == "2026-07-15", latest
    assert latest["type"] == "减亏", latest
    assert len(latest["metrics"]) == 3, latest
    assert [item["finance_code"] for item in latest["metrics"]] == [
        "004", "005", "003",
    ], latest["metrics"]
    net = latest["metrics"][0]
    assert net["label"] == "归母净利润", net
    assert net["lower"] == -228_000_000, net
    assert net["upper"] == -180_000_000, net
    assert net["yoy_low"] == 5, net
    assert net["yoy_high"] == 25, net

    assert [item["report_date"] for item in data["events"]] == [
        "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30",
        "2025-06-30", "2024-12-31",
    ], data["events"]

    annual = {item["year"]: item for item in data["annual"]}
    assert sorted(annual) == [2024, 2025, 2026], annual
    assert annual[2024]["events"] == 1, annual[2024]
    assert annual[2025]["events"] == 3, annual[2025]
    assert annual[2026]["events"] == 2, annual[2026]

    types = {item["type"]: item["events"] for item in data["type_counts"]}
    assert types == {"减亏": 2, "略增": 2, "预增": 1, "预减": 1}, types

    notes = data["notes"]
    assert any("初步测算" in note for note in notes), notes
    assert any("定期报告" in note for note in notes), notes


def test_default_and_window() -> None:
    default = forecast.forecasts_from_rows(ROWS, CODE, today=TODAY)
    assert default["years"] == 3, default
    assert default["window_start"] == "2024-01-01", default
    assert default["counts"]["events"] == 6, default["counts"]

    wide = forecast.forecasts_from_rows(ROWS, CODE, years=99, today=TODAY)
    assert wide["years"] == 10, wide
    assert wide["counts"]["events"] == 7, wide["counts"]

    bad = forecast.forecasts_from_rows(ROWS, CODE, years="bad", today=TODAY)
    assert bad["years"] == 3, bad


def test_empty_and_errors() -> None:
    data = forecast.forecasts_from_rows([], CODE, today=TODAY)
    assert data["available"] is True, data
    assert data["has_forecasts"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None, data
    assert data["events"] == [] and data["annual"] == [], data
    assert data["counts"]["events"] == 0, data["counts"]
    assert any("没有取到" in note for note in data["notes"]), data["notes"]

    try:
        forecast.forecasts_from_rows("bad", CODE)  # type: ignore[arg-type]
    except forecast.ForecastError:
        pass
    else:
        raise AssertionError("非列表输入应抛 ForecastError")

    try:
        forecast.fetch_forecasts("")
    except forecast.ForecastError:
        pass
    else:
        raise AssertionError("空代码应抛 ForecastError")


def test_window_empty() -> None:
    old = [
        _row("2023-12-31", "2023-12-20", "004",
             "归属于上市公司股东的净利润", lower=300_000_000,
             upper=350_000_000, predict_type="略增")
    ]
    data = forecast.forecasts_from_rows(old, CODE, years=3, today=TODAY)
    assert data["as_of"] == "2023-12-20", data
    assert data["latest"]["report_date"] == "2023-12-31", data
    assert data["counts"]["events"] == 0, data["counts"]
    assert data["events"] == [], data
    assert any("近3年" in note for note in data["notes"]), data["notes"]


def test_fetch_error() -> None:
    original = forecast._get_page
    forecast._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "接口超时",
    }
    try:
        forecast.fetch_forecasts(CODE)
    except forecast.ForecastError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 ForecastError")

    forecast._get_page = lambda code, page: {  # type: ignore[assignment]
        "success": False, "message": "返回数据为空",
    }
    try:
        empty = forecast.fetch_forecasts(CODE)
        assert empty["events"] == [], empty
    finally:
        forecast._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = forecast.forecasts_from_rows(ROWS, CODE, years=3, today=TODAY)
    text = forecast.format_forecasts(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "业绩预告近3年" in text, text
    assert "最新：2026-06-30（公告 2026-07-15）减亏" in text, text
    assert (
        "归母净利润：-2.28亿元~-1.80亿元，同比 +5.0%~+25.0%"
    ) in text, text
    assert (
        "扣非净利润：-6.75亿元~-6.00亿元，同比 -85.0%~-65.0%"
    ) in text, text
    assert "每股收益：-0.0967元~-0.0764元" in text, text
    assert "近3年：6 期" in text, text
    assert "减亏 2" in text and "略增 2" in text, text
    assert "数据来源：" in text, text

    empty = forecast.forecasts_from_rows([], CODE, today=TODAY)
    empty_text = forecast.format_forecasts(empty, code=CODE, name=NAME)
    assert "暂无已披露的业绩预告" in empty_text, empty_text

    old = forecast.forecasts_from_rows(
        [_row("2023-12-31", "2023-12-20", "004",
              "归属于上市公司股东的净利润", lower=300_000_000,
              upper=350_000_000, predict_type="略增")],
        CODE, years=3, today=TODAY,
    )
    old_text = forecast.format_forecasts(old, code=CODE, name=NAME)
    assert "近3年没有已披露的业绩预告" in old_text, old_text
    assert "最近一次为 2023-12-31" in old_text, old_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="forecast_cache_")
    forecast.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = [
        item for event in forecast.forecasts_from_rows(
            ROWS, CODE, years=10, today=TODAY
        )["events"]
        for item in event["metrics"]
    ]
    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {"code": code, "events": events, "count": len(events)}

    forecast.fetch_forecasts = fake_fetch  # type: ignore[assignment]

    first = forecast.ensure_forecasts(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(events), first
    assert first["data"]["fetched_at"], first

    second = forecast.ensure_forecasts(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    forecast.ensure_forecasts(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = forecast._age_hours
    forecast._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    forecast.ensure_forecasts(CODE)
    assert calls["fetch"] == 3, calls
    forecast._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise forecast.ForecastError("接口超时")

    forecast.fetch_forecasts = broken  # type: ignore[assignment]
    stale = forecast.ensure_forecasts(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(events), stale

    try:
        forecast.ensure_forecasts("600000")
    except forecast.ForecastError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 ForecastError")

    try:
        forecast.ensure_forecasts("")
    except forecast.ForecastError:
        pass
    else:
        raise AssertionError("空代码应该抛 ForecastError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="forecast_build_")
    forecast.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = [
        item for event in forecast.forecasts_from_rows(
            ROWS, CODE, years=10, today=TODAY
        )["events"]
        for item in event["metrics"]
    ]
    forecast.fetch_forecasts = lambda code: {  # type: ignore[assignment]
        "code": code, "events": events, "count": len(events),
    }

    class FakeStock:
        name = NAME

    original_stock = forecast.get_stock
    forecast.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = forecast.build_forecasts(CODE, years=3, today=TODAY)
    finally:
        forecast.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["events"] == 6, data["counts"]
    assert data["sources"] == {forecast.SOURCE_LABEL: "2026-07-15"}, data
    assert data["url"] == (
        "https://data.eastmoney.com/bbsj/yjyg.html?code=002230"
    ), data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]


def test_page_url() -> None:
    assert forecast.PAGE_URL.format(code=CODE) == (
        "https://data.eastmoney.com/bbsj/yjyg.html?code=002230"
    )


def main() -> None:
    test_from_rows()
    test_default_and_window()
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
