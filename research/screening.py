"""模块 4 的量化筛选部分：先用程序把五千多只股票收敛到一小组候选。

流程分两步：

1. 用全市场快照做硬过滤：剔除 ST、退市、上市新股、停牌和成交额过小的
   股票，再按板块配额各取一部分，保证候选不是清一色的权重股。创业板、
   北交所不参与（账户没有交易权限）。
2. 只对入选的几十只股票拉日线，算均线位置、20/60 日涨幅、60 日区间位置、
   成交额放大倍数、换手率和总市值，合成量化分。

本模块只回答"哪些股票在量价上值得继续看"，不判断基本面好坏，也不给估值
结论。估值与基本面复核交给助手用 get_finance / get_valuation /
get_announcements 逐只去做；量化筛选只给最终名单逐只补最新 PE/PB 供参考。
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import akshare as ak

from stock.valuation import latest_valuation

HISTORY_DAYS = 140
MAX_WORKERS = 10
VALUATION_WORKERS = 4   # 补估值是逐只联网，别一次打太猛
MIN_BARS = 40          # 少于 40 根日线，均线和区间指标没意义
DEFAULT_LIMIT = 15
MAX_PER_BOARD = 4      # 最终候选里同一板块最多几只，避免清一色银行股
DEFAULT_MIN_AMOUNT_YI = 1.0
DEFAULT_MIN_PRICE = 2.0
CACHE_SECONDS = 900

# 不参与筛选的板块：账户没有创业板、北交所的交易权限
EXCLUDED_BOARDS: tuple[str, ...] = ("创业板", "北交所")

# 按板块配额取成交额靠前的股票，避免候选全是沪深主板的权重股
_ALL_BOARD_QUOTAS: tuple[tuple[str, int], ...] = (
    ("沪市主板", 15),
    ("深市主板", 15),
    ("创业板", 15),
    ("科创板", 10),
    ("北交所", 5),
)
BOARD_QUOTAS: tuple[tuple[str, int], ...] = tuple(
    (name, size) for name, size in _ALL_BOARD_QUOTAS if name not in EXCLUDED_BOARDS
)
SHORTLIST_SIZE = sum(size for _, size in BOARD_QUOTAS)

WEIGHTS = {
    "trend": 0.35,
    "momentum": 0.30,
    "position": 0.20,
    "volume": 0.15,
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


# ---------------------------------------------------------------------------
# 硬过滤
# ---------------------------------------------------------------------------
def board_of(code: str) -> str:
    """按代码前缀给出板块名，用于配额和说明。"""
    code = str(code or "").lower()
    if code.startswith("bj"):
        return "北交所"
    if code.startswith("sh688") or code.startswith("sh689"):
        return "科创板"
    if code.startswith("sz30"):
        return "创业板"
    if code.startswith("sh60"):
        return "沪市主板"
    if code.startswith("sz00"):
        return "深市主板"
    return "其他"


def _is_new_listing(name: str) -> bool:
    """N/C 开头是上市首日或前几日的简写。"""
    text = str(name or "").strip().upper()
    return text.startswith("N") or text.startswith("C")


def exclusion_reason(code: str, name: str, price, amount) -> str | None:
    """返回被剔除的原因；可以通过返回 None。"""
    text = str(name or "")
    if "ST" in text.upper() or "退" in text:
        return "ST/退市"
    if _is_new_listing(text):
        return "上市新股"
    price_value = _as_float(price)
    amount_value = _as_float(amount)
    if not price_value or price_value <= 0:
        return "停牌或无报价"
    if not amount_value or amount_value <= 0:
        return "无成交"
    return None


def shortlist(
    rows: list[dict],
    min_amount_yi: float = DEFAULT_MIN_AMOUNT_YI,
    min_price: float = DEFAULT_MIN_PRICE,
    quotas: tuple[tuple[str, int], ...] = BOARD_QUOTAS,
) -> tuple[list[dict], dict]:
    """按成交额在各自板块里取前几名，返回候选与过滤统计。"""
    stats = {"scanned": len(rows), "excluded": {}, "after_filters": 0}
    buckets: dict[str, list[tuple[float, dict]]] = {name: [] for name, _ in quotas}

    for row in rows:
        code = str(row.get("代码") or "").strip().lower()
        name = str(row.get("名称") or row.get("name") or "").strip()
        if not code or not name:
            continue
        reason = exclusion_reason(code, name, row.get("最新价"), row.get("成交额"))
        if reason:
            stats["excluded"][reason] = stats["excluded"].get(reason, 0) + 1
            continue
        price = _as_float(row.get("最新价"))
        amount = _as_float(row.get("成交额"))
        if price is None or amount is None or price < min_price:
            stats["excluded"]["价格或成交额不合适"] = (
                stats["excluded"].get("价格或成交额不合适", 0) + 1
            )
            continue
        if amount < min_amount_yi * 1e8:
            stats["excluded"]["成交额不足"] = (
                stats["excluded"].get("成交额不足", 0) + 1
            )
            continue
        board = board_of(code)
        if board not in buckets:
            continue
        buckets[board].append((amount, row))
        stats["after_filters"] += 1

    picked: list[dict] = []
    for board, size in quotas:
        items = sorted(buckets.get(board) or [], key=lambda item: item[0], reverse=True)
        picked.extend(row for _, row in items[:size])
    return picked, stats


# ---------------------------------------------------------------------------
# 个股指标
# ---------------------------------------------------------------------------
def stock_stats(row: dict, history: list[dict]) -> dict:
    """把快照和日线合成一只股票的量化指标。"""
    code = str(row.get("代码") or "").strip().lower()
    name = str(row.get("名称") or "").strip()
    price = _as_float(row.get("最新价"))
    chg = _as_float(row.get("涨跌幅"))
    spot_amount = _as_float(row.get("成交额"))

    bars = [
        item for item in history
        if _as_float(item.get("close")) is not None
    ]
    closes = [_as_float(item.get("close")) for item in bars]
    highs = [_as_float(item.get("high")) for item in bars]
    lows = [_as_float(item.get("low")) for item in bars]
    amounts = [_as_float(item.get("amount")) for item in bars]
    last = bars[-1] if bars else {}

    bars_count = len(closes)
    ma20 = _mean(closes[-20:]) if bars_count >= 20 else None
    ma60 = _mean(closes[-60:]) if bars_count >= 60 else None
    latest_close = closes[-1] if closes else price
    reference_price = latest_close or price

    ret_20d = None
    if bars_count >= 21 and closes[-21]:
        ret_20d = (closes[-1] / closes[-21] - 1) * 100
    ret_60d = None
    if bars_count >= 61 and closes[-61]:
        ret_60d = (closes[-1] / closes[-61] - 1) * 100

    window_high = max((item for item in highs[-60:] if item), default=None)
    window_low = min((item for item in lows[-60:] if item), default=None)
    pos_60d = None
    if window_high and window_low and window_high > window_low and reference_price:
        pos_60d = (reference_price - window_low) / (window_high - window_low)
    drawdown = None
    if window_high and reference_price:
        drawdown = (reference_price / window_high - 1) * 100

    average_amount = _mean(amounts[-21:-1]) if len(amounts) >= 21 else None
    amount_ratio = None
    if average_amount and last.get("amount"):
        amount_ratio = _as_float(last["amount"]) / average_amount

    outstanding = _as_float(last.get("outstanding_share"))
    market_cap = None
    if outstanding and reference_price:
        market_cap = reference_price * outstanding / 1e8
    turnover = _as_float(last.get("turnover"))

    return {
        "code": code,
        "name": name,
        "board": board_of(code),
        "price": _round(price),
        "chg_pct": _round(chg),
        "spot_amount_yi": _round(spot_amount / 1e8) if spot_amount else None,
        "bars": bars_count,
        "ma20": _round(ma20),
        "ma60": _round(ma60),
        "above_ma20": (reference_price > ma20) if ma20 and reference_price else None,
        "above_ma60": (reference_price > ma60) if ma60 and reference_price else None,
        "ma20_above_ma60": (ma20 > ma60) if ma20 and ma60 else None,
        "ret_20d": _round(ret_20d),
        "ret_60d": _round(ret_60d),
        "high_60d": _round(window_high),
        "low_60d": _round(window_low),
        "pos_60d": _round(pos_60d),
        "drawdown_from_high": _round(drawdown),
        "amount_ratio": _round(amount_ratio),
        "turnover_pct": _round(turnover * 100) if turnover is not None else None,
        "market_cap_yi": _round(market_cap),
    }


def _component_trend(stats: dict) -> float | None:
    checks = [
        stats.get("above_ma20"),
        stats.get("above_ma60"),
        stats.get("ma20_above_ma60"),
    ]
    known = [item for item in checks if item is not None]
    if not known:
        return None
    return sum(1 for item in known if item) / len(known) * 2 - 1


def _component_momentum(stats: dict) -> float | None:
    parts = []
    if stats.get("ret_20d") is not None:
        parts.append(_clip(stats["ret_20d"] / 20))
    if stats.get("ret_60d") is not None:
        parts.append(_clip(stats["ret_60d"] / 40))
    return sum(parts) / len(parts) if parts else None


def _component_position(stats: dict) -> float | None:
    """60 日区间位置：0.75 附近最好，贴着高点扣分，接近低点不加分。"""
    pos = stats.get("pos_60d")
    if pos is None:
        return None
    if pos >= 0.98:
        return 0.2
    return _clip(1 - abs(pos - 0.75) / 0.75)


def _component_volume(stats: dict) -> float | None:
    ratio = stats.get("amount_ratio")
    return None if ratio is None else _clip((ratio - 1) / 0.5)


def score_stock(stats: dict) -> float | None:
    """合成量化分（-10 到 10）；缺数据的维度不参与，权重重归一。"""
    if stats.get("bars", 0) < MIN_BARS:
        return None
    parts = (
        (_component_trend(stats), WEIGHTS["trend"]),
        (_component_momentum(stats), WEIGHTS["momentum"]),
        (_component_position(stats), WEIGHTS["position"]),
        (_component_volume(stats), WEIGHTS["volume"]),
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
    score = _clip(total / weight) * 10
    # 上市不足 60 日的股票缺中期维度，打分整体打折
    if stats.get("bars", 0) < 60:
        score *= 0.9
    return round(score, 2)


def diversify(items: list[dict], limit: int, per_board: int = MAX_PER_BOARD) -> list[dict]:
    """保留打分顺序，但同一板块最多 ``per_board`` 只。

    宁可名单短一点也不让一个板块占满：候选是用来继续做基本面研究的，
    同一板块的四只银行股信息量远低于四个不同方向。
    """
    picked: list[dict] = []
    used: dict[str, int] = {}
    for item in items:
        board = item.get("board") or "其他"
        if used.get(board, 0) >= per_board:
            continue
        used[board] = used.get(board, 0) + 1
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _reasons(stats: dict) -> list[str]:
    items: list[str] = []
    if stats.get("above_ma20") and stats.get("above_ma60"):
        items.append("站上20日与60日均线")
    elif stats.get("above_ma20"):
        items.append("站上20日均线，60日均线仍未收复")
    elif stats.get("above_ma20") is False:
        items.append("仍在20日均线下方")
    if stats.get("ret_20d") is not None:
        items.append(f"20日{stats['ret_20d']:+.1f}%")
    if stats.get("ret_60d") is not None:
        items.append(f"60日{stats['ret_60d']:+.1f}%")
    if stats.get("drawdown_from_high") is not None:
        items.append(f"距60日高点{stats['drawdown_from_high']:+.1f}%")
    if stats.get("amount_ratio") is not None:
        items.append(f"成交额约为20日均值{stats['amount_ratio']:.1f}倍")
    if stats.get("turnover_pct") is not None:
        items.append(f"换手{stats['turnover_pct']:.2f}%")
    if stats.get("market_cap_yi") is not None:
        items.append(f"市值约{stats['market_cap_yi']:.0f}亿")
    return items


def _risks(stats: dict) -> list[str]:
    items: list[str] = []
    pos = stats.get("pos_60d")
    if pos is not None and pos >= 0.95:
        items.append("贴着60日高点，位置偏高，追高风险")
    if stats.get("above_ma60") is False:
        items.append("仍在60日均线下方，中期趋势未修复")
    ratio = stats.get("amount_ratio")
    if ratio is not None and ratio < 0.7:
        items.append(f"成交额缩到20日均值的{ratio:.1f}倍，关注度下降")
    if stats.get("ret_20d") is not None and stats["ret_20d"] > 35:
        items.append("20日涨幅超过35%，短期涨幅偏大")
    if stats.get("bars", 0) < 60:
        items.append("历史不足60个交易日，均线与区间指标参考性弱")
    if stats.get("market_cap_yi") is not None and stats["market_cap_yi"] < 50:
        items.append("市值不足50亿，流动性与波动风险较高")
    return items


# ---------------------------------------------------------------------------
# 最终名单补估值（候选有几十只，只对入选的这几只联网取最新一行）
# ---------------------------------------------------------------------------
def _fill_valuations(items: list[dict]) -> None:
    """逐只补最新 PE/PB；取不到的保持 None，不影响筛选结果。"""
    if not items:
        return

    def load(item: dict) -> tuple[dict, dict | None]:
        try:
            row = latest_valuation(str(item.get("code") or ""))
        except Exception:  # noqa: BLE001 - 单只取不到估值不拖累名单
            return item, None
        return item, {
            "as_of": row.get("trade_date"),
            "pe_ttm": _round(row.get("pe_ttm")),
            "pb_mrq": _round(row.get("pb_mrq")),
            "total_mv_yi": _round(row.get("total_mv") / 1e8) if row.get("total_mv") else None,
            "stale": bool(row.get("stale")),
        }

    workers = max(1, min(VALUATION_WORKERS, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item, valuation in pool.map(load, items):
            if valuation is not None:
                item["valuation"] = valuation


# ---------------------------------------------------------------------------
# 抓取与汇总
# ---------------------------------------------------------------------------
def _history(code: str) -> list[dict]:
    frame = ak.stock_zh_a_daily(symbol=code)
    return _records(frame.tail(HISTORY_DAYS))


def _fetch_history(codes: list[str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    history: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    def load(code: str) -> tuple[str, list[dict] | None, str | None]:
        try:
            return code, _history(code), None
        except Exception as error:  # noqa: BLE001 - 单只失败不影响其他股票
            return code, None, str(error)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for code, rows, error in pool.map(load, codes):
            if rows is not None:
                history[code] = rows
            else:
                errors[code] = error or "空数据"
    return history, errors


def _public(stats: dict) -> dict:
    keys = (
        "code", "name", "board", "price", "chg_pct", "market_cap_yi",
        "ret_20d", "ret_60d", "pos_60d", "drawdown_from_high",
        "above_ma20", "above_ma60", "ma20", "ma60",
        "amount_ratio", "turnover_pct", "spot_amount_yi",
        "score", "reasons", "risks", "valuation", "bars",
    )
    return {key: stats.get(key) for key in keys}


def screen_candidates(
    limit: int = DEFAULT_LIMIT,
    min_amount_yi: float = DEFAULT_MIN_AMOUNT_YI,
    force: bool = False,
) -> dict:
    """量化筛选：全市场硬过滤 → 板块配额选样 → 日线量化打分。"""
    now = datetime.now()
    key = f"{now.strftime('%Y-%m-%d')}:{limit}:{min_amount_yi}"
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
        rows = _records(ak.stock_zh_a_spot())
    except Exception as error:  # noqa: BLE001
        return {
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
            "candidates": [],
            "universe": None,
            "filters": None,
            "errors": {"spot": f"全市场快照：{error}"},
            "notes": [],
            "missing": [],
        }

    picked, stats = shortlist(rows, min_amount_yi=min_amount_yi)
    codes = [str(row.get("代码") or "").strip().lower() for row in picked]
    history, history_errors = _fetch_history(codes)
    if history_errors:
        sample = "；".join(
            f"{code}：{reason}" for code, reason in list(history_errors.items())[:3]
        )
        errors["history"] = f"日线 {len(history_errors)} 只失败，例如 {sample}"

    candidates: list[dict] = []
    for row in picked:
        code = str(row.get("代码") or "").strip().lower()
        item = stock_stats(row, history.get(code) or [])
        item["score"] = score_stock(item)
        item["reasons"] = _reasons(item)
        item["risks"] = _risks(item)
        item["valuation"] = None
        if item["score"] is None:
            continue
        candidates.append(item)

    candidates.sort(key=lambda item: (item["score"], item.get("spot_amount_yi") or 0), reverse=True)
    final = diversify(candidates, max(1, min(limit, 30)))
    _fill_valuations(final)
    top = [_public(item) for item in final]

    missing_valuation = [item["code"] for item in final if not item.get("valuation")]
    if missing_valuation:
        sample = "、".join(missing_valuation[:3])
        more = " 等" if len(missing_valuation) > 3 else ""
        notes.append(
            f"最终名单里有 {len(missing_valuation)} 只没取到 PE/PB（{sample}{more}），"
            "需要时让助手用 get_valuation 再查"
        )
    missing = [
        "PE/PB 历史分位",
        "营收与利润增速、ROE、现金流",
        "公司公告与新闻事件",
        "所属行业与同业估值",
    ]
    notes.append("量化分只反映量价与位置，不含基本面与估值结论")
    notes.append("候选池按板块配额取成交额靠前的股票，不是全市场涨幅榜")
    notes.append(f"候选池不含{'、'.join(EXCLUDED_BOARDS)}")
    notes.append(f"最终名单每个板块最多 {MAX_PER_BOARD} 只，避免同一类股票占满")

    result = {
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "universe": stats,
        "filters": {
            "min_amount_yi": min_amount_yi,
            "exclude": "ST、退市、上市新股、停牌、无成交",
            "exclude_boards": list(EXCLUDED_BOARDS),
            "boards": {name: size for name, size in BOARD_QUOTAS},
            "shortlisted": len(picked),
        },
        "candidates": top,
        "errors": errors,
        "notes": notes,
        "missing": missing,
    }
    _cache.update({"at": now, "key": key, "data": result})
    return result
