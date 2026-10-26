"""模块 5：策略与风险管理。

把市场状态、风险等级和用户的风险承受能力、当前仓位、持仓结构放在一起，
给出有条件的操作预案，而不是一句“该买还是该卖”。

规则都是确定性的：

* 总仓位上限由市场状态和风险等级决定，风险承受能力只在这个上限内调整
* 单只股票上限防止一只票决定整体结果
* 止损比例按承受能力分档，弱势与极端风险市场收紧
* 每个动作都带触发条件，条件来自市场状态的判断依据与失效条件

这里不做收益预测，也不给具体买卖点。输出的是“什么情况下做什么”，
以及“什么情况下原来的判断不再成立”。
"""

from __future__ import annotations

from .market_state import EXTREME, RANGE, STRONG, WEAK

TOLERANCES = ("保守", "稳健", "积极")
DEFAULT_TOLERANCE = "稳健"

# 总仓位上限（%），按市场状态 × 风险承受能力
STATE_CAPS: dict[str, dict[str, float]] = {
    STRONG: {"保守": 50, "稳健": 70, "积极": 90},
    RANGE: {"保守": 40, "稳健": 60, "积极": 75},
    WEAK: {"保守": 20, "稳健": 35, "积极": 50},
    EXTREME: {"保守": 5, "稳健": 10, "积极": 20},
}

# 单只股票上限（%）
SINGLE_CAPS: dict[str, dict[str, float]] = {
    STRONG: {"保守": 10, "稳健": 15, "积极": 20},
    RANGE: {"保守": 8, "稳健": 12, "积极": 15},
    WEAK: {"保守": 5, "稳健": 8, "积极": 10},
    EXTREME: {"保守": 3, "稳健": 5, "积极": 8},
}

# 止损比例（%），弱势与极端风险市场收紧
STOP_PCT: dict[str, dict[str, float]] = {
    STRONG: {"保守": 5.0, "稳健": 8.0, "积极": 10.0},
    RANGE: {"保守": 5.0, "稳健": 7.0, "积极": 9.0},
    WEAK: {"保守": 4.0, "稳健": 6.0, "积极": 8.0},
    EXTREME: {"保守": 3.0, "稳健": 5.0, "积极": 6.0},
}

# 风险等级对仓位上限的折价
RISK_MULTIPLIER = {"低": 1.0, "中": 1.0, "高": 0.85, "极高": 0.7}

STANCE_REDUCE = "降低风险"
STANCE_HOLD = "持有不加仓"
STANCE_WATCH = "观望"
STANCE_BUY = "研究买入（分批）"

# 动作阈值（百分点）
REDUCE_OVER = 5.0      # 超过上限这么多就必须减
AT_CAP_BAND = 5.0      # 距离上限这么近就不再加
BUY_HEADROOM = 15.0    # 至少低于上限这么多才考虑分批买入


def _tolerance(value) -> str:
    return value if value in TOLERANCES else DEFAULT_TOLERANCE


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number))


def position_cap(state: str, risk_level: str | None, tolerance: str) -> dict:
    """总仓位上限与现金下限，带上限来源，方便解释。"""
    state = state if state in STATE_CAPS else RANGE
    tolerance = _tolerance(tolerance)
    base = STATE_CAPS[state][tolerance]
    multiplier = RISK_MULTIPLIER.get(str(risk_level or ""), 1.0)
    cap = round(base * multiplier, 1)
    return {
        "total_pct": cap,
        "base_pct": base,
        "base_state": state,
        "base_tolerance": tolerance,
        "risk_multiplier": multiplier,
        "cash_floor_pct": round(100 - cap, 1),
    }


def single_cap(state: str, tolerance: str) -> float:
    state = state if state in SINGLE_CAPS else RANGE
    return SINGLE_CAPS[state][_tolerance(tolerance)]


def stop_loss_pct(state: str, tolerance: str) -> float:
    state = state if state in STOP_PCT else RANGE
    return STOP_PCT[state][_tolerance(tolerance)]


def _stance(
    state: str, position: float | None, cap: float
) -> str:
    if position is None:
        return STANCE_WATCH
    if position > cap + REDUCE_OVER:
        return STANCE_REDUCE
    if position >= cap - AT_CAP_BAND:
        return STANCE_HOLD
    if state in (WEAK, EXTREME):
        return STANCE_WATCH
    if state == STRONG and position <= cap - BUY_HEADROOM:
        return STANCE_BUY
    return STANCE_WATCH


def _holdings_plan(
    holdings: list[dict] | None,
    single: float,
    stop_pct: float,
) -> tuple[list[dict], list[str]]:
    """逐只检查超配、浮亏与止损，返回明细与汇总提醒。"""
    rows: list[dict] = []
    warnings: list[str] = []
    for item in holdings or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or code or "未命名持仓")
        weight = _number(item.get("weight_pct"))
        cost = _number_value(item.get("cost"))
        price = _number_value(item.get("price"))

        notes: list[str] = []
        status = "正常"
        if weight is not None and weight > single:
            status = "超配"
            notes.append(f"占比{weight:.1f}%超过单只上限{single:.0f}%，考虑降到上限以内")
        if cost and price:
            pnl = (price / cost - 1) * 100
            notes.append(f"相对成本{pnl:+.1f}%")
            if pnl <= -stop_pct:
                status = "触发止损"
                notes.append(f"亏损超过止损线{stop_pct:.0f}%，按纪律应先处理这只")
            stop_price = cost * (1 - stop_pct / 100)
        else:
            pnl = None
            stop_price = None
        if weight is not None and weight <= 0:
            status = "空仓"

        rows.append({
            "code": code or None,
            "name": name,
            "weight_pct": weight,
            "pnl_pct": round(pnl, 2) if pnl is not None else None,
            "stop_price": round(stop_price, 2) if stop_price else None,
            "status": status,
            "notes": notes,
        })
        if status in ("超配", "触发止损"):
            warnings.append(f"{name}：{status}")

    if rows:
        total = sum(row["weight_pct"] or 0 for row in rows)
        if total > 100:
            warnings.append(f"持仓占比合计{total:.1f}%，数据可能含杠杆或统计口径不一致")
    return rows, warnings


def _number_value(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _conditions(market: dict) -> dict:
    basis = market.get("basis") or []
    invalidation = market.get("invalidation") or []
    trend = next(
        (item for item in basis if str(item.get("factor") or "").startswith("趋势")),
        None,
    )
    breadth = next(
        (item for item in basis if str(item.get("factor") or "").startswith("宽度")),
        None,
    )
    return {
        "trend_reading": (trend or {}).get("reading"),
        "breadth_reading": (breadth or {}).get("reading"),
        "invalidation": [str(text) for text in invalidation][:4],
    }


def build_plan(
    profile: dict | None = None,
    market: dict | None = None,
    holdings: list[dict] | None = None,
) -> dict:
    """给出有条件的操作预案；缺输入时明确写缺失，不替用户假设。"""
    profile = profile or {}
    market = market or {}
    missing: list[str] = []
    notes: list[str] = []

    state = str(market.get("state") or "").strip()
    if state not in STATE_CAPS:
        missing.append("市场状态：需要先取大盘状态分类，暂按震荡处理")
        state = RANGE
    risk_level = market.get("risk_level")
    confidence = market.get("confidence")
    tolerance = _tolerance(profile.get("risk_tolerance"))
    if profile.get("risk_tolerance") not in TOLERANCES:
        missing.append("风险承受能力：未提供，按稳健处理")

    position = _number(profile.get("position_pct"))
    cash = _number(profile.get("cash_pct"))
    if position is None:
        missing.append("当前总仓位：未提供，只给仓位区间不给具体动作")
    if cash is None:
        missing.append("现金比例：未提供，无法判断可用资金")

    cap = position_cap(state, risk_level, tolerance)
    single = single_cap(state, tolerance)
    stop_pct = stop_loss_pct(state, tolerance)
    stance = _stance(state, position, cap["total_pct"])

    headroom = None
    if position is not None:
        headroom = round(cap["total_pct"] - position, 1)

    actions: list[dict] = []
    conditions = _conditions(market)

    if stance == STANCE_REDUCE:
        actions.append({
            "action": "减仓",
            "condition": f"当前仓位{position:.0f}%高于上限{cap['total_pct']:.0f}%",
            "detail": (
                f"把总仓位降到{cap['total_pct']:.0f}%以内，约减持"
                f"{position - cap['total_pct']:.0f}个百分点；"
                "先减跌破止损线、逻辑已失效或超配的持仓"
            ),
        })
    elif stance == STANCE_HOLD:
        actions.append({
            "action": "持有",
            "condition": f"仓位已接近上限{cap['total_pct']:.0f}%",
            "detail": "不加仓也不主动减；跌破止损线或指数跌破20日均线时再降一档",
        })
    elif stance == STANCE_BUY:
        first = round(min((cap["total_pct"] - position) / 2, 10), 1)
        actions.append({
            "action": "分批建仓",
            "condition": f"市场{state}且仓位低于上限{cap['total_pct']:.0f}%",
            "detail": (
                f"第一批最多加{first:.0f}个百分点，优先选已确认趋势、"
                "估值不处于历史高位的标的；第二批要等指数站稳均线、"
                "上涨家数占比连续回升后再考虑"
            ),
        })
    else:
        detail = (
            "不新增仓位，等触发条件出现：指数重新站上20日均线、"
            "上涨家数占比回升到50%以上、行业轮动出现明确的持续强势方向"
        )
        if state in (WEAK, EXTREME):
            detail = (
                f"市场{state}，不新增仓位；已有持仓按止损纪律处理，"
                "现金保持在上限对应的下限之上"
            )
        actions.append({
            "action": "观望",
            "condition": f"市场{state}，风险等级{risk_level or '未知'}",
            "detail": detail,
        })

    actions.append({
        "action": "止损",
        "condition": f"单只亏损超过{stop_pct:.0f}%",
        "detail": (
            f"按成本价下浮{stop_pct:.0f}%设止损；市场{state}时不因为"
            "“再等等”把止损挪低，先减到能睡得着的仓位"
        ),
    })

    if cash is not None and cash < cap["cash_floor_pct"]:
        actions.append({
            "action": "补充现金",
            "condition": f"现金{cash:.0f}%低于下限{cap['cash_floor_pct']:.0f}%",
            "detail": "减仓补回现金，保证极端行情下还有调整空间",
        })

    holding_rows, warnings = _holdings_plan(holdings, single, stop_pct)
    if holdings is None:
        missing.append("持仓明细：未提供，无法检查超配与止损状态")

    if confidence == "低":
        notes.append(f"市场状态置信度为低，仓位上限{cap['total_pct']:.0f}%只作为区间参考")

    return {
        "market": {
            "state": state,
            "risk_level": risk_level,
            "confidence": confidence,
            "trend": conditions["trend_reading"],
            "breadth": conditions["breadth_reading"],
        },
        "profile": {
            "risk_tolerance": tolerance,
            "position_pct": position,
            "cash_pct": cash,
            "headroom_pct": headroom,
        },
        "caps": {
            "total_pct": cap["total_pct"],
            "cash_floor_pct": cap["cash_floor_pct"],
            "single_pct": single,
            "stop_loss_pct": stop_pct,
            "base_pct": cap["base_pct"],
            "risk_multiplier": cap["risk_multiplier"],
        },
        "stance": stance,
        "headroom_pct": headroom,
        "actions": actions,
        "holdings": holding_rows,
        "holding_warnings": warnings,
        "raise_conditions": [
            "指数重新站上20日均线且上涨家数占比连续三个交易日高于50%",
            "行业轮动出现持续强势方向，且成交额同步放大",
            "市场状态由当前档位上调，并且置信度不为低",
        ],
        "lower_conditions": conditions["invalidation"] or [
            "指数跌破20日均线且上涨家数占比低于35%",
            "放量下跌或跌停家数明显增加",
        ],
        "missing": missing,
        "notes": notes,
    }
