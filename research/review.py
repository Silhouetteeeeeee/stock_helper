"""模块 6：复盘与预测评估。

两件事：

* 存档：把当天规则引擎给出的市场状态、行业强弱、候选股名单和（可选的）
  策略方案写进本地库，一天一条，重复保存覆盖。
* 复盘：对每条存档算之后 ``horizon`` 个交易日的实际走势，统计命中率、
  相对沪深300的超额收益、区间最大回撤，并把没命中的样本归到具体原因。

复盘只统计已经走完观察窗口的判断；没走完的标成"待观察"，不参与命中率，
避免把还在浮亏或浮盈的样本提前算成结论。所有收益按收盘价计算，未考虑
停牌、涨跌停无法成交等执行摩擦，成本按往返 ``ROUND_TRIP_COST_PCT`` 估算。
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import akshare as ak

from research.market_state import (
    BENCHMARK_CODE,
    EXTREME,
    RANGE,
    STRONG,
    WEAK,
    classify_market,
)
from research.screening import screen_candidates
from research.sector_rotation import build_sector_rotation
from research.store import load_reports, save_report
from stock.market import build_market_overview

BENCHMARK_NAME = "沪深300"
HORIZON = 5                     # 默认观察 5 个交易日
MAX_REPORTS = 20
MAX_CANDIDATES = 8
MAX_MISTAKES = 10
CACHE_SECONDS = 900
ROUND_TRIP_COST_PCT = 0.12      # 佣金、过户费与卖出印花税的粗估
RANGE_BAND_PCT = 3.0            # 震荡状态认为基准 5 日涨跌幅在 ±3% 以内
MAX_WORKERS = 8

STATE_EXPECTATION = {
    STRONG: f"基准 {HORIZON} 日内上涨",
    RANGE: f"基准 {HORIZON} 日内涨跌幅在 ±{int(RANGE_BAND_PCT)}% 以内",
    WEAK: f"基准 {HORIZON} 日内下跌",
    EXTREME: "基准出现明显下跌",
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


def _mean(values: list[float]) -> float | None:
    usable = [item for item in values if item is not None]
    return sum(usable) / len(usable) if usable else None


def _pct(hits: list[bool]) -> float | None:
    return round(sum(1 for item in hits if item) / len(hits) * 100, 1) if hits else None


def _series(rows: list[dict]) -> dict:
    """兼容个股日线（date/close）与行业指数（日期/收盘价）两种字段名。"""
    dates: list[str] = []
    closes: list[float] = []
    for row in rows or []:
        day = str(row.get("date") or row.get("日期") or "")[:10]
        close = _as_float(row.get("close"))
        if close is None:
            close = _as_float(row.get("收盘价"))
        if day and close:
            dates.append(day)
            closes.append(close)
    return {"dates": dates, "closes": closes}


def _index_at(dates: list[str], day: str) -> int | None:
    """``day`` 及之前最后一个交易日的下标。"""
    found = None
    for index, value in enumerate(dates):
        if value <= day:
            found = index
        else:
            break
    return found


def _forward_window(series: dict, trade_date: str, horizon: int) -> dict | None:
    """从存档日收盘算起，往后 ``horizon`` 个交易日的收益与回撤。"""
    dates, closes = series.get("dates") or [], series.get("closes") or []
    start = _index_at(dates, trade_date)
    if start is None:
        return None
    end = min(start + horizon, len(closes) - 1)
    entry = closes[start]
    if not entry or entry <= 0:
        return None
    path = closes[start:end + 1]
    return {
        "entry_date": dates[start],
        "entry_close": _round(entry),
        "end_date": dates[end],
        "end_close": _round(closes[end]),
        "days": end - start,
        "complete": end - start >= horizon,
        "return_pct": _round((closes[end] / entry - 1) * 100),
        "max_drawdown_pct": _round((min(path) / entry - 1) * 100),
        "max_gain_pct": _round((max(path) / entry - 1) * 100),
    }


def _window_return(series: dict, start_date: str, end_date: str) -> float | None:
    dates, closes = series.get("dates") or [], series.get("closes") or []
    start = _index_at(dates, start_date)
    end = _index_at(dates, end_date)
    if start is None or end is None or end <= start:
        return None
    base = closes[start]
    if not base or base <= 0:
        return None
    return (closes[end] / base - 1) * 100


def _direction_hit(state: str | None, benchmark_return: float | None) -> bool | None:
    if benchmark_return is None or state not in STATE_EXPECTATION:
        return None
    if state == STRONG:
        return benchmark_return > 0
    if state in (WEAK, EXTREME):
        return benchmark_return < 0
    return abs(benchmark_return) <= RANGE_BAND_PCT


# ---------------------------------------------------------------------------
# 单条判断的评估（纯计算）
# ---------------------------------------------------------------------------
def evaluate_market(
    market: dict, benchmark: dict, trade_date: str, horizon: int = HORIZON
) -> dict:
    state = (market or {}).get("state")
    window = _forward_window(benchmark, trade_date, horizon)
    result = {
        "state": state,
        "risk_level": (market or {}).get("risk_level"),
        "expectation": STATE_EXPECTATION.get(state),
        "status": "数据不足",
        "benchmark_return_pct": None,
        "days": 0,
        "hit": None,
    }
    if window is None:
        return result
    result.update({
        "status": "已完成" if window["complete"] else "待观察",
        "entry_date": window["entry_date"],
        "entry_close": window["entry_close"],
        "end_date": window["end_date"],
        "benchmark_return_pct": window["return_pct"],
        "days": window["days"],
    })
    if window["complete"]:
        result["hit"] = _direction_hit(state, window["return_pct"])
    return result


def evaluate_candidate(
    candidate: dict,
    rows: list[dict],
    benchmark: dict,
    trade_date: str,
    horizon: int = HORIZON,
) -> dict:
    code = (candidate or {}).get("code")
    name = (candidate or {}).get("name")
    result = {
        "code": code,
        "name": name,
        "score": (candidate or {}).get("score"),
        "status": "数据不足",
        "return_pct": None,
        "benchmark_return_pct": None,
        "excess_pct": None,
        "net_return_pct": None,
        "max_drawdown_pct": None,
        "days": 0,
        "hit": None,
    }
    window = _forward_window(_series(rows), trade_date, horizon)
    if window is None:
        return result
    bench_return = _window_return(benchmark, window["entry_date"], window["end_date"])
    result.update({
        "status": "已完成" if window["complete"] else "待观察",
        "entry_date": window["entry_date"],
        "end_date": window["end_date"],
        "return_pct": window["return_pct"],
        "net_return_pct": _round(window["return_pct"] - ROUND_TRIP_COST_PCT),
        "max_drawdown_pct": window["max_drawdown_pct"],
        "days": window["days"],
    })
    if bench_return is not None:
        result["benchmark_return_pct"] = _round(bench_return)
        result["excess_pct"] = _round(window["return_pct"] - bench_return)
    if window["complete"] and result["excess_pct"] is not None:
        result["hit"] = result["excess_pct"] > 0
    return result


def evaluate_sector(
    name: str,
    rows: list[dict],
    benchmark: dict,
    trade_date: str,
    horizon: int = HORIZON,
    expect_positive: bool = True,
) -> dict:
    result = {
        "name": name,
        "bucket": "领涨" if expect_positive else "弱势",
        "status": "数据不足",
        "return_pct": None,
        "benchmark_return_pct": None,
        "excess_pct": None,
        "days": 0,
        "hit": None,
    }
    window = _forward_window(_series(rows), trade_date, horizon)
    if window is None:
        return result
    bench_return = _window_return(benchmark, window["entry_date"], window["end_date"])
    result.update({
        "status": "已完成" if window["complete"] else "待观察",
        "entry_date": window["entry_date"],
        "end_date": window["end_date"],
        "return_pct": window["return_pct"],
        "days": window["days"],
    })
    if bench_return is not None:
        result["benchmark_return_pct"] = _round(bench_return)
        result["excess_pct"] = _round(window["return_pct"] - bench_return)
    if window["complete"] and result["excess_pct"] is not None:
        result["hit"] = (
            result["excess_pct"] > 0 if expect_positive else result["excess_pct"] < 0
        )
    return result


def _mistakes(rows: list[dict]) -> list[dict]:
    """把没命中的判断翻译成可复盘的失误原因。"""
    items: list[dict] = []
    market = rows.get("market") or {}
    if market.get("hit") is False:
        items.append({
            "type": "市场状态",
            "detail": f"判断为{market.get('state')}（预期{market.get('expectation')}），"
                      f"基准实际 {market.get('benchmark_return_pct'):+.2f}%",
        })
    for sector in rows.get("sectors") or []:
        if sector.get("hit") is False:
            direction = "未跑赢" if sector.get("bucket") == "领涨" else "没有继续走弱"
            items.append({
                "type": "行业判断",
                "detail": f"{sector.get('bucket')}行业{sector.get('name')}{direction}基准，"
                          f"5日超额 {sector.get('excess_pct'):+.2f}%",
            })
    for candidate in rows.get("candidates") or []:
        if candidate.get("hit") is False:
            items.append({
                "type": "候选股",
                "detail": f"{candidate.get('name')}({candidate.get('code')}) "
                          f"跑输基准 {candidate.get('excess_pct'):+.2f}%，"
                          f"区间最大回撤 {candidate.get('max_drawdown_pct'):+.2f}%",
            })
    return items


def _is_complete(row: dict) -> bool:
    entries = [row.get("market") or {}]
    entries += row.get("sectors") or []
    entries += row.get("candidates") or []
    return any(item.get("status") == "已完成" for item in entries)


def _summary(rows: list[dict]) -> dict:
    market_hits = [
        row["market"]["hit"] for row in rows
        if (row.get("market") or {}).get("hit") is not None
    ]
    sector_hits = [
        item["hit"] for row in rows for item in row.get("sectors") or []
        if item.get("hit") is not None
    ]
    candidate_rows = [
        item for row in rows for item in row.get("candidates") or []
        if item.get("status") == "已完成" and item.get("hit") is not None
    ]
    candidate_hits = [item["hit"] for item in candidate_rows]
    candidate_excess = [item.get("excess_pct") for item in candidate_rows]
    candidate_returns = [item.get("return_pct") for item in candidate_rows]
    drawdowns = [item.get("max_drawdown_pct") for item in candidate_rows]
    sector_excess = [
        item.get("excess_pct") for row in rows for item in row.get("sectors") or []
        if item.get("hit") is not None
    ]
    avg_excess = _mean(candidate_excess)
    return {
        "reports": len(rows),
        "evaluated": sum(1 for row in rows if _is_complete(row)),
        "market": {
            "judged": len(market_hits),
            "hit": sum(1 for item in market_hits if item),
            "hit_rate_pct": _pct(market_hits),
            "avg_benchmark_return_pct": _round(_mean([
                row["market"].get("benchmark_return_pct") for row in rows
                if (row.get("market") or {}).get("hit") is not None
            ])),
        },
        "sectors": {
            "judged": len(sector_hits),
            "hit": sum(1 for item in sector_hits if item),
            "hit_rate_pct": _pct(sector_hits),
            "avg_excess_pct": _round(_mean(sector_excess)),
        },
        "candidates": {
            "judged": len(candidate_hits),
            "hit": sum(1 for item in candidate_hits if item),
            "hit_rate_pct": _pct(candidate_hits),
            "avg_return_pct": _round(_mean(candidate_returns)),
            "avg_excess_pct": _round(avg_excess),
            "avg_max_drawdown_pct": _round(_mean(drawdowns)),
            "worst_drawdown_pct": _round(min(drawdowns)) if drawdowns else None,
            "cost_pct": ROUND_TRIP_COST_PCT,
            "net_avg_excess_pct": _round(avg_excess - ROUND_TRIP_COST_PCT)
            if avg_excess is not None else None,
        },
        "mistakes": [
            {"trade_date": row["trade_date"], **item}
            for row in rows for item in row.get("mistakes") or []
        ][:MAX_MISTAKES],
    }


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def _benchmark_history(start: str, end: str) -> dict:
    frame = ak.stock_zh_index_daily(symbol=BENCHMARK_CODE)
    rows = [
        row for row in _records(frame)
        if start <= str(row.get("date") or "")[:10].replace("-", "") <= end
    ]
    return _series(rows)


def _stock_history(code: str, start: str, end: str) -> list[dict]:
    frame = ak.stock_zh_a_daily(symbol=code)
    return [
        row for row in _records(frame)
        if start <= str(row.get("date") or "")[:10].replace("-", "") <= end
    ]


def _sector_history(name: str, start: str, end: str) -> list[dict]:
    frame = ak.stock_board_industry_index_ths(
        symbol=name, start_date=start, end_date=end
    )
    return _records(frame)


def _fetch(keys: list[str], loader) -> tuple[dict[str, list[dict]], dict[str, str]]:
    data: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def run(key: str) -> tuple[str, list[dict] | None, str | None]:
        try:
            return key, loader(key), None
        except Exception as error:  # noqa: BLE001 - 单个标的失败不影响整体复盘
            return key, None, str(error)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for key, rows, error in pool.map(run, keys):
            if rows is not None:
                data[key] = rows
            else:
                errors[key] = error or "空数据"
    return data, errors


# ---------------------------------------------------------------------------
# 存档
# ---------------------------------------------------------------------------
def _market_block(overview: dict) -> dict:
    state = classify_market(overview)
    return {
        "state": state.get("state"),
        "risk_level": state.get("risk_level"),
        "confidence": state.get("confidence"),
        "basis": [
            {"factor": item.get("factor"), "reading": item.get("reading")}
            for item in state.get("basis") or []
        ],
        "invalidation": state.get("invalidation") or [],
        "metrics": state.get("metrics") or {},
    }


def _sector_block(rotation: dict) -> dict:
    def top(items, size=MAX_CANDIDATES):
        return [
            {
                "name": item.get("name"),
                "score": item.get("score"),
                "trend": item.get("trend"),
                "excess_5d": item.get("excess_5d"),
            }
            for item in (items or [])[:size]
        ]

    return {
        "leaders": top(rotation.get("leaders")),
        "weak": top(rotation.get("weak")),
        "emerging": [item.get("name") for item in rotation.get("emerging") or []],
        "weakening": [item.get("name") for item in rotation.get("weakening") or []],
        "breadth": rotation.get("breadth"),
    }


def _candidate_block(screen: dict) -> list[dict]:
    return [
        {
            "code": item.get("code"),
            "name": item.get("name"),
            "board": item.get("board"),
            "score": item.get("score"),
            "price": item.get("price"),
        }
        for item in (screen.get("candidates") or [])[:MAX_CANDIDATES]
    ]


def _strategy_block(strategy) -> dict | None:
    """接受 build_strategy_plan 的返回，也接受压平后的同样字段。"""
    if not isinstance(strategy, dict):
        return None
    caps = strategy.get("caps") if isinstance(strategy.get("caps"), dict) else {}
    profile = strategy.get("profile") if isinstance(strategy.get("profile"), dict) else {}
    stance = strategy.get("stance")
    total = caps.get("total_pct", strategy.get("total_pct"))
    if not stance and total is None:
        return None
    return {
        "stance": stance,
        "total_pct": total,
        "single_pct": caps.get("single_pct", strategy.get("single_pct")),
        "stop_loss_pct": caps.get("stop_loss_pct", strategy.get("stop_loss_pct")),
        "position_pct": profile.get("position_pct", strategy.get("position_pct")),
    }


def build_snapshot(strategy=None, force: bool = False) -> dict:
    """抓取并整理当天要存档的判断；某一段失败只记录错误，不拖垮整份快照。"""
    now = datetime.now()
    errors: dict[str, str] = {}
    market: dict = {}
    sectors: dict = {}
    candidates: list[dict] = []
    trade_date = ""

    try:
        overview = build_market_overview()
        market = _market_block(overview)
        trade_date = str(overview.get("as_of") or "")[:10]
    except Exception as error:  # noqa: BLE001 - 一段失败不影响其他段
        errors["market"] = f"大盘状态：{error}"

    try:
        rotation = build_sector_rotation(force=force)
        sectors = _sector_block(rotation)
        trade_date = str(rotation.get("trade_date") or trade_date)
    except Exception as error:  # noqa: BLE001
        errors["sectors"] = f"行业轮动：{error}"

    try:
        screen = screen_candidates(force=force)
        candidates = _candidate_block(screen)
    except Exception as error:  # noqa: BLE001
        errors["candidates"] = f"候选股筛选：{error}"

    snapshot = {
        "trade_date": trade_date or now.strftime("%Y-%m-%d"),
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market": market,
        "sectors": sectors,
        "candidates": candidates,
        "strategy": _strategy_block(strategy),
        "errors": errors,
        "notes": [
            "存档只保存规则引擎的判断，回答里的文字结论不参与复盘",
            "收益按收盘价计算，成本按往返 "
            f"{ROUND_TRIP_COST_PCT}% 估算；未考虑停牌与涨跌停无法成交",
        ],
    }
    if not market and not candidates and not sectors:
        snapshot["notes"].append("全部数据源都失败，这份快照不保存")
    return snapshot


def save_daily_judgment(strategy=None, force: bool = False) -> dict:
    """生成并保存当天判断；没有取到任何数据时返回 ``saved=False``。"""
    snapshot = build_snapshot(strategy=strategy, force=force)
    if not (snapshot.get("market") or snapshot.get("sectors")
            or snapshot.get("candidates")):
        return {**snapshot, "saved": False, "store": None}
    stored = save_report(snapshot)
    return {**snapshot, "saved": True, "store": stored}


# ---------------------------------------------------------------------------
# 复盘
# ---------------------------------------------------------------------------
def _sector_entries(report: dict) -> list[tuple[str, bool, str]]:
    entries: list[tuple[str, bool, str]] = []
    sectors = report.get("sectors") or {}
    for item in sectors.get("leaders") or []:
        if item.get("name"):
            entries.append((item["name"], True, "领涨"))
    for item in sectors.get("weak") or []:
        if item.get("name"):
            entries.append((item["name"], False, "弱势"))
    return entries


def build_review(
    limit: int = MAX_REPORTS, horizon: int = HORIZON, force: bool = False
) -> dict:
    """读取存档，逐日评估市场、行业与候选股判断，汇总命中率和失误原因。"""
    now = datetime.now()
    horizon = max(1, min(int(horizon), 20))
    key = f"{now.strftime('%Y-%m-%d')}:{limit}:{horizon}"
    cached_at = _cache.get("at")
    if (
        not force
        and _cache.get("key") == key
        and _cache.get("data")
        and cached_at
        and (now - cached_at).total_seconds() < CACHE_SECONDS
    ):
        return _cache["data"]

    reports = load_reports(limit=limit)
    base = {
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "horizon": horizon,
        "benchmark": BENCHMARK_NAME,
        "reports": [],
        "summary": None,
        "errors": {},
        "notes": [],
        "missing": [],
    }
    if not reports:
        base["notes"].append(
            "还没有保存过每日判断；先调用 save_daily_judgment 存档，之后才能复盘"
        )
        _cache.update({"at": now, "key": key, "data": base})
        return base

    earliest = min(report["trade_date"] for report in reports)
    start_dt = datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=20)
    start = start_dt.strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")

    errors: dict[str, str] = {}
    try:
        benchmark = _benchmark_history(start, end)
        if not benchmark["dates"]:
            errors["benchmark"] = "基准日线为空"
    except Exception as error:  # noqa: BLE001
        benchmark = {"dates": [], "closes": []}
        errors["benchmark"] = f"基准日线：{error}"

    codes = sorted({
        item.get("code") for report in reports
        for item in report.get("candidates") or [] if item.get("code")
    })
    names = sorted({
        entry[0] for report in reports for entry in _sector_entries(report)
    })
    stock_rows, stock_errors = _fetch(
        codes, lambda code: _stock_history(code, start, end)
    )
    sector_rows, sector_errors = _fetch(
        names, lambda name: _sector_history(name, start, end)
    )
    if stock_errors:
        sample = "；".join(
            f"{code}：{reason}" for code, reason in list(stock_errors.items())[:3]
        )
        errors["stocks"] = f"个股日线 {len(stock_errors)} 只失败，例如 {sample}"
    if sector_errors:
        sample = "；".join(
            f"{name}：{reason}" for name, reason in list(sector_errors.items())[:3]
        )
        errors["sectors"] = f"行业日线 {len(sector_errors)} 个失败，例如 {sample}"

    rows: list[dict] = []
    for report in reports:
        trade_date = report["trade_date"]
        market = evaluate_market(
            report.get("market") or {}, benchmark, trade_date, horizon
        )
        sectors = [
            evaluate_sector(
                name, sector_rows.get(name) or [], benchmark, trade_date,
                horizon, expect_positive=positive,
            )
            for name, positive, _ in _sector_entries(report)
        ]
        candidates = [
            evaluate_candidate(
                item, stock_rows.get(item.get("code")) or [], benchmark,
                trade_date, horizon,
            )
            for item in report.get("candidates") or []
        ]
        row = {
            "trade_date": trade_date,
            "saved_at": report.get("saved_at"),
            "state": (report.get("market") or {}).get("state"),
            "risk_level": (report.get("market") or {}).get("risk_level"),
            "strategy": report.get("strategy"),
            "market": market,
            "sectors": sectors,
            "candidates": candidates,
        }
        row["complete"] = _is_complete(row)
        row["mistakes"] = _mistakes(row)
        rows.append(row)

    summary = _summary(rows)
    notes = [
        f"只把走完 {horizon} 个交易日的判断计入命中率，其余标为待观察",
        "行业判断以相对沪深300的超额收益为准，领涨看正向超额、弱势看负向超额",
        "候选股按等权估算，未考虑仓位、分批与止损执行差异",
        f"交易成本按往返 {ROUND_TRIP_COST_PCT}% 从超额收益中扣除",
        "样本少时命中率波动很大，不要据此得出方法有效的结论",
    ]
    if not benchmark["dates"]:
        base["missing"].append("基准日线，无法计算超额收益")
    result = {**base, "reports": rows, "summary": summary,
              "errors": errors, "notes": notes}
    _cache.update({"at": now, "key": key, "data": result})
    return result
