"""A 股大盘全景：指数、宽度、量能、板块、情绪与快讯。

回答"今天大盘怎么看"不能只看指数涨跌，否则权重股一拉就会误判。
这里把盘面拆成几块互相独立的数据：指数快照、涨跌家数与涨跌停、
两市成交额与量能、指数均线与区间表现、行业板块、连板高度、市场快讯。
任何一段取不到只记在 ``errors`` 里，其他段照常返回。

* ``build_market_overview()``  联网取数并组装
* ``build_overview_from(...)`` 纯函数：喂原始行，便于测试
* ``trend_stats(...)``         由日线算均线、区间涨跌与位置

数据源：
- 新浪财经：指数实时快照、指数日线
- 乐咕乐股：涨跌家数、涨跌停、活跃度
- 同花顺：行业板块涨跌、成交额、净流入、领涨股
- 东方财富：涨停池（连板高度与行业分布）
- 财联社：市场快讯标题
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime

import akshare as ak

INDEX_CODES = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("sh000688", "科创50"),
)
# 日线只取会用于判断趋势的四个宽基，减少网络请求
TREND_CODES = ("sh000001", "sz399001", "sz399006", "sh000300")
TREND_BARS = 80          # 够算 MA60 与 20 日区间
SECTOR_TOP = 8
SECTOR_BOTTOM = 5
NEWS_LIMIT = 10


# ---------------------------------------------------------------------------
# 纯函数
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


def _percent(value) -> float | None:
    """乐咕的活跃度是 ``"48.05%"`` 这样的字符串。"""
    if value is None:
        return None
    return _as_float(str(value).strip().rstrip("%"))


def session_state(clock, today: str | None = None) -> dict:
    """由数据时点判断盘前/盘中/午间/收盘，并给出交易时段进度。

    进度用来把盘中成交额折算成全天节奏；``today`` 与 ``clock`` 的日期
    不同说明拿到的是最近交易日的数据，按收盘处理。
    """
    text = str(clock or "").strip()
    if not text:
        return {"state": "时点未知", "progress": None}
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return {"state": "时点未知", "progress": None}
    if today and parsed.strftime("%Y-%m-%d") != str(today):
        return {"state": "最近交易日", "progress": 1.0}

    minutes = parsed.hour * 60 + parsed.minute
    morning_open, morning_close = 9 * 60 + 30, 11 * 60 + 30
    afternoon_open, afternoon_close = 13 * 60, 15 * 60
    if minutes < morning_open:
        return {"state": "盘前", "progress": 0.0}
    if minutes <= morning_close:
        return {"state": "盘中", "progress": round((minutes - morning_open) / 240, 3)}
    if minutes < afternoon_open:
        return {"state": "午间休市", "progress": 0.5}
    if minutes <= afternoon_close:
        return {
            "state": "盘中",
            "progress": round((120 + minutes - afternoon_open) / 240, 3),
        }
    return {"state": "收盘", "progress": 1.0}


def trend_stats(rows: list[dict], price=None) -> dict | None:
    """由日线算均线与区间表现；均线只用已完成日线，``price`` 是最新价。"""
    bars = [
        row for row in rows
        if _as_float(row.get("close")) is not None
    ]
    bars.sort(key=lambda row: str(row.get("date") or ""))
    closes = [float(row["close"]) for row in bars]
    if not closes:
        return None

    last = closes[-1]
    live = _as_float(price) or last

    def ma(days: int):
        if len(closes) < days:
            return None
        return round(sum(closes[-days:]) / days, 2)

    def change(days: int):
        if len(closes) <= days:
            return None
        base = closes[-1 - days]
        if not base:
            return None
        return round((live / base - 1) * 100, 2)

    ma20 = ma(20)
    ma60 = ma(60)
    window = closes[-20:]
    return {
        "date": str(bars[-1].get("date") or ""),
        "close": round(last, 2),
        "price": round(live, 2),
        "ma5": ma(5),
        "ma10": ma(10),
        "ma20": ma20,
        "ma60": ma60,
        "above_ma20": None if ma20 is None else live > ma20,
        "above_ma60": None if ma60 is None else live > ma60,
        "ma20_distance_pct": (
            None if not ma20 else round((live / ma20 - 1) * 100, 2)
        ),
        "chg_5d_pct": change(5),
        "chg_20d_pct": change(20),
        "high_20d": round(max(window), 2),
        "low_20d": round(min(window), 2),
    }


def _spot_volume_shares(code: str, value) -> float | None:
    """新浪指数快照的成交量：沪市是手，深市是股。"""
    number = _as_float(value)
    if number is None:
        return None
    return number * 100 if str(code).lower().startswith("sh") else number


def _index_snapshot(rows: list[dict]) -> list[dict]:
    by_code = {str(row.get("代码") or ""): row for row in rows}
    items: list[dict] = []
    for code, default_name in INDEX_CODES:
        row = by_code.get(code)
        if row is None:
            continue
        price = _as_float(row.get("最新价"))
        if price is None:
            continue
        amount = _as_float(row.get("成交额"))
        items.append({
            "code": code,
            "name": str(row.get("名称") or default_name),
            "price": round(price, 2),
            "chg": _round(_as_float(row.get("涨跌额"))),
            "chg_pct": _round(_as_float(row.get("涨跌幅"))),
            "open": _round(_as_float(row.get("今开"))),
            "high": _round(_as_float(row.get("最高"))),
            "low": _round(_as_float(row.get("最低"))),
            "prev_close": _round(_as_float(row.get("昨收"))),
            "amount_yi": round(amount / 1e8, 1) if amount is not None else None,
        })
    return items


def _turnover(index_rows: list[dict], trend_rows: dict, session: dict) -> dict | None:
    by_code = {str(row.get("代码") or ""): row for row in index_rows}
    sh_row = by_code.get("sh000001")
    sz_row = by_code.get("sz399001")
    if sh_row is None and sz_row is None:
        return None

    sh_amount = _as_float((sh_row or {}).get("成交额"))
    sz_amount = _as_float((sz_row or {}).get("成交额"))
    total = sum(value for value in (sh_amount, sz_amount) if value is not None)

    today_volume = 0.0
    have_today = False
    for code, row in (("sh000001", sh_row), ("sz399001", sz_row)):
        if row is None:
            continue
        shares = _spot_volume_shares(code, row.get("成交量"))
        if shares is not None:
            today_volume += shares
            have_today = True

    averages: list[float] = []
    for code in ("sh000001", "sz399001"):
        volumes = [
            _as_float(row.get("volume"))
            for row in (trend_rows.get(code) or [])[-20:]
        ]
        volumes = [value for value in volumes if value is not None]
        if volumes:
            averages.append(sum(volumes) / len(volumes))
    average = sum(averages) if len(averages) == 2 else None

    progress = session.get("progress")
    ratio = None
    if have_today and average and progress and progress >= 0.1:
        ratio = round(today_volume / (average * progress), 2)

    return {
        "sh_yi": round(sh_amount / 1e8, 1) if sh_amount is not None else None,
        "sz_yi": round(sz_amount / 1e8, 1) if sz_amount is not None else None,
        "total_yi": round(total / 1e8, 1) if total else None,
        "today_shares_yi": round(today_volume / 1e8, 1) if have_today else None,
        "avg20_shares_yi": round(average / 1e8, 1) if average else None,
        "pace_ratio": ratio,
        "progress": progress,
        "note": "pace_ratio 是按盘中成交速度折算全天的估算值",
    }


def _breadth(rows: list[dict]) -> dict | None:
    values = {
        str(row.get("item") or "").strip(): row.get("value")
        for row in rows
    }
    up = _as_float(values.get("上涨"))
    down = _as_float(values.get("下跌"))
    if up is None and down is None:
        return None
    flat = _as_float(values.get("平盘")) or 0
    total = up + down + flat if up is not None and down is not None else None
    return {
        "up": _round(up, 0),
        "down": _round(down, 0),
        "flat": _round(flat, 0),
        "limit_up": _round(_as_float(values.get("涨停")), 0),
        "limit_down": _round(_as_float(values.get("跌停")), 0),
        "real_limit_up": _round(_as_float(values.get("真实涨停")), 0),
        "real_limit_down": _round(_as_float(values.get("真实跌停")), 0),
        "suspended": _round(_as_float(values.get("停牌")), 0),
        "activity_pct": _round(_percent(values.get("活跃度"))),
        "up_ratio_pct": (
            round(up / total * 100, 1) if total and up is not None else None
        ),
    }


def _sectors(rows: list[dict]) -> dict | None:
    items: list[dict] = []
    for row in rows:
        change = _as_float(row.get("涨跌幅"))
        if change is None:
            continue
        amount = _as_float(row.get("总成交额"))
        inflow = _as_float(row.get("净流入"))
        items.append({
            "name": str(row.get("板块") or "").strip(),
            "chg_pct": round(change, 2),
            "amount_yi": round(amount, 1) if amount is not None else None,
            "inflow_yi": round(inflow, 2) if inflow is not None else None,
            "up": _round(_as_float(row.get("上涨家数")), 0),
            "down": _round(_as_float(row.get("下跌家数")), 0),
            "leader": str(row.get("领涨股") or "").strip() or None,
            "leader_chg_pct": _round(_as_float(row.get("领涨股-涨跌幅"))),
        })
    if not items:
        return None
    items.sort(key=lambda item: item["chg_pct"], reverse=True)
    top = items[:SECTOR_TOP]
    rest = items[SECTOR_TOP:]
    bottom = list(reversed(rest[-SECTOR_BOTTOM:]))
    return {
        "top": top,
        "bottom": bottom,
        "up_count": sum(1 for item in items if item["chg_pct"] > 0),
        "down_count": sum(1 for item in items if item["chg_pct"] < 0),
        "total": len(items),
    }


def _limit_up(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    streaks: Counter[int] = Counter()
    industries: Counter[str] = Counter()
    for row in rows:
        boards = _as_float(row.get("连板数"))
        streaks[int(boards) if boards is not None else 1] += 1
        industry = str(row.get("所属行业") or "").strip()
        if industry:
            industries[industry] += 1
    if not streaks:
        return None
    return {
        "count": len(rows),
        "max_streak": max(streaks),
        "streaks": [
            {"boards": boards, "count": count}
            for boards, count in sorted(streaks.items(), reverse=True)
        ],
        "top_industries": [
            {"name": name, "count": count}
            for name, count in industries.most_common(5)
        ],
    }


def _news(rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        title = str(row.get("标题") or "").strip()
        if not title:
            continue
        date = str(row.get("发布日期") or "").strip()
        clock = str(row.get("发布时间") or "").strip()
        items.append({"time": f"{date} {clock}".strip(), "title": title})
        if len(items) >= NEWS_LIMIT:
            break
    return items


def build_overview_from(
    index_rows=(),
    breadth_rows=(),
    sector_rows=(),
    trend_rows: dict[str, list[dict]] | None = None,
    limit_rows=(),
    news_rows=(),
    as_of: str | None = None,
    today: str | None = None,
    errors: dict | None = None,
) -> dict:
    """把各数据源的原始行组装成大盘全景；纯函数，不联网。"""
    trend_rows = trend_rows or {}
    session = session_state(as_of, today)
    indices = _index_snapshot(list(index_rows))
    names = {item["code"]: item["name"] for item in indices}
    prices = {item["code"]: item["price"] for item in indices}
    trend: list[dict] = []
    for code, default_name in INDEX_CODES:
        stats = trend_stats(trend_rows.get(code) or [], prices.get(code))
        if stats is None:
            continue
        trend.append({"code": code, "name": names.get(code, default_name), **stats})
    return {
        "as_of": as_of,
        "session": session,
        "indices": indices,
        "turnover": _turnover(list(index_rows), trend_rows, session),
        "breadth": _breadth(list(breadth_rows)),
        "trend": trend,
        "sectors": _sectors(list(sector_rows)),
        "limit_up": _limit_up(list(limit_rows)),
        "news": _news(list(news_rows)),
        "errors": dict(errors or {}),
    }


# ---------------------------------------------------------------------------
# 联网取数
# ---------------------------------------------------------------------------
def _legu_clock(rows: list[dict]) -> str | None:
    for row in rows:
        if str(row.get("item") or "").strip() == "统计日期":
            value = str(row.get("value") or "").strip()
            return value or None
    return None


def build_market_overview() -> dict:
    """取一次盘面数据；各段独立失败，返回里带 ``errors``。"""
    now = datetime.now()
    errors: dict[str, str] = {}
    index_rows: list[dict] = []
    breadth_rows: list[dict] = []
    sector_rows: list[dict] = []
    trend_rows: dict[str, list[dict]] = {}
    limit_rows: list[dict] = []
    news_rows: list[dict] = []

    try:
        breadth_rows = _records(ak.stock_market_activity_legu())
    except Exception as error:  # noqa: BLE001 - 一段失败不拖垮整个盘面
        errors["breadth"] = f"市场宽度：{error}"

    as_of = _legu_clock(breadth_rows) or now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        index_rows = _records(ak.stock_zh_index_spot_sina())
    except Exception as error:  # noqa: BLE001
        errors["indices"] = f"指数行情：{error}"

    trend_errors: list[str] = []
    for code in TREND_CODES:
        try:
            frame = ak.stock_zh_index_daily(symbol=code)
            trend_rows[code] = _records(frame.tail(TREND_BARS))
        except Exception as error:  # noqa: BLE001
            trend_errors.append(f"{code}：{error}")
    if trend_errors:
        errors["trend"] = "指数日线：" + "；".join(trend_errors)

    try:
        sector_rows = _records(ak.stock_board_industry_summary_ths())
    except Exception as error:  # noqa: BLE001
        errors["sectors"] = f"行业板块：{error}"

    trade_date = str(as_of)[:10].replace("-", "")
    if len(trade_date) == 8:
        try:
            limit_rows = _records(ak.stock_zt_pool_em(date=trade_date))
        except Exception as error:  # noqa: BLE001
            errors["limit_up"] = f"涨停池：{error}"

    try:
        news_rows = _records(ak.stock_info_global_cls())
    except Exception as error:  # noqa: BLE001
        errors["news"] = f"市场快讯：{error}"

    return build_overview_from(
        index_rows=index_rows,
        breadth_rows=breadth_rows,
        sector_rows=sector_rows,
        trend_rows=trend_rows,
        limit_rows=limit_rows,
        news_rows=news_rows,
        as_of=as_of,
        today=now.strftime("%Y-%m-%d"),
        errors=errors,
    )
