"""模块 4 验证：硬过滤、板块配额、个股指标、量化分与汇总。"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.screening as screening


class Frame:
    def __init__(self, rows):
        self.rows = list(rows)

    @property
    def empty(self):
        return not self.rows

    def to_dict(self, orient="records"):
        assert orient == "records"
        return [dict(row) for row in self.rows]

    def tail(self, count):
        return Frame(self.rows[-count:])


def _dates(count: int, end: date = date(2026, 10, 23)) -> list[str]:
    days: list[str] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y-%m-%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _bars(count: int, step: float = 1.005, amount: float = 5e8) -> list[dict]:
    days = _dates(count)
    closes = [100.0 * (step**index) for index in range(count)]
    return [
        {
            "date": day,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1e6,
            "amount": amount,
            "outstanding_share": 1e9,
            "turnover": 0.01,
        }
        for day, close in zip(days, closes)
    ]


def _spot(code, name, price=10.0, amount=5e8, chg=1.0):
    return {
        "代码": code,
        "名称": name,
        "最新价": price,
        "涨跌幅": chg,
        "成交额": amount,
    }


def test_board_of() -> None:
    assert screening.board_of("sh600519") == "沪市主板"
    assert screening.board_of("sz000001") == "深市主板"
    assert screening.board_of("sz300001") == "创业板"
    assert screening.board_of("sh688001") == "科创板"
    assert screening.board_of("bj920000") == "北交所"
    assert screening.board_of("hk00700") == "其他"


def test_exclusion_reason() -> None:
    assert screening.exclusion_reason("sh600001", "ST某某", 10, 5e8) == "ST/退市"
    assert screening.exclusion_reason("sh600001", "*ST某某", 10, 5e8) == "ST/退市"
    assert screening.exclusion_reason("sh600001", "某某退", 10, 5e8) == "ST/退市"
    assert screening.exclusion_reason("sh600001", "N某某", 10, 5e8) == "上市新股"
    assert screening.exclusion_reason("sh600001", "C某某", 10, 5e8) == "上市新股"
    assert screening.exclusion_reason("sh600001", "某某股份", 0, 5e8) == "停牌或无报价"
    assert screening.exclusion_reason("sh600001", "某某股份", 10, 0) == "无成交"
    assert screening.exclusion_reason("sh600001", "某某股份", 10, 5e8) is None


def test_shortlist_quotas_and_filters() -> None:
    rows = [
        _spot("sh600001", "沪一", 10, 9e8),
        _spot("sh600002", "沪二", 10, 8e8),
        _spot("sh600003", "沪三", 10, 7e8),
        _spot("sz000001", "深一", 10, 6e8),
        _spot("sz300001", "创一", 10, 5e8),
        _spot("sh600004", "ST沪四", 10, 50e8),   # ST 再大也不进
        _spot("sh600005", "小成交", 10, 1e7),    # 成交额不足
        _spot("sh600006", "低价", 1.0, 5e8),     # 价格低于门槛
    ]
    picked, stats = screening.shortlist(
        rows, quotas=(("沪市主板", 2), ("深市主板", 1), ("创业板", 1))
    )
    names = [row["名称"] for row in picked]
    assert names == ["沪一", "沪二", "深一", "创一"], names
    assert stats["excluded"]["ST/退市"] == 1
    assert stats["excluded"]["成交额不足"] == 1
    assert stats["excluded"]["价格或成交额不合适"] == 1


def test_stock_stats_metrics() -> None:
    bars = _bars(80, step=1.01)
    spot = _spot("sh600000", "测试股份", price=bars[-1]["close"], amount=10e8)
    stats = screening.stock_stats(spot, bars)
    assert stats["board"] == "沪市主板"
    assert stats["bars"] == 80
    assert stats["above_ma20"] is True and stats["above_ma60"] is True
    assert stats["ma20_above_ma60"] is True
    assert stats["ret_20d"] > 20
    assert 0.9 < stats["pos_60d"] <= 1.0
    assert stats["drawdown_from_high"] < 0
    assert stats["amount_ratio"] is not None
    assert stats["turnover_pct"] == 1.0
    assert stats["market_cap_yi"] == round(bars[-1]["close"] * 1e9 / 1e8, 2)


def test_stock_stats_without_history() -> None:
    stats = screening.stock_stats(_spot("sh600000", "测试股份"), [])
    assert stats["bars"] == 0
    assert stats["ma20"] is None
    assert stats["ret_20d"] is None
    assert stats["pos_60d"] is None
    assert screening.score_stock(stats) is None


def test_score_stock_directions() -> None:
    up = screening.stock_stats(_spot("sh600000", "上涨"), _bars(80, step=1.01))
    down = screening.stock_stats(_spot("sh600000", "下跌"), _bars(80, step=0.995))
    up_score = screening.score_stock(up)
    down_score = screening.score_stock(down)
    assert up_score is not None and down_score is not None
    assert up_score > down_score
    assert -10 <= up_score <= 10

    # 上市不足 60 日：打分打折，而不是直接剔除
    short = screening.stock_stats(_spot("sh600000", "次新"), _bars(50, step=1.01))
    full = screening.stock_stats(_spot("sh600000", "老股"), _bars(80, step=1.01))
    assert screening.score_stock(short) == round(screening.score_stock(full) * 0.9, 2)
    assert screening.score_stock(
        screening.stock_stats(_spot("sh600000", "更次新"), _bars(20))
    ) is None


def test_risks_and_reasons() -> None:
    stats = screening.stock_stats(_spot("sh600000", "上涨"), _bars(80, step=1.02))
    reasons = screening._reasons(stats)
    assert any("站上20日与60日均线" in item for item in reasons)
    assert any("20日" in item for item in reasons)
    risks = screening._risks(stats)
    assert any("60日高点" in item or "20日涨幅" in item for item in risks)


def test_diversify_caps_per_board() -> None:
    items = [
        {"name": f"沪{i}", "board": "沪市主板", "score": 10 - i} for i in range(6)
    ] + [{"name": "深0", "board": "深市主板", "score": 1}]
    picked = screening.diversify(items, limit=4, per_board=2)
    boards = [item["board"] for item in picked]
    assert boards.count("沪市主板") == 2
    assert len(picked) == 3  # 宁可短一点也不让沪市占满

    picked_wide = screening.diversify(items, limit=4, per_board=4)
    assert len(picked_wide) == 4


def test_valuations_use_latest_row() -> None:
    original = screening.query_all

    class Row(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)

    rows = [
        Row(code="600000", trade_date="2026-10-23", pe_ttm=12.0, pb_mrq=1.1, total_mv=1e11),
        Row(code="600000", trade_date="2026-10-24", pe_ttm=13.0, pb_mrq=1.2, total_mv=1.1e11),
    ]
    screening.query_all = lambda sql, params=(): rows
    try:
        values = screening._valuations(["sh600000", "sz000001"])
    finally:
        screening.query_all = original
    assert values["600000"]["pe_ttm"] == 13.0
    assert values["600000"]["as_of"] == "2026-10-24"
    assert values["600000"]["total_mv_yi"] == 1100.0


def test_screen_candidates() -> None:
    original = screening.ak
    original_query = screening.query_all
    bars = {
        "sh600000": _bars(80, step=1.01),
        "sz000001": _bars(80, step=0.995),
        "sz300001": _bars(20, step=1.0),
    }

    class FakeAk:
        @staticmethod
        def stock_zh_a_spot():
            return Frame([
                _spot("sh600000", "上涨股份", 300.0, 10e8, 3.0),
                _spot("sz000001", "下跌股份", 50.0, 8e8, -2.0),
                _spot("sz300001", "次新股份", 20.0, 6e8, 1.0),
            ])

        @staticmethod
        def stock_zh_a_daily(symbol):
            if symbol == "sz000001":
                raise RuntimeError("日线接口超时")
            return Frame(bars[symbol])

    screening.ak = FakeAk
    screening.query_all = lambda sql, params=(): []
    screening._cache.update({"at": None, "key": None, "data": None})
    try:
        result = screening.screen_candidates(limit=5)
    finally:
        screening.ak = original
        screening.query_all = original_query

    names = [item["name"] for item in result["candidates"]]
    assert names == ["上涨股份"], names
    assert "sz000001" in result["errors"]["history"]
    # 次新只有 20 根日线，不进候选
    assert "次新股份" not in names
    assert result["filters"]["shortlisted"] == 3
    assert result["universe"]["scanned"] == 3
    assert result["missing"]


def test_screen_candidates_is_cached() -> None:
    original = screening.ak
    original_query = screening.query_all
    calls: list[int] = []

    class FakeAk:
        @staticmethod
        def stock_zh_a_spot():
            calls.append(1)
            return Frame([_spot("sh600000", "上涨股份", 300.0, 10e8)])

        @staticmethod
        def stock_zh_a_daily(symbol):
            return Frame(_bars(80, step=1.01))

    screening.ak = FakeAk
    screening.query_all = lambda sql, params=(): []
    screening._cache.update({"at": None, "key": None, "data": None})
    try:
        first = screening.screen_candidates(limit=3)
        second = screening.screen_candidates(limit=3)
        third = screening.screen_candidates(limit=3, force=True)
    finally:
        screening.ak = original
        screening.query_all = original_query
    assert first is second
    assert third is not first
    assert len(calls) == 2, calls


def test_screen_candidates_spot_failure() -> None:
    original = screening.ak

    class FakeAk:
        @staticmethod
        def stock_zh_a_spot():
            raise RuntimeError("新浪接口超时")

    screening.ak = FakeAk
    screening._cache.update({"at": None, "key": None, "data": None})
    try:
        result = screening.screen_candidates()
    finally:
        screening.ak = original
    assert result["candidates"] == []
    assert "新浪接口超时" in result["errors"]["spot"]


def main() -> None:
    test_board_of()
    test_exclusion_reason()
    test_shortlist_quotas_and_filters()
    test_stock_stats_metrics()
    test_stock_stats_without_history()
    test_score_stock_directions()
    test_risks_and_reasons()
    test_diversify_caps_per_board()
    test_valuations_use_latest_row()
    test_screen_candidates()
    test_screen_candidates_is_cached()
    test_screen_candidates_spot_failure()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
