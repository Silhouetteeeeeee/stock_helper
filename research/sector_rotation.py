"""模块 3：行业轮动分析。

用同花顺行业板块的当日快照和历史指数，回答三个问题：

* 哪些行业相对基准在走强，强弱是在延续还是在切换
* 上涨广度够不够，还是只靠少数个股和权重
* 现在有哪些反转信号，哪些结论只是一天的涨幅

一天的涨幅只占打分的一小部分，主要权重给 5/20/60 日相对基准的跑赢幅度和
10 日内的相对强度天数。所有判断都是规则计算结果，不预测板块下一日的涨跌。
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import akshare as ak

from stock.market import session_state

BENCHMARK_NAME = "沪深300"
BENCHMARK_CODE = "sh000300"

STRONG = "持续强势"
EMERGING = "由弱转强"
WEAKENING = "由强转弱"
WEAK = "持续弱势"
NEUTRAL = "中性"

LEADER_LIMIT = 8
LIST_LIMIT = 5
HISTORY_DAYS = 140
RS_WINDOW = 10
MAX_WORKERS = 6
CACHE_SECONDS = 600

# 打分权重：短期动量只占一成，避免“只看一天涨幅”
WEIGHTS = {
    "today": 0.10,
    "ret_5d": 0.30,
    "ret_20d": 0.25,
    "ret_60d": 0.15,
    "breadth": 0.10,
    "persistence": 0.05,
    "ma20": 0.05,
}

_cache: dict = {"at": None, "key": None, "data": None}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _records(frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return frame.to_dict("records")


def _as_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _round(value, digits: int = 2):
    return None if value is None else round(value, digits)


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _mean(values: list[float]) -> float | None:
    values = [item for item in values if item is not None]
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    values = sorted(item for item in values if item is not None)
    if not values:
        return None
    return values[len(values) // 2]


# ---------------------------------------------------------------------------
# 纯计算
# ---------------------------------------------------------------------------
def _aligned_return(
    dates: list[str], closes: list[float], last_date: str, days: int
) -> float | None:
    """以 ``last_date`` 及之前最后一个交易日为终点，算 ``days`` 日涨跌幅。"""
    end = None
    for index in range(len(dates) - 1, -1, -1):
        if dates[index] <= last_date:
            end = index
            break
    if end is None or end - days < 0:
        return None
    base = closes[end - days]
    if not base or base <= 0:
        return None
    return (closes[end] / base - 1) * 100


def _rs_days(
    dates: list[str], closes: list[float], benchmark: dict[str, float], window: int = RS_WINDOW
) -> int | None:
    """最近 ``window`` 个交易日里，有多少天跑赢基准。"""
    wins = 0
    used = 0
    start = max(1, len(closes) - window)
    for index in range(start, len(closes)):
        day = dates[index]
        previous_day = dates[index - 1]
        if day not in benchmark or previous_day not in benchmark:
            continue
        base = closes[index - 1]
        bench_base = benchmark[previous_day]
        if not base or not bench_base:
            continue
        own = closes[index] / base - 1
        other = benchmark[day] / bench_base - 1
        used += 1
        if own > other:
            wins += 1
    return wins if used >= 5 else None


def _trend_label(rank_5d: int | None, rank_20d: int | None, total: int) -> str:
    if rank_5d is None or rank_20d is None or total < 10:
        return NEUTRAL
    top = max(3, round(total * 0.15))
    bottom = total - top + 1
    strong_now, strong_mid = rank_5d <= top, rank_20d <= top
    weak_now, weak_mid = rank_5d >= bottom, rank_20d >= bottom
    if strong_now and strong_mid:
        return STRONG
    if strong_now and weak_mid:
        return EMERGING
    if strong_mid and weak_now:
        return WEAKENING
    if weak_now and weak_mid:
        return WEAK
    return NEUTRAL


def _persistence(rs_days: int | None, score: float | None) -> str:
    if rs_days is None:
        return "中" if score is not None and abs(score) >= 0.3 else "低"
    if rs_days >= 7 and score is not None and abs(score) >= 0.4:
        return "高"
    if rs_days >= 5:
        return "中"
    return "低"


def _reversal_signals(sector: dict) -> list[str]:
    signals: list[str] = []
    ret_20d = sector.get("ret_20d")
    excess_5d = sector.get("excess_5d")
    chg = sector.get("chg_pct")
    up_ratio = sector.get("up_ratio")
    amount_ratio = sector.get("amount_ratio")
    ma_above = sector.get("above_ma20")
    rank_5d = sector.get("rank_5d")
    rank_20d = sector.get("rank_20d")
    total = sector.get("total")
    if ret_20d is not None and ret_20d >= 15 and excess_5d is not None and excess_5d <= -1:
        signals.append(
            f"20日已涨{ret_20d:.2f}%，近5日跑输基准{abs(excess_5d):.2f}%，有高位转弱迹象"
        )
    if chg is not None and chg > 0 and up_ratio is not None and up_ratio < 0.4:
        signals.append(
            f"板块指数上涨但只有{up_ratio * 100:.0f}%个股上涨，涨幅集中在少数个股或权重"
        )
    if ma_above is False:
        signals.append("仍在20日均线下方，趋势尚未修复")
    if (
        amount_ratio is not None
        and amount_ratio >= 1.6
        and chg is not None
        and chg <= -0.5
    ):
        signals.append(f"成交额放大到同期{amount_ratio:.1f}倍却在下跌，资金可能在流出")
    if isinstance(total, int) and total >= 10 and rank_20d is not None and rank_5d is not None:
        if rank_20d <= round(total * 0.15) and rank_5d >= round(total * 0.7):
            signals.append("20日仍排在前段但5日掉到后段，属于由强转弱")
    return signals


def _scaled(value, scale: float) -> float | None:
    """把百分比读数按经验尺度压到 [-1, 1]；缺失就返回 None 而不是 0。"""
    return None if value is None else _clip(value / scale)


def _breadth_component(sector: dict) -> float | None:
    up_ratio = sector.get("up_ratio")
    samples = (sector.get("up_count") or 0) + (sector.get("down_count") or 0)
    if up_ratio is None or samples < 5:
        return None
    return _clip((up_ratio - 0.5) * 2)


def _persistence_component(sector: dict) -> float | None:
    rs_days = sector.get("rs_days_10")
    return None if rs_days is None else (rs_days - 5) / 5


def _score(sector: dict) -> float | None:
    """按权重合成轮动分；缺数据的维度不参与，权重重归一。

    只有广度而没有区间收益时不给分：单日广度不足以说明“轮动”。
    """
    if sector.get("excess_5d") is None and sector.get("excess_20d") is None:
        return None
    parts = (
        (_scaled(sector.get("excess_today"), 3), WEIGHTS["today"]),
        (_scaled(sector.get("excess_5d"), 8), WEIGHTS["ret_5d"]),
        (_scaled(sector.get("excess_20d"), 15), WEIGHTS["ret_20d"]),
        (_scaled(sector.get("excess_60d"), 30), WEIGHTS["ret_60d"]),
        (_breadth_component(sector), WEIGHTS["breadth"]),
        (_persistence_component(sector), WEIGHTS["persistence"]),
        (_scaled(sector.get("ma20_dev_pct"), 8), WEIGHTS["ma20"]),
    )
    total = 0.0
    weight = 0.0
    for value, item_weight in parts:
        if value is None:
            continue
        total += value * item_weight
        weight += item_weight
    if weight == 0:
        return None
    return round(_clip(total / weight) * 10, 2)


def make_benchmark(closes: dict[str, float]) -> dict:
    """把 ``{日期: 收盘}`` 整理成算超额收益用的基准结构，只算一次。"""
    dates = sorted(closes)
    return {
        "dates": dates,
        "closes": [closes[day] for day in dates],
        "map": dict(closes),
        "last": dates[-1] if dates else None,
    }


def sector_stats(
    summary: dict,
    history: list[dict],
    benchmark: dict,
    total: int = 0,
    progress: float | None = None,
) -> dict:
    """把一块当日快照和一段历史合并成一个板块的轮动指标。"""
    name = str(summary.get("板块") or "").strip()
    chg = _as_float(summary.get("涨跌幅"))
    amount = _as_float(summary.get("总成交额"))
    up_count = int(_as_float(summary.get("上涨家数")) or 0)
    down_count = int(_as_float(summary.get("下跌家数")) or 0)
    net_inflow = _as_float(summary.get("净流入"))

    dates = [str(row.get("日期") or "")[:10] for row in history]
    closes = [_as_float(row.get("收盘价")) for row in history]
    rows = [
        (day, close, _as_float(row.get("成交额")))
        for day, close, row in zip(dates, closes, history)
        if day and close
    ]
    dates = [item[0] for item in rows]
    closes = [item[1] for item in rows]
    amounts = [item[2] for item in rows]

    last_date = dates[-1] if dates else ""
    ret_5d = _aligned_return(dates, closes, last_date, 5) if dates else None
    ret_20d = _aligned_return(dates, closes, last_date, 20) if dates else None
    ret_60d = _aligned_return(dates, closes, last_date, 60) if dates else None

    bench_dates = benchmark.get("dates") or []
    bench_closes = benchmark.get("closes") or []
    bench_map = benchmark.get("map") or {}

    def bench_return(days: int) -> float | None:
        if not last_date or not bench_dates:
            return None
        return _aligned_return(bench_dates, bench_closes, last_date, days)

    bench_5d, bench_20d, bench_60d = (
        bench_return(5), bench_return(20), bench_return(60)
    )

    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    above_ma20 = closes[-1] > ma20 if ma20 else None
    ma20_dev = (closes[-1] / ma20 - 1) * 100 if ma20 else None

    recent_amounts = [item for item in amounts[-5:] if item]
    average_amount = _mean(recent_amounts)
    amount_ratio = None
    if amount and average_amount:
        expected = average_amount
        if progress is not None and 0.1 <= progress <= 0.95:
            expected = average_amount * progress
        if expected > 0:
            amount_ratio = amount * 1e8 / expected

    sector = {
        "name": name,
        "chg_pct": _round(chg),
        "amount": _round(amount),
        "net_inflow": _round(net_inflow),
        "up_count": up_count,
        "down_count": down_count,
        "up_ratio": _round(up_count / (up_count + down_count)) if up_count + down_count >= 3 else None,
        "leader": {
            "name": str(summary.get("领涨股") or "").strip() or None,
            "chg_pct": _round(_as_float(summary.get("领涨股-涨跌幅"))),
        },
        "ret_5d": _round(ret_5d),
        "ret_20d": _round(ret_20d),
        "ret_60d": _round(ret_60d),
        "excess_5d": _round(ret_5d - bench_5d) if ret_5d is not None and bench_5d is not None else None,
        "excess_20d": _round(ret_20d - bench_20d) if ret_20d is not None and bench_20d is not None else None,
        "excess_60d": _round(ret_60d - bench_60d) if ret_60d is not None and bench_60d is not None else None,
        "rs_days_10": _rs_days(dates, closes, bench_map) if dates else None,
        "ma20": _round(ma20),
        "ma20_dev_pct": _round(ma20_dev),
        "above_ma20": above_ma20,
        "amount_ratio": _round(amount_ratio),
        "history_days": len(closes),
        "last_date": last_date or None,
        "total": total,
    }
    return sector


# ---------------------------------------------------------------------------
# 数据抓取
# ---------------------------------------------------------------------------
def _history(name: str, start: str, end: str) -> list[dict]:
    frame = ak.stock_board_industry_index_ths(
        symbol=name, start_date=start, end_date=end
    )
    return _records(frame)


def _benchmark_closes(start: str, end: str) -> tuple[dict[str, float], str | None]:
    frame = ak.stock_zh_index_daily(symbol=BENCHMARK_CODE)
    rows = _records(frame)
    closes: dict[str, float] = {}
    for row in rows:
        day = str(row.get("date") or "")[:10]
        close = _as_float(row.get("close"))
        if day and close and start <= day.replace("-", "") <= end:
            closes[day] = close
    return closes, (max(closes) if closes else None)


def _benchmark_today() -> float | None:
    frame = ak.stock_zh_index_spot_sina()
    for row in _records(frame):
        if str(row.get("代码") or "") == BENCHMARK_CODE:
            return _as_float(row.get("涨跌幅"))
    return None


def _fetch_all(
    names: list[str], start: str, end: str
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    history: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def load(name: str) -> tuple[str, list[dict] | None, str | None]:
        try:
            return name, _history(name, start, end), None
        except Exception as error:  # noqa: BLE001 - 单个板块失败不影响其他板块
            return name, None, str(error)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for name, rows, error in pool.map(load, names):
            if rows is not None:
                history[name] = rows
            else:
                errors[name] = error or "空数据"
    return history, errors


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def _rank(values: list[tuple[str, float | None]]) -> dict[str, int]:
    usable = sorted(
        ((name, value) for name, value in values if value is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    return {name: index for index, (name, _) in enumerate(usable, start=1)}


def _public(sector: dict) -> dict:
    keys = (
        "name", "chg_pct", "excess_5d", "excess_20d", "excess_60d", "ret_5d",
        "ret_20d", "ret_60d", "rs_days_10", "up_ratio", "up_count", "down_count",
        "above_ma20", "ma20_dev_pct", "amount", "amount_ratio", "net_inflow",
        "leader", "trend", "persistence", "score", "drivers", "reversal_signals",
    )
    return {key: sector.get(key) for key in keys}


def _drivers(sector: dict) -> list[str]:
    items: list[str] = []
    if sector.get("chg_pct") is not None:
        items.append(f"今日{sector['chg_pct']:+.2f}%")
    if sector.get("excess_5d") is not None:
        items.append(f"5日超额{sector['excess_5d']:+.2f}%")
    if sector.get("excess_20d") is not None:
        items.append(f"20日超额{sector['excess_20d']:+.2f}%")
    if sector.get("rs_days_10") is not None:
        items.append(f"近10日有{sector['rs_days_10']}天跑赢基准")
    if sector.get("up_ratio") is not None:
        items.append(f"上涨家数占比{sector['up_ratio'] * 100:.0f}%")
    if sector.get("amount_ratio") is not None:
        items.append(f"成交额约为近期同期{sector['amount_ratio']:.1f}倍")
    leader = sector.get("leader") or {}
    if leader.get("name"):
        items.append(f"领涨股{leader['name']}{leader.get('chg_pct') or 0:+.2f}%")
    return items


def build_sector_rotation(limit: int = LEADER_LIMIT, force: bool = False) -> dict:
    """抓取行业板块快照与历史，返回轮动排序、强弱切换和反转信号。"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    key = f"{today}:{limit}"
    cached_at = _cache.get("at")
    if (
        not force
        and _cache.get("key") == key
        and _cache.get("data")
        and cached_at
        and (now - cached_at).total_seconds() < CACHE_SECONDS
    ):
        return _cache["data"]

    errors: dict[str, str] = {}
    notes: list[str] = []

    try:
        summary_rows = _records(ak.stock_board_industry_summary_ths())
    except Exception as error:  # noqa: BLE001
        return {
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
            "sectors": [],
            "leaders": [],
            "emerging": [],
            "weakening": [],
            "persistent": [],
            "weak": [],
            "ranking": [],
            "breadth": None,
            "notes": [],
            "errors": {"sectors": f"行业快照：{error}"},
        }

    names = [
        str(row.get("板块") or "").strip()
        for row in summary_rows
        if str(row.get("板块") or "").strip()
    ]
    total = len(names)

    start_date = (now - timedelta(days=HISTORY_DAYS * 2)).strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")

    benchmark_closes: dict[str, float] = {}
    try:
        benchmark_closes, _ = _benchmark_closes(start_date, end_date)
        if not benchmark_closes:
            errors["benchmark"] = "基准日线为空"
    except Exception as error:  # noqa: BLE001
        errors["benchmark"] = f"基准日线：{error}"
    benchmark = make_benchmark(benchmark_closes)

    history, history_errors = _fetch_all(names, start_date, end_date)
    if history_errors:
        sample = "；".join(
            f"{name}：{reason}" for name, reason in list(history_errors.items())[:3]
        )
        errors["history"] = f"板块历史 {len(history_errors)} 个失败，例如 {sample}"

    try:
        benchmark_today = _benchmark_today()
    except Exception as error:  # noqa: BLE001
        benchmark_today = None
        errors["benchmark_today"] = f"基准当日行情：{error}"

    session = session_state(now.strftime("%Y-%m-%d %H:%M:%S"), today)
    progress = session.get("progress")
    if progress is None:
        notes.append("交易时段未知，成交额放大倍数未按盘中进度折算")

    sectors: list[dict] = []
    for row in summary_rows:
        name = str(row.get("板块") or "").strip()
        if not name:
            continue
        sector = sector_stats(
            row,
            history.get(name) or [],
            benchmark,
            total=total,
            progress=progress,
        )
        chg = sector.get("chg_pct")
        sector["excess_today"] = (
            _round(chg - benchmark_today)
            if chg is not None and benchmark_today is not None
            else None
        )
        sector["score"] = _score(sector)
        sectors.append(sector)

    bench_rank = _rank([(item["name"], item.get("excess_today")) for item in sectors])
    rank_5d = _rank([(item["name"], item.get("excess_5d")) for item in sectors])
    rank_20d = _rank([(item["name"], item.get("excess_20d")) for item in sectors])

    for sector in sectors:
        name = sector["name"]
        sector["rank_today"] = bench_rank.get(name)
        sector["rank_5d"] = rank_5d.get(name)
        sector["rank_20d"] = rank_20d.get(name)
        sector["trend"] = _trend_label(
            sector["rank_5d"], sector["rank_20d"], total
        )
        sector["persistence"] = _persistence(
            sector.get("rs_days_10"), sector.get("score")
        )
        sector["reversal_signals"] = _reversal_signals(sector)
        sector["drivers"] = _drivers(sector)

    by_score = sorted(
        [item for item in sectors if item.get("score") is not None],
        key=lambda item: item["score"],
        reverse=True,
    )
    usable_days = max((item.get("history_days") or 0) for item in sectors) if sectors else 0
    if usable_days < 61:
        notes.append("板块历史不足 60 个交易日，60 日相对强度可能为空")

    up_industries = sum(1 for item in sectors if (item.get("chg_pct") or 0) > 0)
    down_industries = sum(1 for item in sectors if (item.get("chg_pct") or 0) < 0)
    breadth = {
        "up_industries": up_industries,
        "down_industries": down_industries,
        "up_ratio": _round(up_industries / (up_industries + down_industries))
        if up_industries + down_industries
        else None,
        "median_chg_pct": _round(_median([item.get("chg_pct") for item in sectors])),
    }

    def pick(label: str, size: int) -> list[dict]:
        picked = [item for item in by_score if item["trend"] == label]
        return [_public(item) for item in picked[:size]]

    leaders = [_public(item) for item in by_score[: max(1, min(limit, 20))]]
    weak = [_public(item) for item in by_score[::-1][:LIST_LIMIT]]
    persistent = [
        _public(item) for item in by_score if item["persistence"] == "高"
    ][:LIST_LIMIT]

    notes.append("行业强弱按相对沪深300的超额收益排序，单日涨幅权重仅一成")
    notes.append("盘中数据按交易进度折算成交额，开盘初期倍数波动较大")

    result = {
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": max(
            (item.get("last_date") or "" for item in sectors), default=""
        ) or None,
        "session": session,
        "benchmark": BENCHMARK_NAME,
        "benchmark_today": _round(benchmark_today),
        "intraday": (progress or 0) < 0.999,
        "total_sectors": total,
        "breadth": breadth,
        "leaders": leaders,
        "emerging": pick(EMERGING, LIST_LIMIT),
        "weakening": pick(WEAKENING, LIST_LIMIT),
        "persistent": persistent,
        "weak": weak,
        "ranking": [
            {
                "name": item["name"],
                "score": item.get("score"),
                "chg_pct": item.get("chg_pct"),
                "trend": item.get("trend"),
            }
            for item in by_score
        ],
        "notes": notes,
        "errors": errors,
    }
    _cache.update({"at": now, "key": key, "data": result})
    return result
