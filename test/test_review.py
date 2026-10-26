"""模块 6 验证：区间收益、方向命中、个股/行业/大盘评估、汇总与存档编排。"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.review as review


def _dates(count: int, end: date = date(2026, 10, 23)) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y-%m-%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _rows(closes: list[float], days: list[str] | None = None) -> list[dict]:
    days = days or _dates(len(closes))
    return [
        {"date": day, "close": close}
        for day, close in zip(days, closes)
    ]


def _benchmark(closes: list[float]) -> dict:
    return review._series(_rows(closes))


def _reset_cache() -> None:
    review._cache.update({"at": None, "key": None, "data": None})


def test_forward_window() -> None:
    dates = _dates(7)
    series = review._series(_rows([10, 10.5, 9.5, 11, 12, 13, 14], dates))
    window = review._forward_window(series, dates[0], 5)
    assert window["entry_date"] == dates[0]
    assert window["end_date"] == dates[5]
    assert window["days"] == 5
    assert window["complete"] is True
    assert abs(window["return_pct"] - 30.0) < 1e-9
    assert abs(window["max_drawdown_pct"] - (-5.0)) < 1e-9
    assert abs(window["max_gain_pct"] - 30.0) < 1e-9

    partial = review._forward_window(series, dates[1], 2)
    assert partial["return_pct"] == round((11 / 10.5 - 1) * 100, 2)

    pending = review._forward_window(series, dates[-2], 5)
    assert pending["complete"] is False
    assert pending["days"] == 1
    assert pending["return_pct"] == round((14 / 13 - 1) * 100, 2)

    assert review._forward_window({"dates": [], "closes": []}, dates[0], 5) is None


def test_window_return_and_direction_hit() -> None:
    dates = _dates(5)
    benchmark = _benchmark([100, 101, 102, 103, 104])
    assert abs(review._window_return(benchmark, dates[0], dates[3]) - 3.0) < 1e-9
    assert review._window_return(benchmark, dates[3], dates[0]) is None

    assert review._direction_hit("强势", 0.5) is True
    assert review._direction_hit("弱势", -0.5) is True
    assert review._direction_hit("极端风险", -1.0) is True
    assert review._direction_hit("震荡", 2.0) is True
    assert review._direction_hit("震荡", 4.0) is False
    assert review._direction_hit(None, 1.0) is None
    assert review._direction_hit("强势", None) is None


def test_evaluate_market() -> None:
    dates = _dates(7)
    falling = _benchmark([100, 99.5, 99, 98.5, 98, 97.5, 97])
    result = review.evaluate_market(
        {"state": "弱势", "risk_level": "高"}, falling, dates[0], 5
    )
    assert result["status"] == "已完成"
    assert result["hit"] is True
    assert abs(result["benchmark_return_pct"] - (-2.5)) < 1e-9
    assert result["expectation"]

    wrong = review.evaluate_market({"state": "强势"}, falling, dates[0], 5)
    assert wrong["hit"] is False

    pending = review.evaluate_market({"state": "强势"}, falling, dates[-1], 5)
    assert pending["status"] == "待观察"
    assert pending["hit"] is None


def test_evaluate_candidate() -> None:
    dates = _dates(7)
    benchmark = _benchmark([100, 100.2, 100.4, 100.6, 100.8, 101, 101.2])
    rows = _rows([10, 10.2, 10.4, 10.6, 10.8, 11, 11.2], dates)
    result = review.evaluate_candidate(
        {"code": "sh600519", "name": "贵州茅台", "score": 7.5},
        rows, benchmark, dates[0], 5,
    )
    assert result["status"] == "已完成"
    assert abs(result["return_pct"] - 10.0) < 1e-9
    assert abs(result["benchmark_return_pct"] - 1.0) < 1e-9
    assert abs(result["excess_pct"] - 9.0) < 1e-9
    assert abs(result["net_return_pct"] - (10 - review.ROUND_TRIP_COST_PCT)) < 1e-9
    assert result["hit"] is True

    pending = review.evaluate_candidate(
        {"code": "sh600519", "name": "贵州茅台"}, rows, benchmark, dates[-1], 5
    )
    assert pending["status"] == "待观察"
    assert pending["hit"] is None

    missing = review.evaluate_candidate(
        {"code": "sz000001", "name": "平安银行"}, [], benchmark, dates[0], 5
    )
    assert missing["status"] == "数据不足"
    assert missing["hit"] is None


def test_evaluate_sector() -> None:
    dates = _dates(7)
    benchmark = _benchmark([100] * 7)
    rising = _rows([1000, 1010, 1020, 1030, 1040, 1050, 1060], dates)
    falling = _rows([1000, 995, 990, 985, 980, 975, 970], dates)

    leader = review.evaluate_sector("半导体", rising, benchmark, dates[0], 5)
    assert leader["bucket"] == "领涨"
    assert leader["hit"] is True
    assert leader["excess_pct"] > 0

    weak_hit = review.evaluate_sector(
        "银行", falling, benchmark, dates[0], 5, expect_positive=False
    )
    assert weak_hit["bucket"] == "弱势"
    assert weak_hit["hit"] is True

    weak_miss = review.evaluate_sector(
        "银行", rising, benchmark, dates[0], 5, expect_positive=False
    )
    assert weak_miss["hit"] is False


def test_summary_and_mistakes() -> None:
    dates = _dates(7)
    benchmark = _benchmark([100, 100.2, 100.4, 100.6, 100.8, 101, 101.2])
    stock_up = _rows([10, 10.2, 10.4, 10.6, 10.8, 11, 11.2], dates)
    stock_down = _rows([10, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4], dates)
    sector_up = _rows([1000, 1010, 1020, 1030, 1040, 1050, 1060], dates)

    row = {
        "trade_date": dates[0],
        "market": review.evaluate_market({"state": "强势"}, benchmark, dates[0], 5),
        "sectors": [
            review.evaluate_sector("半导体", sector_up, benchmark, dates[0], 5),
            review.evaluate_sector(
                "银行", sector_up, benchmark, dates[0], 5, expect_positive=False
            ),
        ],
        "candidates": [
            review.evaluate_candidate(
                {"code": "sh600519", "name": "贵州茅台"}, stock_up, benchmark,
                dates[0], 5,
            ),
            review.evaluate_candidate(
                {"code": "sz000001", "name": "平安银行"}, stock_down, benchmark,
                dates[0], 5,
            ),
        ],
    }
    row["mistakes"] = review._mistakes(row)
    summary = review._summary([row])

    assert summary["reports"] == 1
    assert summary["evaluated"] == 1
    assert summary["market"]["judged"] == 1
    assert summary["market"]["hit_rate_pct"] == 100.0
    assert summary["sectors"]["judged"] == 2
    assert summary["sectors"]["hit_rate_pct"] == 50.0
    assert summary["candidates"]["judged"] == 2
    assert summary["candidates"]["hit_rate_pct"] == 50.0
    assert summary["candidates"]["avg_return_pct"] is not None
    assert summary["candidates"]["worst_drawdown_pct"] <= 0
    assert summary["candidates"]["cost_pct"] == review.ROUND_TRIP_COST_PCT
    assert abs(
        summary["candidates"]["net_avg_excess_pct"]
        - (summary["candidates"]["avg_excess_pct"] - review.ROUND_TRIP_COST_PCT)
    ) < 1e-9
    types = {item["type"] for item in summary["mistakes"]}
    assert types == {"行业判断", "候选股"}
    assert any("平安银行" in item["detail"] for item in summary["mistakes"])


def test_build_snapshot() -> None:
    originals = (
        review.build_market_overview,
        review.classify_market,
        review.build_sector_rotation,
        review.screen_candidates,
    )
    review.build_market_overview = lambda: {"as_of": "2026-10-23 14:30:00"}
    review.classify_market = lambda overview: {
        "state": "震荡", "risk_level": "中", "confidence": "高",
        "basis": [{"factor": "趋势", "reading": "指数在20日线附近"}],
        "invalidation": ["沪深300 跌破 4000 点"],
        "metrics": {"index_chg_pct": 0.3},
    }
    review.build_sector_rotation = lambda force=False: {
        "trade_date": "2026-10-23",
        "leaders": [{"name": "半导体", "score": 7.2, "trend": "持续强势",
                     "excess_5d": 3.1}],
        "weak": [{"name": "银行", "score": -3.0, "trend": "持续弱势",
                  "excess_5d": -1.5}],
        "emerging": [{"name": "游戏"}],
        "weakening": [],
        "breadth": {"up_industries": 40, "down_industries": 50},
    }
    review.screen_candidates = lambda force=False: {
        "candidates": [
            {"code": "sh600519", "name": "贵州茅台", "board": "沪市主板",
             "score": 7.5, "price": 1500.0},
            {"code": "sz000001", "name": "平安银行", "board": "深市主板",
             "score": 6.1, "price": 11.0},
        ],
    }
    try:
        snapshot = review.build_snapshot(strategy={
            "stance": "观望",
            "caps": {"total_pct": 60, "single_pct": 12, "stop_loss_pct": 7},
            "profile": {"position_pct": 55},
        })
    finally:
        (
            review.build_market_overview,
            review.classify_market,
            review.build_sector_rotation,
            review.screen_candidates,
        ) = originals

    assert snapshot["trade_date"] == "2026-10-23"
    assert snapshot["market"]["state"] == "震荡"
    assert snapshot["sectors"]["leaders"][0]["name"] == "半导体"
    assert [item["code"] for item in snapshot["candidates"]] == [
        "sh600519", "sz000001",
    ]
    assert snapshot["strategy"] == {
        "stance": "观望", "total_pct": 60, "single_pct": 12,
        "stop_loss_pct": 7, "position_pct": 55,
    }
    assert snapshot["errors"] == {}


def test_build_snapshot_all_sources_fail() -> None:
    originals = (
        review.build_market_overview,
        review.build_sector_rotation,
        review.screen_candidates,
    )

    def broken(*args, **kwargs):
        raise RuntimeError("接口超时")

    review.build_market_overview = broken
    review.build_sector_rotation = broken
    review.screen_candidates = broken
    try:
        snapshot = review.build_snapshot()
    finally:
        (
            review.build_market_overview,
            review.build_sector_rotation,
            review.screen_candidates,
        ) = originals

    assert set(snapshot["errors"]) == {"market", "sectors", "candidates"}
    assert any("不保存" in note for note in snapshot["notes"])
    assert snapshot["trade_date"]


def test_save_daily_judgment() -> None:
    originals = (review.build_snapshot, review.save_report)
    saved: list[dict] = []

    review.build_snapshot = lambda strategy=None, force=False: {
        "trade_date": "2026-10-23", "market": {"state": "震荡"},
        "sectors": {}, "candidates": [],
    }
    review.save_report = lambda report: (
        saved.append(report) or {"trade_date": "2026-10-23",
                                 "saved_at": "x", "replaced": False}
    )
    try:
        result = review.save_daily_judgment()
        assert result["saved"] is True
        assert result["store"]["replaced"] is False
        assert saved[0]["trade_date"] == "2026-10-23"

        review.build_snapshot = lambda strategy=None, force=False: {
            "trade_date": "2026-10-23", "market": {}, "sectors": {},
            "candidates": [], "notes": ["全部数据源都失败，这份快照不保存"],
        }
        empty = review.save_daily_judgment()
        assert empty["saved"] is False
        assert len(saved) == 1
    finally:
        review.build_snapshot, review.save_report = originals


def test_build_review() -> None:
    _reset_cache()
    dates = _dates(20)
    benchmark = _benchmark([100 + index * 0.2 for index in range(20)])
    stock = _rows([10 + index * 0.1 for index in range(20)], dates)
    sector_rise = _rows([1000 + index * 5 for index in range(20)], dates)
    sector_mixed = _rows([1000 - index * 5 for index in range(20)], dates)

    reports = [
        {
            "trade_date": dates[-1], "saved_at": "2026-10-23 15:10:00",
            "market": {"state": "强势", "risk_level": "低"},
            "sectors": {"leaders": [{"name": "半导体", "score": 8}],
                        "weak": [{"name": "银行", "score": -2}]},
            "candidates": [{"code": "sh600519", "name": "贵州茅台", "score": 7}],
            "strategy": None,
        },
        {
            "trade_date": dates[5], "saved_at": "2026-10-20 15:10:00",
            "market": {"state": "强势", "risk_level": "低"},
            "sectors": {"leaders": [{"name": "半导体", "score": 8}],
                        "weak": [{"name": "银行", "score": -2}]},
            "candidates": [{"code": "sh600519", "name": "贵州茅台", "score": 7}],
            "strategy": {"stance": "观望", "total_pct": 60},
        },
    ]

    originals = (
        review.load_reports, review._benchmark_history,
        review._stock_history, review._sector_history,
    )
    review.load_reports = lambda limit=60: list(reports)
    review._benchmark_history = lambda start, end: benchmark
    review._stock_history = lambda code, start, end: stock
    review._sector_history = lambda name, start, end: (
        sector_rise if name == "半导体" else sector_mixed
    )
    try:
        result = review.build_review(limit=10, horizon=5, force=True)
        row_new, row_old = result["reports"][0], result["reports"][1]
        assert row_new["trade_date"] == dates[-1]
        assert row_new["market"]["status"] == "待观察"
        assert row_old["market"]["status"] == "已完成"
        assert row_old["market"]["hit"] is True
        assert row_old["sectors"][0]["name"] == "半导体"
        assert row_old["sectors"][0]["hit"] is True
        assert row_old["sectors"][1]["bucket"] == "弱势"
        assert row_old["sectors"][1]["hit"] is True
        assert row_old["candidates"][0]["hit"] is True
        assert row_old["complete"] is True

        summary = result["summary"]
        assert summary["reports"] == 2
        assert summary["evaluated"] == 1
        assert summary["market"]["judged"] == 1
        assert summary["candidates"]["judged"] == 1
        assert summary["candidates"]["net_avg_excess_pct"] == round(
            summary["candidates"]["avg_excess_pct"] - review.ROUND_TRIP_COST_PCT, 2
        )
        assert any("待观察" in note for note in result["notes"])
        assert result["benchmark"] == review.BENCHMARK_NAME

        second = review.build_review(limit=10, horizon=5)
        assert second is result
        refreshed = review.build_review(limit=10, horizon=5, force=True)
        assert refreshed is not result
    finally:
        (
            review.load_reports, review._benchmark_history,
            review._stock_history, review._sector_history,
        ) = originals


def test_build_review_empty_and_benchmark_failure() -> None:
    _reset_cache()
    original = review.load_reports
    review.load_reports = lambda limit=60: []
    try:
        empty = review.build_review(force=True)
    finally:
        review.load_reports = original
    assert empty["summary"] is None
    assert any("还没有保存" in note for note in empty["notes"])

    _reset_cache()
    dates = _dates(10)
    reports = [{
        "trade_date": dates[3], "saved_at": "x",
        "market": {"state": "震荡"}, "sectors": {},
        "candidates": [{"code": "sh600519", "name": "贵州茅台"}],
    }]
    originals = (review.load_reports, review._benchmark_history,
                 review._stock_history)
    review.load_reports = lambda limit=60: list(reports)

    def broken(start, end):
        raise RuntimeError("指数接口超时")

    review._benchmark_history = broken
    review._stock_history = lambda code, start, end: _rows(
        [10 + index * 0.1 for index in range(len(dates))], dates
    )
    try:
        result = review.build_review(force=True)
    finally:
        review.load_reports, review._benchmark_history, review._stock_history = originals

    assert "benchmark" in result["errors"]
    assert result["missing"]
    candidate = result["reports"][0]["candidates"][0]
    assert candidate["excess_pct"] is None
    assert candidate["hit"] is None


def main() -> None:
    test_forward_window()
    test_window_return_and_direction_hit()
    test_evaluate_market()
    test_evaluate_candidate()
    test_evaluate_sector()
    test_summary_and_mistakes()
    test_build_snapshot()
    test_build_snapshot_all_sources_fail()
    test_save_daily_judgment()
    test_build_review()
    test_build_review_empty_and_benchmark_failure()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
