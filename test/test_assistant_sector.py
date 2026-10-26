"""行业轮动工具验证：不需要公司、来源登记、失败是观察结果。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from assistant.agent import UNCACHED_TOOLS

ROTATION = {
    "as_of": "2026-10-26 15:05:00",
    "trade_date": "2026-10-26",
    "session": {"state": "收盘", "progress": 1.0},
    "benchmark": "沪深300",
    "benchmark_today": -0.04,
    "intraday": False,
    "total_sectors": 90,
    "breadth": {"up_industries": 33, "down_industries": 57, "up_ratio": 0.37,
                "median_chg_pct": -0.39},
    "leaders": [{"name": "软件开发", "score": 6.25, "trend": "持续强势"}],
    "emerging": [{"name": "半导体", "score": 2.1}],
    "weakening": [],
    "persistent": [{"name": "游戏", "score": 4.64}],
    "weak": [{"name": "贵金属", "score": -4.06}],
    "ranking": [{"name": "软件开发", "score": 6.25}, {"name": "贵金属", "score": -4.06}],
    "notes": [],
    "errors": {},
}


def test_sector_tool_does_not_need_company() -> None:
    calls: list[int] = []
    original = tools.build_sector_rotation

    def fake(limit=8):
        calls.append(limit)
        return ROTATION

    tools.build_sector_rotation = fake
    try:
        result = tools.run_tool(
            "get_sector_rotation", {"limit": 6}, default_company="605499"
        )
    finally:
        tools.build_sector_rotation = original

    assert calls == [6], calls
    assert result.ok is True, result
    assert result.company is None
    assert result.data == ROTATION
    assert [source.id for source in result.sources] == [
        "sector:ths:2026-10-26", "sector:benchmark:sh000300",
    ]
    assert result.sources[0].label == "行业板块"
    assert result.sources[1].detail == "沪深300"
    assert "get_sector_rotation" in UNCACHED_TOOLS


def test_sector_tool_clamps_limit() -> None:
    calls: list[int] = []
    original = tools.build_sector_rotation

    def fake(limit=8):
        calls.append(limit)
        return ROTATION

    tools.build_sector_rotation = fake
    try:
        tools.run_tool("get_sector_rotation", {"limit": 99})
        tools.run_tool("get_sector_rotation", {"limit": "bad"})
        tools.run_tool("get_sector_rotation", {})
    finally:
        tools.build_sector_rotation = original

    assert calls == [20, 8, 8], calls


def test_sector_tool_failure_is_observation() -> None:
    def broken(limit=8):
        raise RuntimeError("同花顺超时")

    original = tools.build_sector_rotation
    tools.build_sector_rotation = broken
    try:
        result = tools.run_tool("get_sector_rotation", {})
    finally:
        tools.build_sector_rotation = original

    assert result.ok is False
    assert "行业轮动获取失败" in result.error
    assert "同花顺超时" in result.error


def test_sector_tool_without_data() -> None:
    empty = {
        "as_of": None,
        "ranking": [],
        "leaders": [],
        "emerging": [],
        "weakening": [],
        "persistent": [],
        "weak": [],
        "breadth": None,
        "notes": [],
        "errors": {"sectors": "行业快照：连不上"},
    }
    original = tools.build_sector_rotation
    tools.build_sector_rotation = lambda limit=8: empty
    try:
        result = tools.run_tool("get_sector_rotation", {})
    finally:
        tools.build_sector_rotation = original

    assert result.ok is False
    assert "sectors：行业快照：连不上" in result.error


def test_sector_intent_and_schema() -> None:
    assert tools.is_market_question("哪些行业在走强")
    assert tools.is_market_question("行业轮动到哪了")
    schema = next(
        item for item in tools.schemas()
        if item["function"]["name"] == "get_sector_rotation"
    )
    properties = schema["function"]["parameters"]["properties"]
    assert properties["limit"]["minimum"] == 3
    assert properties["limit"]["maximum"] == 20


def main() -> None:
    test_sector_tool_does_not_need_company()
    test_sector_tool_clamps_limit()
    test_sector_tool_failure_is_observation()
    test_sector_tool_without_data()
    test_sector_intent_and_schema()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
