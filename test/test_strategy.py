"""模块 5 验证：仓位上限、姿态判断、止损与持仓检查。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.strategy as strategy


def test_position_cap_by_state_and_tolerance() -> None:
    strong = strategy.position_cap("强势", "低", "稳健")
    assert strong["total_pct"] == 70
    assert strong["cash_floor_pct"] == 30

    weak = strategy.position_cap("弱势", "低", "保守")
    assert weak["total_pct"] == 20

    extreme = strategy.position_cap("极端风险", "极高", "积极")
    assert extreme["total_pct"] == 14.0  # 20 × 0.7

    high = strategy.position_cap("震荡", "高", "稳健")
    assert high["total_pct"] == 51.0  # 60 × 0.85

    # 未知状态按震荡处理，不让它变成极端仓位
    unknown = strategy.position_cap("看不懂", None, "稳健")
    assert unknown["total_pct"] == 60


def test_single_cap_and_stop() -> None:
    assert strategy.single_cap("强势", "稳健") == 15
    assert strategy.single_cap("极端风险", "积极") == 8
    assert strategy.stop_loss_pct("强势", "稳健") == 8.0
    assert strategy.stop_loss_pct("弱势", "稳健") == 6.0
    # 未知风险承受能力退回稳健
    assert strategy.single_cap("震荡", "赌徒") == 12


def test_stance_transitions() -> None:
    assert strategy._stance("震荡", 80, 60) == strategy.STANCE_REDUCE
    assert strategy._stance("震荡", 57, 60) == strategy.STANCE_HOLD
    assert strategy._stance("弱势", 10, 35) == strategy.STANCE_WATCH
    assert strategy._stance("极端风险", 0, 10) == strategy.STANCE_WATCH
    assert strategy._stance("强势", 40, 70) == strategy.STANCE_BUY
    assert strategy._stance("震荡", 40, 60) == strategy.STANCE_WATCH
    assert strategy._stance("强势", None, 70) == strategy.STANCE_WATCH


def test_plan_reduce_when_over_cap() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "稳健", "position_pct": 80, "cash_pct": 20},
        {"state": "震荡", "risk_level": "中", "confidence": "高"},
    )
    assert plan["caps"]["total_pct"] == 60
    assert plan["stance"] == strategy.STANCE_REDUCE
    assert plan["headroom_pct"] == -20.0
    action = next(item for item in plan["actions"] if item["action"] == "减仓")
    assert "20个百分点" in action["detail"]
    assert plan["caps"]["single_pct"] == 12
    assert plan["caps"]["stop_loss_pct"] == 7.0


def test_plan_buy_in_strong_market() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "积极", "position_pct": 30, "cash_pct": 70},
        {"state": "强势", "risk_level": "低", "confidence": "高"},
    )
    assert plan["caps"]["total_pct"] == 90
    assert plan["stance"] == strategy.STANCE_BUY
    action = next(item for item in plan["actions"] if item["action"] == "分批建仓")
    assert "第一批" in action["detail"]
    assert plan["raise_conditions"] and plan["lower_conditions"]


def test_plan_weak_market_keeps_cash() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "稳健", "position_pct": 10, "cash_pct": 90},
        {"state": "弱势", "risk_level": "高", "confidence": "中"},
    )
    assert plan["stance"] == strategy.STANCE_WATCH
    action = next(item for item in plan["actions"] if item["action"] == "观望")
    assert "不新增仓位" in action["detail"]


def test_plan_without_profile_inputs() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "稳健"},
        {"state": "震荡", "risk_level": "中"},
    )
    assert plan["stance"] == strategy.STANCE_WATCH
    assert plan["headroom_pct"] is None
    joined = " ".join(plan["missing"])
    assert "当前总仓位" in joined
    assert "现金比例" in joined
    assert "持仓明细" in joined


def test_plan_missing_state_falls_back_to_range() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "稳健", "position_pct": 50, "cash_pct": 50},
        {},
    )
    assert plan["market"]["state"] == "震荡"
    assert any("市场状态" in item for item in plan["missing"])


def test_holdings_flags_overweight_and_stop() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "稳健", "position_pct": 50, "cash_pct": 50},
        {"state": "震荡", "risk_level": "中"},
        holdings=[
            {"code": "sh600519", "name": "贵州茅台", "weight_pct": 20,
             "cost": 1500, "price": 1350},
            {"code": "sz000001", "name": "平安银行", "weight_pct": 5,
             "cost": 10, "price": 10.5},
        ],
    )
    rows = {row["name"]: row for row in plan["holdings"]}
    assert rows["贵州茅台"]["status"] == "触发止损"
    assert rows["贵州茅台"]["stop_price"] == 1395.0  # 1500 × (1 - 7%)
    assert rows["贵州茅台"]["pnl_pct"] == -10.0
    assert any("超过单只上限" in note for note in rows["贵州茅台"]["notes"])
    assert "贵州茅台" in " ".join(plan["holding_warnings"])
    assert rows["平安银行"]["status"] == "正常"


def test_low_confidence_note() -> None:
    plan = strategy.build_plan(
        {"risk_tolerance": "稳健", "position_pct": 50, "cash_pct": 50},
        {"state": "强势", "risk_level": "低", "confidence": "低"},
    )
    assert any("置信度" in item for item in plan["notes"])


def main() -> None:
    test_position_cap_by_state_and_tolerance()
    test_single_cap_and_stop()
    test_stance_transitions()
    test_plan_reduce_when_over_cap()
    test_plan_buy_in_strong_market()
    test_plan_weak_market_keeps_cash()
    test_plan_without_profile_inputs()
    test_plan_missing_state_falls_back_to_range()
    test_holdings_flags_overweight_and_stop()
    test_low_confidence_note()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
