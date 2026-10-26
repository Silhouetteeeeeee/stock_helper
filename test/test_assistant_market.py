"""大盘工具与意图识别验证：无需公司、来源登记、失败是观察结果。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from assistant.agent import UNCACHED_TOOLS

OVERVIEW = {
    "as_of": "2026-10-26 13:35:00",
    "session": {"state": "盘中", "progress": 0.646},
    "indices": [{
        "code": "sh000001", "name": "上证指数", "price": 3687.7,
        "chg_pct": -0.07, "amount_yi": 5412.4,
    }],
    "turnover": {"total_yi": 11934.8, "pace_ratio": 1.02},
    "breadth": {"up": 2510.0, "down": 2565.0, "limit_up": 59.0},
    "trend": [{
        "code": "sh000001", "name": "上证指数", "date": "2026-10-23",
        "price": 3687.7, "ma20": 3803.88, "above_ma20": False,
    }],
    "sectors": {"top": [], "bottom": [], "up_count": 20, "down_count": 60},
    "limit_up": {"count": 56, "max_streak": 4, "streaks": [], "top_industries": []},
    "news": [{"time": "2026-10-26 13:10:10", "title": "沪指翻红"}],
    "errors": {},
}


def test_market_question_intent() -> None:
    assert tools.is_market_question("今天大盘怎么看 有什么操作建议")
    assert tools.is_market_question("两市成交额多少")
    assert tools.is_market_question("创业板走势如何")
    assert tools.is_market_question("现在该减仓吗")
    assert not tools.is_market_question("东鹏饮料毛利率为什么下滑")
    assert not tools.is_market_question("")
    assert not tools.is_market_question(None)


def test_market_tool_does_not_need_company() -> None:
    original = tools.build_market_overview
    tools.build_market_overview = lambda: OVERVIEW
    try:
        # 即使会话默认公司是东鹏，大盘工具也不做公司解析
        result = tools.run_tool(
            "get_market_overview", {}, default_company="605499"
        )
    finally:
        tools.build_market_overview = original

    assert result.ok is True
    assert result.company is None
    assert result.as_of == "2026-10-26 13:35:00"
    assert {key: value for key, value in result.data.items() if key != "state"} == OVERVIEW
    state = result.data["state"]
    assert state["state"] in ("强势", "震荡", "弱势", "极端风险")
    assert state["risk_level"] in ("低", "中", "高", "极高")
    assert state["basis"] and state["invalidation"]
    ids = [source.id for source in result.sources]
    assert ids == [
        "market:index:20261026133500",
        "market:breadth:20261026133500",
        "market:trend:2026-10-23",
        "market:sector:20261026133500",
        "market:limitup:2026-10-26",
        "market:news:20261026133500",
        "market:state:20261026133500",
    ], ids
    assert "get_market_overview" in UNCACHED_TOOLS


def test_market_tool_failure_is_observation() -> None:
    def broken():
        raise RuntimeError("接口超时")

    original = tools.build_market_overview
    tools.build_market_overview = broken
    try:
        result = tools.run_tool("get_market_overview", {})
    finally:
        tools.build_market_overview = original

    assert result.ok is False
    assert "大盘数据获取失败" in result.error
    assert "接口超时" in result.error


def test_market_tool_without_data() -> None:
    empty = {
        "as_of": None,
        "indices": [],
        "breadth": None,
        "trend": [],
        "sectors": None,
        "limit_up": None,
        "news": [],
        "errors": {"indices": "连不上", "breadth": "连不上"},
    }
    original = tools.build_market_overview
    tools.build_market_overview = lambda: empty
    try:
        result = tools.run_tool("get_market_overview", {})
    finally:
        tools.build_market_overview = original

    assert result.ok is False
    assert "indices：连不上" in result.error
    assert "breadth：连不上" in result.error


def main() -> None:
    test_market_question_intent()
    test_market_tool_does_not_need_company()
    test_market_tool_failure_is_observation()
    test_market_tool_without_data()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
