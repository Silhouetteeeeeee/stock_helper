"""策略工具验证：状态可传入、缺状态时现场取数、失败是观察结果。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from assistant.agent import UNCACHED_TOOLS

PLAN = {
    "market": {"state": "震荡", "risk_level": "中", "confidence": "高"},
    "profile": {"risk_tolerance": "稳健", "position_pct": 50.0,
                "cash_pct": 50.0, "headroom_pct": 10.0},
    "caps": {"total_pct": 60.0, "cash_floor_pct": 40.0, "single_pct": 12,
             "stop_loss_pct": 7.0, "base_pct": 60, "risk_multiplier": 1.0},
    "stance": "观望",
    "headroom_pct": 10.0,
    "actions": [],
    "holdings": [],
    "holding_warnings": [],
    "raise_conditions": [],
    "lower_conditions": [],
    "missing": [],
    "notes": [],
}


def test_strategy_tool_with_given_state() -> None:
    calls: list[tuple] = []
    original = tools.plan_strategy

    def fake(profile, market, holdings):
        calls.append((profile, market, holdings))
        return dict(PLAN)

    tools.plan_strategy = fake
    try:
        result = tools.run_tool("build_strategy_plan", {
            "market_state": "弱势",
            "risk_level": "高",
            "risk_tolerance": "保守",
            "position_pct": 30,
            "cash_pct": 70,
            "holdings": [{"code": "sh600519", "name": "贵州茅台", "weight_pct": 10}],
        })
    finally:
        tools.plan_strategy = original

    assert result.ok is True, result
    assert result.company is None
    assert result.sources == ()
    profile, market, holdings = calls[0]
    assert profile == {"risk_tolerance": "保守", "position_pct": 30, "cash_pct": 70}
    assert market == {"state": "弱势", "risk_level": "高"}
    assert holdings == [{"code": "sh600519", "name": "贵州茅台", "weight_pct": 10}]
    assert "build_strategy_plan" in UNCACHED_TOOLS


def test_strategy_tool_fetches_market_when_state_missing() -> None:
    overview = {
        "as_of": "2026-10-26 14:10:00",
        "indices": [{"code": "sh000001", "name": "上证指数", "price": 3300}],
        "breadth": {"up": 2000, "down": 3000},
    }
    state = {
        "state": "弱势", "risk_level": "高", "confidence": "高",
        "basis": [{"factor": "趋势", "reading": "四大指数均在20日均线下方"}],
        "invalidation": ["沪深300 重新站上 20 日均线 4100 点"],
    }

    original_overview = tools.build_market_overview
    original_classify = tools.classify_market
    original_plan = tools.plan_strategy
    seen: list[dict] = []

    def fake_plan(profile, market, holdings):
        seen.append(market)
        return dict(PLAN)

    tools.build_market_overview = lambda: overview
    tools.classify_market = lambda data: state
    tools.plan_strategy = fake_plan
    try:
        result = tools.run_tool("build_strategy_plan", {"risk_tolerance": "稳健"})
    finally:
        tools.build_market_overview = original_overview
        tools.classify_market = original_classify
        tools.plan_strategy = original_plan

    assert result.ok is True, result
    assert seen[0]["state"] == "弱势"
    assert result.as_of == "2026-10-26 14:10:00"
    assert any(source.id.startswith("market:index:") for source in result.sources)
    assert any(source.label == "市场状态" for source in result.sources)


def test_strategy_tool_market_failure_is_observation() -> None:
    original_overview = tools.build_market_overview
    original_plan = tools.plan_strategy

    def broken():
        raise RuntimeError("大盘接口超时")

    tools.build_market_overview = broken
    tools.plan_strategy = lambda profile, market, holdings: {
        **PLAN, "missing": list(PLAN["missing"]),
    }
    try:
        result = tools.run_tool("build_strategy_plan", {})
    finally:
        tools.build_market_overview = original_overview
        tools.plan_strategy = original_plan

    assert result.ok is True
    assert any("大盘状态获取失败" in item for item in result.data["missing"])


def test_strategy_tool_plan_failure() -> None:
    original = tools.plan_strategy

    def broken(profile, market, holdings):
        raise RuntimeError("规则计算异常")

    tools.plan_strategy = broken
    try:
        result = tools.run_tool(
            "build_strategy_plan", {"market_state": "震荡", "risk_level": "中"}
        )
    finally:
        tools.plan_strategy = original

    assert result.ok is False
    assert "策略方案生成失败" in result.error
    assert "规则计算异常" in result.error


def test_strategy_schema() -> None:
    schema = next(
        item for item in tools.schemas()
        if item["function"]["name"] == "build_strategy_plan"
    )
    properties = schema["function"]["parameters"]["properties"]
    assert properties["market_state"]["enum"] == ["强势", "震荡", "弱势", "极端风险"]
    assert properties["risk_tolerance"]["enum"] == ["保守", "稳健", "积极"]
    assert properties["holdings"]["type"] == "array"
    assert tools.is_market_question("现在几成仓位合适")


def main() -> None:
    test_strategy_tool_with_given_state()
    test_strategy_tool_fetches_market_when_state_missing()
    test_strategy_tool_market_failure_is_observation()
    test_strategy_tool_plan_failure()
    test_strategy_schema()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
