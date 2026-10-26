"""大盘数据层验证：交易时段、均线、成交折算、宽度、板块与缺失隔离。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock.market import build_overview_from, session_state, trend_stats


def _bars(close_start: float = 3500.0, volume: float = 50e9, days: int = 70):
    return [
        {
            "date": f"2026-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
            "close": close_start + index,
            "volume": volume,
        }
        for index in range(days)
    ]


def test_session_state() -> None:
    today = "2026-10-26"
    assert session_state("2026-10-26 09:00:00", today) == {
        "state": "盘前", "progress": 0.0,
    }
    assert session_state("2026-10-26 10:30:00", today) == {
        "state": "盘中", "progress": 0.25,
    }
    assert session_state("2026-10-26 11:45:00", today) == {
        "state": "午间休市", "progress": 0.5,
    }
    assert session_state("2026-10-26 13:35:00", today) == {
        "state": "盘中", "progress": 0.646,
    }
    assert session_state("2026-10-26 15:00:00", today) == {
        "state": "盘中", "progress": 1.0,
    }
    assert session_state("2026-10-26 15:01:00", today) == {
        "state": "收盘", "progress": 1.0,
    }
    # 周末拿到的是上一交易日的数据：按收盘看，不把旧数据说成盘中
    assert session_state("2026-10-23 15:00:00", today) == {
        "state": "最近交易日", "progress": 1.0,
    }
    assert session_state("", today)["state"] == "时点未知"
    assert session_state("不是时间", today)["progress"] is None


def test_trend_stats() -> None:
    stats = trend_stats(_bars(), price=3687.7)
    assert stats is not None
    assert stats["close"] == 3569.0
    assert stats["price"] == 3687.7
    assert stats["ma5"] == 3567.0
    assert stats["ma20"] == 3559.5
    assert stats["ma60"] is not None
    assert stats["above_ma20"] is True
    assert stats["above_ma60"] is True
    assert stats["chg_5d_pct"] == 3.47, stats["chg_5d_pct"]
    assert stats["chg_20d_pct"] == 3.91, stats["chg_20d_pct"]
    assert stats["high_20d"] == 3569.0
    assert stats["low_20d"] == 3550.0
    # 没有最新价时用最后一根日线收盘
    assert trend_stats(_bars())["price"] == 3569.0
    assert trend_stats([]) is None


def test_build_overview_from() -> None:
    index_rows = [
        {
            "代码": "sh000001", "名称": "上证指数", "最新价": 3687.7,
            "涨跌额": -2.8, "涨跌幅": -0.07, "今开": 3671.0, "最高": 3699.0,
            "最低": 3664.8, "昨收": 3690.5, "成交量": 313146708,
            "成交额": 541235377390,
        },
        {
            "代码": "sz399001", "名称": "深证成指", "最新价": 11567.5,
            "涨跌额": -106.3, "涨跌幅": -0.91, "今开": 11690.3, "最高": 11707.1,
            "最低": 11350.9, "昨收": 11673.8, "成交量": 41520478403,
            "成交额": 652240662597,
        },
    ]
    breadth_rows = [
        {"item": "上涨", "value": 2510.0},
        {"item": "下跌", "value": 2565.0},
        {"item": "平盘", "value": 142.0},
        {"item": "涨停", "value": 59.0},
        {"item": "跌停", "value": 13.0},
        {"item": "真实涨停", "value": 52.0},
        {"item": "真实跌停", "value": 11.0},
        {"item": "停牌", "value": 7.0},
        {"item": "活跃度", "value": "48.05%"},
    ]
    sector_rows = [
        {
            "板块": f"板块{index}", "涨跌幅": 8 - index, "总成交额": 100.0 - index,
            "净流入": 10.0 - index, "上涨家数": 20, "下跌家数": 3,
            "领涨股": f"龙头{index}", "领涨股-涨跌幅": 7.5,
        }
        for index in range(1, 15)
    ]
    trend_rows = {
        "sh000001": _bars(3500.0, 50e9),
        "sz399001": _bars(11000.0, 60e9),
    }
    limit_rows = [
        {"连板数": 4, "所属行业": "通信设备"},
        {"连板数": 1, "所属行业": "一般零售"},
        {"连板数": 2, "所属行业": "通信设备"},
    ]
    news_rows = [
        {"标题": "央行开展逆回购", "发布日期": "2026-10-26", "发布时间": "09:00:00"},
        {"标题": "", "发布日期": "2026-10-26", "发布时间": "09:01:00"},
    ]

    overview = build_overview_from(
        index_rows=index_rows,
        breadth_rows=breadth_rows,
        sector_rows=sector_rows,
        trend_rows=trend_rows,
        limit_rows=limit_rows,
        news_rows=news_rows,
        as_of="2026-10-26 13:35:00",
        today="2026-10-26",
        errors={"news_source": "测试"},
    )

    assert overview["session"]["state"] == "盘中"
    assert len(overview["indices"]) == 2
    assert overview["indices"][0]["name"] == "上证指数"
    assert overview["indices"][0]["amount_yi"] == 5412.4

    turnover = overview["turnover"]
    assert turnover["sh_yi"] == 5412.4
    assert turnover["sz_yi"] == 6522.4
    assert turnover["total_yi"] == 11934.8
    # 沪市快照成交量单位是手，要乘 100 才能和深市股数相加
    assert turnover["today_shares_yi"] == 728.4
    assert turnover["avg20_shares_yi"] == 1100.0
    assert turnover["pace_ratio"] == 1.02, turnover["pace_ratio"]

    breadth = overview["breadth"]
    assert breadth["up"] == 2510.0 and breadth["down"] == 2565.0
    assert breadth["limit_up"] == 59.0 and breadth["limit_down"] == 13.0
    assert breadth["activity_pct"] == 48.05
    assert breadth["up_ratio_pct"] == 48.1

    assert [item["name"] for item in overview["sectors"]["top"]][:3] == [
        "板块1", "板块2", "板块3",
    ]
    assert overview["sectors"]["bottom"][0]["name"] == "板块14"
    assert overview["sectors"]["up_count"] == 7
    assert overview["sectors"]["down_count"] == 6

    limit_up = overview["limit_up"]
    assert limit_up["count"] == 3
    assert limit_up["max_streak"] == 4
    assert limit_up["streaks"] == [
        {"boards": 4, "count": 1},
        {"boards": 2, "count": 1},
        {"boards": 1, "count": 1},
    ]
    assert limit_up["top_industries"] == [
        {"name": "通信设备", "count": 2},
        {"name": "一般零售", "count": 1},
    ]

    assert overview["news"] == [
        {"time": "2026-10-26 09:00:00", "title": "央行开展逆回购"},
    ]
    assert overview["errors"] == {"news_source": "测试"}

    trend = {item["code"]: item for item in overview["trend"]}
    assert set(trend) == {"sh000001", "sz399001"}
    assert trend["sh000001"]["above_ma20"] is True
    assert trend["sh000001"]["date"].startswith("2026-")


def test_build_overview_from_missing_sections() -> None:
    """所有数据源都失败时也要返回结构完整的空盘面，不能抛异常。"""
    overview = build_overview_from(
        as_of="2026-10-26 08:00:00",
        today="2026-10-26",
        errors={"indices": "连不上"},
    )
    assert overview["session"]["state"] == "盘前"
    assert overview["indices"] == []
    assert overview["turnover"] is None
    assert overview["breadth"] is None
    assert overview["sectors"] is None
    assert overview["trend"] == []
    assert overview["limit_up"] is None
    assert overview["news"] == []
    assert overview["errors"] == {"indices": "连不上"}


def main() -> None:
    test_session_state()
    test_trend_stats()
    test_build_overview_from()
    test_build_overview_from_missing_sections()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
