"""选股工具验证：不需要公司、参数收敛、来源登记与失败处理。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from assistant.agent import UNCACHED_TOOLS

SCREEN = {
    "as_of": "2026-10-26 15:10:00",
    "universe": {"scanned": 5576, "after_filters": 2735, "excluded": {}},
    "filters": {"min_amount_yi": 1.0, "shortlisted": 60},
    "candidates": [{
        "code": "sh600000", "name": "上涨股份", "board": "沪市主板",
        "price": 12.3, "chg_pct": 1.2, "market_cap_yi": 900.0,
        "ret_20d": 12.0, "ret_60d": 25.0, "pos_60d": 0.8,
        "score": 7.1, "reasons": ["站上20日与60日均线"], "risks": [],
        "valuation": None, "bars": 140,
    }],
    "errors": {},
    "notes": [],
    "missing": ["PE/PB 历史分位"],
}


def test_screen_tool_does_not_need_company() -> None:
    calls: list[tuple[int, float]] = []
    original = tools.build_screen

    def fake(limit=15, min_amount_yi=1.0):
        calls.append((limit, min_amount_yi))
        return SCREEN

    tools.build_screen = fake
    try:
        result = tools.run_tool(
            "screen_candidates", {"limit": 5, "min_amount_yi": 2.0},
            default_company="605499",
        )
    finally:
        tools.build_screen = original

    assert calls == [(5, 2.0)], calls
    assert result.ok is True, result
    assert result.company is None
    assert result.data == SCREEN
    assert [source.label for source in result.sources] == ["全市场快照", "个股日线"]
    assert result.sources[0].id == "screen:spot:2026-10-26"
    assert "screen_candidates" in UNCACHED_TOOLS


def test_screen_tool_clamps_arguments() -> None:
    calls: list[tuple[int, float]] = []
    original = tools.build_screen

    def fake(limit=15, min_amount_yi=1.0):
        calls.append((limit, min_amount_yi))
        return SCREEN

    tools.build_screen = fake
    try:
        tools.run_tool("screen_candidates", {"limit": 999, "min_amount_yi": 999})
        tools.run_tool("screen_candidates", {"limit": "bad", "min_amount_yi": "bad"})
        tools.run_tool("screen_candidates", {})
    finally:
        tools.build_screen = original

    assert calls == [(30, 50.0), (15, 1.0), (15, 1.0)], calls


def test_screen_tool_failure_is_observation() -> None:
    def broken(limit=15, min_amount_yi=1.0):
        raise RuntimeError("新浪超时")

    original = tools.build_screen
    tools.build_screen = broken
    try:
        result = tools.run_tool("screen_candidates", {})
    finally:
        tools.build_screen = original

    assert result.ok is False
    assert "量化筛选失败" in result.error
    assert "新浪超时" in result.error


def test_screen_tool_without_candidates() -> None:
    empty = dict(SCREEN, candidates=[], errors={"spot": "全市场快照：连不上"})
    original = tools.build_screen
    tools.build_screen = lambda limit=15, min_amount_yi=1.0: empty
    try:
        result = tools.run_tool("screen_candidates", {})
    finally:
        tools.build_screen = original

    assert result.ok is False
    assert "spot：全市场快照：连不上" in result.error


def test_screen_intent_and_schema() -> None:
    assert tools.is_market_question("帮我筛一下候选股")
    assert tools.is_market_question("有什么股票值得研究")
    schema = next(
        item for item in tools.schemas()
        if item["function"]["name"] == "screen_candidates"
    )
    properties = schema["function"]["parameters"]["properties"]
    assert properties["limit"]["minimum"] == 3
    assert properties["limit"]["maximum"] == 30
    assert properties["min_amount_yi"]["maximum"] == 50


def main() -> None:
    test_screen_tool_does_not_need_company()
    test_screen_tool_clamps_arguments()
    test_screen_tool_failure_is_observation()
    test_screen_tool_without_candidates()
    test_screen_intent_and_schema()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
