"""模块 1 验证：强势/震荡/弱势/极端风险分类、风险等级与失效条件。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.market_state import classify_market


def _trend(code, name, above20, above60, volatility, chg, ma20=3600.0):
    return {
        "code": code,
        "name": name,
        "price": ma20 + (100 if above20 else -100),
        "chg_pct": chg,
        "ma20": ma20,
        "ma60": ma20 - 80,
        "above_ma20": above20,
        "above_ma60": above60,
        "high_20d": ma20 + 200,
        "low_20d": ma20 - 200,
        "volatility_20d_pct": volatility,
    }


def _overview(
    trend,
    up,
    down,
    flat,
    limit_up,
    limit_down,
    total_yi,
    pace,
    sector_up,
    sector_total,
    max_streak=4,
    index_chg=0.1,
):
    total = up + down + flat
    return {
        "as_of": "2026-10-26 14:30:00",
        "indices": [{"code": "sh000300", "name": "沪深300", "chg_pct": index_chg}],
        "breadth": {
            "up": up, "down": down, "flat": flat,
            "up_ratio_pct": round(up / total * 100, 1),
            "limit_up": limit_up, "limit_down": limit_down,
        },
        "turnover": {"total_yi": total_yi, "pace_ratio": pace},
        "trend": trend,
        "sectors": {
            "up_count": sector_up, "down_count": sector_total - sector_up,
            "total": sector_total,
        },
        "limit_up": {
            "count": limit_up, "max_streak": max_streak,
            "streaks": [], "top_industries": [],
        },
        "news": [],
        "errors": {},
    }


def _strong() -> dict:
    trend = [
        _trend("sh000001", "上证指数", True, True, 12.0, 1.2),
        _trend("sz399001", "深证成指", True, True, 13.0, 1.5),
        _trend("sz399006", "创业板指", True, True, 14.0, 1.8),
        _trend("sh000300", "沪深300", True, True, 11.0, 1.0),
    ]
    return _overview(
        trend, 3200, 1800, 200, 95, 4, 13000, 1.25, 65, 90, 5,
        index_chg=1.0,
    )


def _weak() -> dict:
    trend = [
        _trend("sh000001", "上证指数", False, False, 26.0, -1.2),
        _trend("sz399001", "深证成指", False, False, 27.0, -1.5),
        _trend("sz399006", "创业板指", False, False, 28.0, -1.8),
        _trend("sh000300", "沪深300", False, False, 25.0, -1.1),
    ]
    return _overview(
        trend, 1400, 3800, 200, 20, 35, 9000, 1.2, 18, 90, 3,
        index_chg=-1.1,
    )


def _extreme() -> dict:
    trend = [
        _trend("sh000001", "上证指数", False, False, 42.0, -3.0),
        _trend("sz399001", "深证成指", False, False, 45.0, -3.6),
        _trend("sz399006", "创业板指", False, False, 48.0, -4.2),
        _trend("sh000300", "沪深300", False, False, 40.0, -3.2),
    ]
    return _overview(
        trend, 1000, 4300, 200, 10, 80, 12000, 1.6, 10, 90, 2,
        index_chg=-3.2,
    )


def _range() -> dict:
    trend = [
        _trend("sh000001", "上证指数", True, True, 18.0, 0.3),
        _trend("sz399001", "深证成指", False, False, 20.0, -0.2),
        _trend("sz399006", "创业板指", True, False, 22.0, 0.1),
        _trend("sh000300", "沪深300", False, False, 17.0, -0.1),
    ]
    return _overview(
        trend, 2400, 2400, 200, 50, 10, 10000, 1.0, 45, 90, 3,
        index_chg=-0.1,
    )


def test_strong_state() -> None:
    result = classify_market(_strong())
    assert result["state"] == "强势", result
    assert result["risk_level"] == "低", result["risk_level"]
    assert result["confidence"] == "高", result["confidence"]
    assert result["score"] > 3.0, result["score"]
    assert len(result["basis"]) == 5
    assert result["basis"][0]["factor"] == "趋势"
    assert result["basis"][0]["score"] == 2.0
    assert result["metrics"]["up_ratio_pct"] == 61.5
    assert result["missing"] == []
    assert any("MA20" in item for item in result["invalidation"])


def test_weak_state() -> None:
    result = classify_market(_weak())
    assert result["state"] == "弱势", result
    assert result["risk_level"] == "高", result["risk_level"]
    assert result["score"] <= -3.0, result["score"]
    assert len(result["invalidation"]) == 3
    assert any("弱势判断失效" in item for item in result["invalidation"])
    # 量能放大但指数下跌，应识别为放量下跌，而不是“放量=转强”
    volume = next(item for item in result["basis"] if item["factor"] == "量能")
    assert volume["signal"] == "negative", volume
    # 指数在均线下方、上涨家数少，属于一致偏弱，不应硬造矛盾
    assert result["contradictions"] == []


def test_extreme_state() -> None:
    result = classify_market(_extreme())
    assert result["state"] == "极端风险", result
    assert result["risk_level"] == "极高", result["risk_level"]
    assert len(result["extreme_flags"]) >= 2, result["extreme_flags"]
    assert any("跌停" in item for item in result["extreme_flags"])
    assert any("极端风险" in item for item in result["invalidation"])


def test_range_state_and_contradictions() -> None:
    result = classify_market(_range())
    assert result["state"] == "震荡", result
    assert result["risk_level"] == "低", result["risk_level"]
    assert abs(result["score"]) < 3.0, result["score"]
    # 趋势 -0.5、宽度 0、量能 0：没有明显矛盾
    assert result["contradictions"] == []


def test_missing_sections_lower_confidence() -> None:
    result = classify_market({"indices": [{"code": "sh000001"}]})
    assert result["state"] == "震荡", result
    assert result["confidence"] == "低", result["confidence"]
    assert len(result["missing"]) >= 4, result["missing"]
    assert result["score"] is None
    # 缺数据时不编造依据
    assert all(item["signal"] == "unknown" for item in result["basis"])


def test_classify_never_raises() -> None:
    for overview in (None, {}, {"trend": []}, {"breadth": {}}):
        result = classify_market(overview)
        assert result["state"] in ("强势", "震荡", "弱势", "极端风险")
        assert result["risk_level"] in ("低", "中", "高", "极高")
        assert isinstance(result["basis"], list)
        assert isinstance(result["invalidation"], list)


def main() -> None:
    test_strong_state()
    test_weak_state()
    test_extreme_state()
    test_range_state_and_contradictions()
    test_missing_sections_lower_confidence()
    test_classify_never_raises()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
