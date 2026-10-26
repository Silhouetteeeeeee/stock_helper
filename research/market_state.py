"""模块 1：大盘状态识别。

输入是 ``stock.market.build_market_overview()`` 的结构，输出是可复核的
状态分类，而不是让模型凭感觉写“偏强/偏弱”：

* ``state``        强势 / 震荡 / 弱势 / 极端风险
* ``basis``        每条判断依据的读数、方向与打分
* ``risk_level``   低 / 中 / 高 / 极高
* ``invalidation`` 什么条件出现后，这个判断不再成立
* ``missing``      哪些数据缺失，判断置信度因此下降

规则是显式的、可测试的。指数只占一部分权重，宽度、量能、波动与结构
各自独立打分；出现极端信号时直接覆盖普通分类。
"""

from __future__ import annotations

import math
import statistics

STRONG = "强势"
RANGE = "震荡"
WEAK = "弱势"
EXTREME = "极端风险"

TREND_CODES = ("sh000001", "sz399001", "sz399006", "sh000300")
BENCHMARK_CODE = "sh000300"


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _round(value, digits: int = 2):
    return None if value is None else round(value, digits)


def _trend_items(overview: dict) -> list[dict]:
    wanted = set(TREND_CODES)
    return [
        item for item in (overview.get("trend") or [])
        if str(item.get("code") or "") in wanted
    ]


def _benchmark(items: list[dict]) -> dict | None:
    for item in items:
        if str(item.get("code") or "") == BENCHMARK_CODE:
            return item
    for item in items:
        if str(item.get("code") or "") == "sh000001":
            return item
    return items[0] if items else None


def _index_change(overview: dict, benchmark: dict | None) -> float | None:
    """当日涨跌幅在指数快照里，不在日线统计里。"""
    code = str((benchmark or {}).get("code") or BENCHMARK_CODE)
    snapshots = {
        str(item.get("code") or ""): item
        for item in (overview.get("indices") or [])
    }
    for wanted in (code, BENCHMARK_CODE, "sh000001"):
        value = _number((snapshots.get(wanted) or {}).get("chg_pct"))
        if value is not None:
            return value
    return None


def _trend_signal(items: list[dict]) -> dict:
    if not items:
        return {
            "factor": "趋势", "reading": "指数日线缺失", "signal": "unknown",
            "score": None, "above_ma20": None, "above_ma60": None,
        }

    scores: list[float] = []
    above20 = 0
    above60 = 0
    valid20 = 0
    valid60 = 0
    for item in items:
        score = 0.0
        if item.get("above_ma20") is True:
            score += 1
            above20 += 1
            valid20 += 1
        elif item.get("above_ma20") is False:
            score -= 1
            valid20 += 1
        if item.get("above_ma60") is True:
            score += 1
            above60 += 1
            valid60 += 1
        elif item.get("above_ma60") is False:
            score -= 1
            valid60 += 1
        scores.append(score)

    score = sum(scores) / len(scores)
    reading = (
        f"{above20}/{valid20} 个宽基在 MA20 上方，"
        f"{above60}/{valid60} 个在 MA60 上方"
    )
    signal = "positive" if score > 0.25 else "negative" if score < -0.25 else "neutral"
    return {
        "factor": "趋势", "reading": reading, "signal": signal,
        "score": _round(score), "above_ma20": above20, "above_ma60": above60,
    }


def _breadth_signal(breadth: dict | None) -> dict:
    if not breadth:
        return {
            "factor": "市场宽度", "reading": "涨跌家数缺失",
            "signal": "unknown", "score": None,
        }
    up = _number(breadth.get("up"))
    down = _number(breadth.get("down"))
    flat = _number(breadth.get("flat")) or 0
    ratio = _number(breadth.get("up_ratio_pct"))
    if ratio is None and up is not None and down is not None:
        total = up + down + flat
        ratio = up / total * 100 if total else None

    if ratio is None:
        score = None
        signal = "unknown"
        reading = "上涨家数占比无法计算"
    else:
        if ratio >= 60:
            score = 2.0
        elif ratio >= 52:
            score = 1.0
        elif ratio >= 45:
            score = 0.0
        elif ratio >= 35:
            score = -1.0
        else:
            score = -2.0
        signal = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        reading = f"上涨 {_round(up, 0)} 家、下跌 {_round(down, 0)} 家，上涨占比 {_round(ratio, 1)}%"

    limit_up = _number(breadth.get("limit_up"))
    limit_down = _number(breadth.get("limit_down"))
    if score is not None and limit_up is not None and limit_down is not None:
        if limit_up >= max(limit_down, 1) * 3 and limit_up >= 30:
            score += 0.5
        elif limit_down >= max(limit_up, 1) * 2 and limit_down >= 10:
            score -= 0.5
        reading += f"；涨停 {_round(limit_up, 0)} 家、跌停 {_round(limit_down, 0)} 家"
    return {
        "factor": "市场宽度", "reading": reading, "signal": signal,
        "score": _round(score), "up_ratio_pct": _round(ratio, 1),
        "limit_up": _round(limit_up, 0), "limit_down": _round(limit_down, 0),
    }


def _volume_signal(turnover: dict | None, index_chg: float | None) -> dict:
    if not turnover:
        return {
            "factor": "量能", "reading": "成交额缺失",
            "signal": "unknown", "score": None,
        }
    pace = _number(turnover.get("pace_ratio"))
    total_yi = _number(turnover.get("total_yi"))
    if pace is None:
        return {
            "factor": "量能",
            "reading": f"两市成交额 {_round(total_yi, 1)} 亿元，缺少近 20 日均量对比",
            "signal": "unknown", "score": None, "total_yi": _round(total_yi, 1),
        }

    if pace >= 1.15:
        score = 1.0 if index_chg is not None and index_chg >= 0 else -1.0
    elif pace <= 0.8:
        score = -0.5
    else:
        score = 0.0
    signal = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    reading = (
        f"两市成交额 {_round(total_yi, 1)} 亿元，按当前节奏折算为近 20 日均量的 "
        f"{_round(pace, 2)} 倍"
    )
    if pace >= 1.15 and index_chg is not None:
        reading += "；放量" + ("上涨" if index_chg >= 0 else "下跌")
    return {
        "factor": "量能", "reading": reading, "signal": signal,
        "score": _round(score), "total_yi": _round(total_yi, 1),
        "pace_ratio": _round(pace, 2),
    }


def _volatility_signal(items: list[dict], trend_score: float | None) -> dict:
    values = [
        _number(item.get("volatility_20d_pct"))
        for item in items
        if _number(item.get("volatility_20d_pct")) is not None
    ]
    if not values:
        return {
            "factor": "波动", "reading": "20 日波动率缺失",
            "signal": "unknown", "score": None,
        }
    volatility = statistics.median(values)
    if volatility >= 30:
        score = -1.0
    elif volatility >= 22:
        score = -0.5
    elif volatility <= 12:
        score = 0.5
    else:
        score = 0.0
    if volatility >= 25 and trend_score is not None and trend_score < 0:
        score -= 0.5
    signal = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return {
        "factor": "波动",
        "reading": f"主要宽基 20 日年化波动率中位数 {_round(volatility, 1)}%",
        "signal": signal, "score": _round(score),
        "volatility_20d_pct": _round(volatility, 1),
    }


def _structure_signal(sectors: dict | None, limit_up: dict | None, breadth: dict | None) -> dict:
    score = 0.0
    parts: list[str] = []
    if sectors and sectors.get("total"):
        total = _number(sectors.get("total")) or 0
        up_count = _number(sectors.get("up_count")) or 0
        ratio = up_count / total if total else None
        if ratio is not None:
            if ratio >= 0.6:
                score += 1.0
            elif ratio <= 0.3:
                score -= 1.0
            parts.append(f"{_round(up_count, 0)}/{_round(total, 0)} 个行业上涨")
    count = _number((limit_up or {}).get("count"))
    streak = _number((limit_up or {}).get("max_streak"))
    if count is not None:
        if count >= 80 and (streak or 0) >= 4:
            score += 0.5
        elif count < 30:
            score -= 0.5
        parts.append(f"涨停 {_round(count, 0)} 家、最高 {_round(streak, 0)} 连板")
    limit_down = _number((breadth or {}).get("limit_down"))
    if limit_down is not None and limit_down >= 30:
        score -= 0.5
        parts.append(f"跌停 {_round(limit_down, 0)} 家")

    if not parts:
        return {
            "factor": "结构", "reading": "行业与涨停数据缺失",
            "signal": "unknown", "score": None,
        }
    signal = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return {
        "factor": "结构", "reading": "；".join(parts), "signal": signal,
        "score": _round(score),
    }


def _extreme_flags(
    index_chg: float | None,
    breadth: dict | None,
    turnover: dict | None,
    volatility: float | None,
    below_ma20: int,
) -> list[str]:
    flags: list[str] = []
    up_ratio = _number((breadth or {}).get("up_ratio_pct"))
    limit_down = _number((breadth or {}).get("limit_down"))
    pace = _number((turnover or {}).get("pace_ratio"))
    if index_chg is not None and index_chg <= -2.5:
        flags.append("沪深300单日跌幅达到 2.5%")
    if up_ratio is not None and up_ratio <= 25:
        flags.append("上涨家数占比不足 25%")
    if limit_down is not None and limit_down >= 60:
        flags.append("跌停家数达到 60 家")
    if volatility is not None and volatility >= 35 and below_ma20 >= 2:
        flags.append("20 日年化波动率超过 35% 且多数宽基跌破 MA20")
    if (
        pace is not None and pace >= 1.5
        and index_chg is not None and index_chg <= -1.0
    ):
        flags.append("放量下跌，量能达到近 20 日均量的 1.5 倍以上")
    return flags


def _contradictions(signals: dict[str, dict]) -> list[str]:
    items: list[str] = []
    trend = signals["趋势"]["signal"]
    breadth = signals["市场宽度"]["signal"]
    volume = signals["量能"]["signal"]
    structure = signals["结构"]["signal"]
    if trend == "positive" and breadth == "negative":
        items.append("指数趋势偏强，但多数股票在跌，上涨广度不足")
    if trend == "negative" and breadth == "positive":
        items.append("指数在均线下方，但上涨家数占优，属于结构性修复而非全面转强")
    if volume == "negative" and trend == "positive":
        items.append("指数偏强但量能不足，突破的持续性需要观察")
    if volume == "positive" and trend == "negative":
        items.append("放量但指数在均线下方，先按风险释放处理，不把放量直接当成见底")
    if structure == "positive" and breadth == "negative":
        items.append("少数行业与涨停股活跃，但整体上涨家数不足，赚钱效应集中")
    return items


def _missing(overview: dict, signals: dict[str, dict]) -> list[str]:
    items: list[str] = []
    names = {
        "趋势": "指数日线",
        "市场宽度": "涨跌家数",
        "量能": "成交额与均量",
        "波动": "20 日波动率",
        "结构": "行业与涨停数据",
    }
    for factor, label in names.items():
        if signals[factor]["signal"] == "unknown":
            items.append(f"{label}缺失")
    if not overview.get("indices"):
        items.append("指数快照缺失")
    return items


def _invalidation(
    state: str,
    benchmark: dict | None,
) -> list[str]:
    name = str((benchmark or {}).get("name") or "沪深300")
    ma20 = _number((benchmark or {}).get("ma20"))
    high20 = _number((benchmark or {}).get("high_20d"))
    low20 = _number((benchmark or {}).get("low_20d"))
    ma20_text = f"（{_round(ma20, 2)}）" if ma20 is not None else ""
    high_text = f"（{_round(high20, 2)}）" if high20 is not None else ""
    low_text = f"（{_round(low20, 2)}）" if low20 is not None else ""

    if state == EXTREME:
        return [
            "跌停家数回落到 30 家以下、上涨家数占比回到 35% 以上，极端风险可降级",
            f"{name}不再创新低并重新站上 MA20{ma20_text}，才谈得上修复",
            "成交额缩到近 20 日均量以下且指数止跌，说明抛压开始释放",
        ]
    if state == WEAK:
        return [
            f"{name}重新站上 MA20{ma20_text}，且上涨家数占比连续两日超过 50%，弱势判断失效",
            "跌停家数回落到 20 家以下，成交额恢复到近 20 日均量的 90% 以上，风险等级可下调",
            "行业上涨家数占比连续两日超过 55%，结构由防御转向扩散",
        ]
    if state == STRONG:
        return [
            f"{name}跌破 MA20{ma20_text}，或上涨家数占比连续两日低于 40%，强势判断失效",
            "涨停家数较当前水平减半、最高连板高度下降，短线情绪转弱",
            "放量滞涨或领涨板块出现放量下跌，需先降低节奏预期",
        ]
    return [
        f"{name}放量突破 20 日高点{high_text}且上涨家数占比超过 60%，可上调整档",
        f"{name}跌破 20 日低点{low_text}，或跌停家数超过 30 家，需下调整档",
        "行业上涨家数占比连续两日低于 35%，震荡判断转向弱势",
    ]


def classify_market(overview: dict) -> dict:
    """把大盘全景归类为强势/震荡/弱势/极端风险，并给出依据与失效条件。

    纯函数：不联网，输入缺哪块就记在 ``missing`` 里，不抛异常。
    """
    overview = overview or {}
    items = _trend_items(overview)
    benchmark = _benchmark(items)
    index_chg = _index_change(overview, benchmark)
    breadth = overview.get("breadth") or None
    turnover = overview.get("turnover") or None

    trend = _trend_signal(items)
    breadth_signal = _breadth_signal(breadth)
    volume = _volume_signal(turnover, index_chg)
    volatility = _volatility_signal(items, trend["score"])
    structure = _structure_signal(
        overview.get("sectors"), overview.get("limit_up"), breadth
    )
    signals = {
        "趋势": trend,
        "市场宽度": breadth_signal,
        "量能": volume,
        "波动": volatility,
        "结构": structure,
    }

    scores = [
        item["score"] for item in signals.values()
        if item["score"] is not None
    ]
    total = sum(scores) if scores else None

    below_ma20 = sum(
        1 for item in items if item.get("above_ma20") is False
    )
    extreme_flags = _extreme_flags(
        index_chg, breadth, turnover,
        volatility.get("volatility_20d_pct"), below_ma20,
    )
    if len(extreme_flags) >= 2:
        state = EXTREME
    elif total is None:
        state = RANGE
    elif total >= 2.5:
        state = STRONG
    elif total <= -2.0:
        state = WEAK
    else:
        state = RANGE

    volatility_value = volatility.get("volatility_20d_pct")
    limit_down = _number((breadth or {}).get("limit_down"))
    if state == EXTREME:
        risk_level = "极高"
    elif state == WEAK:
        risk_level = (
            "高" if (
                (volatility_value is not None and volatility_value >= 25)
                or (limit_down is not None and limit_down >= 30)
                or (trend["score"] is not None and trend["score"] <= -1.5)
            ) else "中"
        )
    elif state == STRONG:
        risk_level = "中" if (
            volatility_value is not None and volatility_value >= 25
        ) else "低"
    else:
        risk_level = "中" if (
            (volatility_value is not None and volatility_value >= 25)
            or (breadth_signal["score"] is not None and breadth_signal["score"] <= -1)
            or (trend["score"] is not None and trend["score"] <= -1)
        ) else "低"

    missing = _missing(overview, signals)
    confidence = "高" if not missing else "中" if len(missing) <= 2 else "低"
    benchmark_name = str((benchmark or {}).get("name") or "沪深300")

    return {
        "state": state,
        "score": _round(total),
        "risk_level": risk_level,
        "confidence": confidence,
        "benchmark": benchmark_name,
        "basis": list(signals.values()),
        "extreme_flags": extreme_flags,
        "contradictions": _contradictions(signals),
        "invalidation": _invalidation(state, benchmark),
        "missing": missing,
        "metrics": {
            "index_chg_pct": _round(index_chg),
            "up_ratio_pct": breadth_signal.get("up_ratio_pct"),
            "limit_up": breadth_signal.get("limit_up"),
            "limit_down": breadth_signal.get("limit_down"),
            "total_yi": volume.get("total_yi"),
            "pace_ratio": volume.get("pace_ratio"),
            "volatility_20d_pct": volatility_value,
            "trend_score": trend["score"],
            "breadth_score": breadth_signal["score"],
            "volume_score": volume["score"],
            "volatility_score": volatility["score"],
            "structure_score": structure["score"],
        },
        "notes": [
            "状态由趋势、宽度、量能、波动与结构五项规则打分得出；"
            "极端信号出现两项以上时直接覆盖为极端风险",
            "盘中量能是按当前成交速度折算的估算值，收盘后应重新判断",
        ],
    }
