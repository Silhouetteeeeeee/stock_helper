"""估值摘要验证：分位计算、同业对比、提示规则、缓存与降级。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.stock import Stock

import stock.valuation as val

CODE = "605499"
BOARD = "BK9999"
E8 = 1e8

calls = {"history": 0, "peers": 0}


def make_history(pe_end: float = 19.75, pb_end: float = 2.95, periods: int = 40) -> list[dict]:
    """40 个交易日的估值序列，PE 从 10 线性涨到 pe_end，PB 从 1 涨到 pb_end。"""
    start = datetime(2024, 1, 2)
    rows = []
    for i in range(periods):
        rows.append({
            "code": CODE,
            "trade_date": (start + timedelta(days=7 * i)).strftime("%Y-%m-%d"),
            "name": "测试股份",
            "pe_ttm": 10 + (pe_end - 10) * i / (periods - 1),
            "pb_mrq": 1 + (pb_end - 1) * i / (periods - 1),
            "total_mv": 100 * E8,
            "close": 20.0,
            "board_code": BOARD,
            "board_name": "测试行业",
            "fetched_at": fresh_stamp(),
        })
    return rows


PEERS = [
    {"code": "000001", "name": "甲公司", "pe_ttm": 6.0, "pb_mrq": 0.8, "total_mv": 900 * E8},
    {"code": "000002", "name": "乙公司", "pe_ttm": 8.0, "pb_mrq": 1.0, "total_mv": 800 * E8},
    {"code": "000003", "name": "丙公司", "pe_ttm": 10.0, "pb_mrq": 1.2, "total_mv": 700 * E8},
    {"code": "000004", "name": "丁公司", "pe_ttm": 12.0, "pb_mrq": 1.4, "total_mv": 600 * E8},
    {"code": "000005", "name": "亏钱公司", "pe_ttm": -3.0, "pb_mrq": 0.5, "total_mv": 500 * E8},
    {"code": CODE, "name": "测试股份", "pe_ttm": 19.75, "pb_mrq": 2.95, "total_mv": 100 * E8},
]

def fresh_stamp() -> str:
    """缓存有效期是 6 小时，夹具时间要跟着当前时间走，否则晚上跑就过期了。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


HISTORY = make_history()


def codes(valuation: dict) -> list[str]:
    return [flag["code"] for flag in valuation["flags"]]


def age_cache(db_path: str, hours: int) -> None:
    stamp = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"update {val.HISTORY_TABLE} set fetched_at = ?", (stamp,))
        conn.execute(f"update {val.PEER_TABLE} set fetched_at = ?", (stamp,))


def fake_history(code: str, **kwargs) -> list[dict]:
    calls["history"] += 1
    return [dict(row) for row in HISTORY]


def fake_peers(board_code: str, trade_date: str) -> list[dict]:
    calls["peers"] += 1
    return [
        {**peer, "board_code": board_code, "trade_date": trade_date,
         "fetched_at": fresh_stamp()}
        for peer in PEERS
    ]


def broken(*args, **kwargs):
    raise val.ValuationError("模拟网络失败")


def main() -> None:
    db_path = os.path.join(tempfile.mkdtemp(), "valuation_test.db")
    val.DB_PATH = db_path
    val.fetch_history = fake_history  # type: ignore[assignment]
    val.fetch_peers = fake_peers  # type: ignore[assignment]
    val.get_stock = lambda code: Stock(code=code, name="测试股份")  # type: ignore[assignment]

    # -- 分位与同业 ---------------------------------------------------------
    valuation = val.build_valuation(CODE, years=5)
    assert valuation["available"] is True
    assert valuation["code"] == CODE
    assert valuation["name"] == "测试股份"
    assert valuation["as_of"] == HISTORY[-1]["trade_date"]

    pe = valuation["valuation"]["pe_ttm"]
    assert pe["value"] == 19.75, pe
    assert pe["samples"] == 40, pe
    assert pe["percentile"] == 100.0, pe       # 最新值就是历史最高
    assert round(pe["min"], 2) == 10.0, pe
    assert round(pe["max"], 2) == 19.75, pe

    pb = valuation["valuation"]["pb_mrq"]
    assert pb["percentile"] == 100.0, pb

    industry = valuation["industry"]
    assert industry["board"] == "测试行业"
    assert industry["count"] == 6
    # PE 中值只看盈利公司：6/8/10/12/19.75 的中位数是 10
    assert industry["median_pe"] == 10.0, industry
    assert industry["rank_pe"] == 5, industry
    assert industry["rank_total"] == 5, industry
    assert round(industry["premium_pe"], 1) == 97.5, industry

    found = codes(valuation)
    for expected in ("pe_high", "pb_high", "premium_to_peers"):
        assert expected in found, (expected, found)
    assert "pe_low" not in found, found

    text = val.format_valuation(valuation)
    assert "测试股份(605499)" in text, text
    assert "PE(TTM) 19.75，近 5 年 100% 分位" in text, text
    assert "行业 测试行业（6 家）" in text, text
    assert "本公司 PE 由低到高第 5/5" in text, text
    assert "[测试股份 PE 19.8/PB 2.95]" in text, text
    assert "亏钱公司 亏损/PB 0.50" in text, text
    assert "较行业中值 10.00 溢价 98%" in text, text

    # -- 便宜的情况 ---------------------------------------------------------
    cheap_history = make_history()
    cheap_history[-1].update({"pe_ttm": 10.0, "pb_mrq": 1.0})  # 最新值回到区间最低
    cheap = val.valuation_from_rows(
        cheap_history,
        peers=[{**peer, "pe_ttm": 30.0, "pb_mrq": 3.0} for peer in PEERS],
    )
    # 区间内两个值（起点和最新）都是 10.0
    assert cheap["valuation"]["pe_ttm"]["percentile"] == 2 / 40 * 100, cheap["valuation"]["pe_ttm"]
    cheap_found = codes(cheap)
    assert "pe_low" in cheap_found, cheap_found
    assert "discount_to_peers" in cheap_found, cheap_found

    # -- 亏损公司：PE 为负，分位不适用 ---------------------------------------
    loss = val.valuation_from_rows([{**row, "pe_ttm": -5.0} for row in HISTORY])
    assert "pe_negative" in codes(loss), codes(loss)
    assert loss["valuation"]["pe_ttm"]["percentile"] is None
    assert "分位不适用" in val.format_valuation(loss)

    # -- 历史太短 -----------------------------------------------------------
    short = val.valuation_from_rows(make_history(periods=10))
    assert "short_history" in codes(short), codes(short)

    # -- 空数据 -------------------------------------------------------------
    empty = val.valuation_from_rows([], name="测试股份")
    assert empty["available"] is False
    assert empty["summary"] == "测试股份：暂无估值数据"

    # -- 缓存：第二次不抓 ---------------------------------------------------
    db_path = os.path.join(tempfile.mkdtemp(), "valuation_cache.db")
    val.DB_PATH = db_path
    calls.update(history=0, peers=0)
    val.ensure_valuation(CODE)
    assert calls == {"history": 1, "peers": 1}, calls
    val.ensure_valuation("SH605499")
    assert calls == {"history": 1, "peers": 1}, calls

    # -- force 无视缓存 -----------------------------------------------------
    val.ensure_valuation(CODE, force=True)
    assert calls == {"history": 2, "peers": 2}, calls

    # -- 缓存过期后重抓 -----------------------------------------------------
    age_cache(db_path, 7)
    val.ensure_valuation(CODE)
    assert calls == {"history": 3, "peers": 3}, calls

    # -- 网络失败：有缓存就降级，并标记 stale --------------------------------
    age_cache(db_path, 7)
    val.fetch_history = broken  # type: ignore[assignment]
    val.fetch_peers = broken  # type: ignore[assignment]
    data = val.ensure_valuation(CODE)
    assert data["stale"] is True, data
    assert len(data["history"]) == 40, len(data["history"])
    assert data["peers_error"], data
    assert len(data["peers"]) == 6, data["peers"]     # 同业退回缓存
    stale = val.valuation_from_rows(data["history"], data["peers"], stale=True)
    assert "stale_data" in codes(stale), codes(stale)

    # -- 同业抓不到但个股正常：只降级同业 -------------------------------------
    val.fetch_peers = broken  # type: ignore[assignment]
    val.fetch_history = fake_history  # type: ignore[assignment]
    val.ensure_valuation(CODE, force=True)
    fresh = val.build_valuation(CODE)
    assert fresh["stale"] is False, fresh
    assert fresh["peers_error"], fresh
    assert fresh["industry"]["count"] == 6, fresh["industry"]

    # -- 没有缓存又抓不到：报错 ---------------------------------------------
    val.DB_PATH = os.path.join(tempfile.mkdtemp(), "empty.db")
    val.fetch_history = broken  # type: ignore[assignment]
    try:
        val.ensure_valuation(CODE)
    except val.ValuationError:
        pass
    else:
        raise AssertionError("没有缓存时应该报错")

    try:
        val.build_valuation("")
    except val.ValuationError:
        pass
    else:
        raise AssertionError("空代码没有报错")

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
