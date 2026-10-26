"""模块 3 验证：区间收益对齐、超额、相对强度天数、轮动标签与汇总。"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.sector_rotation as rotation


class Frame:
    def __init__(self, rows):
        self.rows = list(rows)

    @property
    def empty(self):
        return not self.rows

    def to_dict(self, orient="records"):
        assert orient == "records"
        return [dict(row) for row in self.rows]


def _dates(count: int, end: date = date(2026, 10, 23)) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y-%m-%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _history(closes, amounts=None) -> list[dict]:
    days = _dates(len(closes))
    amounts = amounts or [1e9] * len(closes)
    return [
        {
            "日期": day,
            "开盘价": close,
            "最高价": close,
            "最低价": close,
            "收盘价": close,
            "成交量": 1e6,
            "成交额": amount,
        }
        for day, close, amount in zip(days, closes, amounts)
    ]


def _benchmark(values) -> dict:
    return rotation.make_benchmark(dict(zip(_dates(len(values)), values)))


def _summary(name, chg, up=10, down=5, amount=100.0, inflow=1.0):
    return {
        "板块": name,
        "涨跌幅": chg,
        "总成交额": amount,
        "净流入": inflow,
        "上涨家数": up,
        "下跌家数": down,
        "领涨股": f"{name}龙头",
        "领涨股-涨跌幅": 9.98,
    }


def test_aligned_return_uses_last_common_day() -> None:
    days = _dates(10)
    closes = [100.0 + index for index in range(10)]
    latest = rotation._aligned_return(days, closes, days[-1], 5)
    assert latest is not None and abs(latest - (109 / 104 - 1) * 100) < 1e-9
    # 终点早于最后一根 K 线时，用终点当天往前数
    earlier = rotation._aligned_return(days, closes, days[5], 5)
    assert earlier is not None and abs(earlier - 5.0) < 1e-9
    assert rotation._aligned_return(days[:3], closes[:3], days[2], 5) is None


def test_rs_days_counts_relative_wins() -> None:
    days = _dates(12)
    rising = [100.0 * (1.01**index) for index in range(12)]
    benchmark = _benchmark([100.0 * (1.002**index) for index in range(12)])["map"]
    assert rotation._rs_days(days, rising, benchmark) == 10
    falling = [100.0 * (0.99**index) for index in range(12)]
    assert rotation._rs_days(days, falling, benchmark) == 0


def test_trend_labels() -> None:
    assert rotation._trend_label(1, 1, 90) == rotation.STRONG
    assert rotation._trend_label(2, 85, 90) == rotation.EMERGING
    assert rotation._trend_label(88, 3, 90) == rotation.WEAKENING
    assert rotation._trend_label(85, 85, 90) == rotation.WEAK
    assert rotation._trend_label(40, 40, 90) == rotation.NEUTRAL
    assert rotation._trend_label(None, 3, 90) == rotation.NEUTRAL
    assert rotation._trend_label(1, 1, 5) == rotation.NEUTRAL


def test_sector_stats_excess_and_breadth() -> None:
    closes = [100.0 * (1.01**index) for index in range(70)]
    bench = _benchmark([100.0 * (1.001**index) for index in range(70)])
    sector = rotation.sector_stats(
        _summary("游戏", 3.29, up=20, down=3, amount=116.64),
        _history(closes),
        bench,
        total=90,
        progress=1.0,
    )
    assert sector["name"] == "游戏"
    assert sector["up_ratio"] == 0.87
    assert sector["excess_5d"] > 0 and sector["excess_20d"] > 0
    assert sector["above_ma20"] is True
    assert sector["rs_days_10"] == 10
    assert sector["history_days"] == 70
    assert sector["leader"] == {"name": "游戏龙头", "chg_pct": 9.98}


def test_sector_stats_without_history() -> None:
    bench = _benchmark([100.0] * 70)
    sector = rotation.sector_stats(_summary("新股板块", 1.0), [], bench, total=90)
    assert sector["ret_5d"] is None
    assert sector["excess_5d"] is None
    assert sector["above_ma20"] is None
    assert sector["rs_days_10"] is None


def test_amount_ratio_uses_session_progress() -> None:
    closes = [100.0] * 30
    bench = _benchmark([100.0] * 30)
    amounts = [1e9] * 30
    # 快照成交额单位是亿元，历史成交额单位是元：10 亿元 == 1e9 元
    full = rotation.sector_stats(
        _summary("半导体", 1.0, amount=10.0),
        _history(closes, amounts),
        bench,
        total=90,
        progress=1.0,
    )
    half = rotation.sector_stats(
        _summary("半导体", 1.0, amount=10.0),
        _history(closes, amounts),
        bench,
        total=90,
        progress=0.5,
    )
    assert full["amount_ratio"] == 1.0
    assert half["amount_ratio"] == 2.0


def test_reversal_signals() -> None:
    sector = {
        "ret_20d": 20.0,
        "excess_5d": -2.0,
        "chg_pct": 0.5,
        "up_ratio": 0.3,
        "amount_ratio": 1.8,
        "above_ma20": False,
        "rank_5d": 80,
        "rank_20d": 2,
        "total": 90,
    }
    signals = rotation._reversal_signals(sector)
    joined = " ".join(signals)
    assert "高位转弱" in joined
    assert "少数个股或权重" in joined
    assert "20日均线下方" in joined


def _fake_market(count: int = 12) -> dict[str, list[dict]]:
    """构造 count 个板块：第一个一路大涨，最后一个一路下跌。"""
    result: dict[str, list[dict]] = {}
    for index in range(count):
        step = 1.012 if index == 0 else (0.99 if index == count - 1 else 1.0)
        closes = [100.0 * (step**step_index) for step_index in range(80)]
        result[f"板块{index:02d}"] = _history(closes)
    return result


def test_build_sector_rotation() -> None:
    histories = _fake_market()
    names = list(histories)
    original = rotation.ak

    class FakeAk:
        @staticmethod
        def stock_board_industry_summary_ths():
            rows = [
                _summary(name, 3.0 if name == names[0] else (-2.0 if name == names[-1] else 0.2))
                for name in names
            ]
            return Frame(rows)

        @staticmethod
        def stock_board_industry_index_ths(symbol, start_date, end_date):
            if symbol == "板块05":
                raise RuntimeError("该板块历史缺失")
            return Frame(histories[symbol])

        @staticmethod
        def stock_zh_index_daily(symbol):
            closes = [100.0 * (1.0005**index) for index in range(80)]
            return Frame(
                [{"date": day, "close": close}
                 for day, close in zip(_dates(80), closes)]
            )

        @staticmethod
        def stock_zh_index_spot_sina():
            return Frame([{"代码": "sh000300", "涨跌幅": 0.5}])

    rotation.ak = FakeAk
    rotation._cache.update({"at": None, "key": None, "data": None})
    try:
        result = rotation.build_sector_rotation(limit=5)
    finally:
        rotation.ak = original

    assert result["total_sectors"] == len(names)
    assert result["benchmark_today"] == 0.5
    assert len(result["leaders"]) == 5
    assert result["leaders"][0]["name"] == names[0]
    assert "板块05" in result["errors"]["history"]
    assert len(result["ranking"]) == len(names) - 1  # 板块05 没有历史，不进打分
    assert result["breadth"]["up_industries"] == len(names) - 1
    assert result["breadth"]["down_industries"] == 1
    for item in result["leaders"]:
        assert item["drivers"]
        assert item["persistence"] in ("高", "中", "低")
        assert item["trend"] in (
            rotation.STRONG, rotation.EMERGING, rotation.WEAKENING,
            rotation.WEAK, rotation.NEUTRAL,
        )
    scores = [item["score"] for item in result["ranking"]]
    assert scores == sorted(scores, reverse=True)


def test_build_sector_rotation_is_cached() -> None:
    histories = _fake_market(count=6)
    names = list(histories)
    original = rotation.ak
    calls: list[int] = []

    class FakeAk:
        @staticmethod
        def stock_board_industry_summary_ths():
            calls.append(1)
            return Frame([_summary(name, 0.2) for name in names])

        @staticmethod
        def stock_board_industry_index_ths(symbol, start_date, end_date):
            return Frame(histories[symbol])

        @staticmethod
        def stock_zh_index_daily(symbol):
            return Frame(
                [{"date": day, "close": 100.0}
                 for day in _dates(80)]
            )

        @staticmethod
        def stock_zh_index_spot_sina():
            return Frame([{"代码": "sh000300", "涨跌幅": 0.0}])

    rotation.ak = FakeAk
    rotation._cache.update({"at": None, "key": None, "data": None})
    try:
        first = rotation.build_sector_rotation(limit=3)
        second = rotation.build_sector_rotation(limit=3)
        third = rotation.build_sector_rotation(limit=3, force=True)
    finally:
        rotation.ak = original
    assert first is second
    assert third is not first
    assert len(calls) == 2, calls


def test_build_sector_rotation_snapshot_failure() -> None:
    original = rotation.ak

    class FakeAk:
        @staticmethod
        def stock_board_industry_summary_ths():
            raise RuntimeError("同花顺接口超时")

    rotation.ak = FakeAk
    rotation._cache.update({"at": None, "key": None, "data": None})
    try:
        result = rotation.build_sector_rotation()
    finally:
        rotation.ak = original
    assert result["leaders"] == []
    assert "同花顺接口超时" in result["errors"]["sectors"]


def main() -> None:
    test_aligned_return_uses_last_common_day()
    test_rs_days_counts_relative_wins()
    test_trend_labels()
    test_sector_stats_excess_and_breadth()
    test_sector_stats_without_history()
    test_amount_ratio_uses_session_progress()
    test_reversal_signals()
    test_build_sector_rotation()
    test_build_sector_rotation_is_cached()
    test_build_sector_rotation_snapshot_failure()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
