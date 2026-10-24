"""估值数据服务：PE/PB 历史分位与同行业估值对比。

财报快照回答"公司赚了多少"，这里回答"市场给了多少钱"。

数据源是东方财富数据中心的两张表（`datacenter-web.eastmoney.com`）：

* ``RPT_VALUEANALYSIS_DET`` 个股逐日估值：PE(TTM)、PB(MRQ)、总市值、收盘价、
  所属行业。一次请求取回全部历史（2018 年至今，2000 多个交易日）。
* 同一张表按"行业板块 + 交易日"过滤，可以拿到整个行业所有公司当天的估值，
  用于同业对比。

对外三个函数：

* ``ensure_valuation(code)``  按需取数，本地缓存若干小时
* ``build_valuation(code)``   生成分析用摘要：分位、同业排名、提示
* ``format_valuation(...)``   渲染成一段可读文本

口径：PE/PB 是倍数，分位是 0~100 的百分数（60 表示比历史 60% 的交易日贵）；
缺失值一律保留为 None，不填 0。``build_valuation`` 会访问网络，
界面里请放到后台线程调用。
"""

from __future__ import annotations

import sqlite3
import statistics
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Iterable

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SOURCE_LABEL = "东方财富 估值分析"

HISTORY_TABLE = "val_history"
PEER_TABLE = "val_peer"

# 分位默认看最近 5 年；估值数据从 2018 年开始，更长的窗口留给调用方显式指定
DEFAULT_YEARS = 5
# 估值每天变一次，缓存几小时就够，避免界面反复刷新时重复打接口
DEFAULT_CACHE_HOURS = 6

# --- 提示阈值 -------------------------------------------------------------
HIGH_PERCENTILE = 80.0    # PE/PB 处于历史 80% 分位以上算贵
LOW_PERCENTILE = 20.0     # 20% 分位以下算便宜
PEER_GAP_PCT = 50.0       # 相对行业中值的溢价/折价超过这个比例才提示
MIN_SAMPLES = 250         # 少于这个样本数（约一年）的分位参考意义有限
PEER_LIMIT = 8            # 文本里最多列几家同业

LEVEL_LABELS = {"warning": "异常", "info": "关注"}


class ValuationError(Exception):
    """估值数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def ensure_valuation(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取某公司的估值历史与同业快照。

    返回 ``{"history": [...], "peers": [...], "stale": bool, "peers_error": str|None}``。
    历史按交易日升序；``stale`` 表示网络抓取失败、返回的是旧缓存。
    同业数据抓不到时只影响 ``peers``，不影响个股估值本身。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ValuationError("请输入股票代码")

    history, stale = _ensure_history(code, max_age_hours, force)
    if not history:
        raise ValuationError(f"{code}：没有取到估值数据")

    latest = history[-1]
    peers, peers_error = [], None
    board_code = latest.get("board_code")
    if board_code:
        try:
            peers = _ensure_peers(board_code, latest["trade_date"], max_age_hours, force)
        except ValuationError as error:
            # 同业对比是锦上添花，取不到就降级到本地缓存
            peers, peers_error = _cached_peers(board_code, latest["trade_date"]), str(error)

    return {
        "history": history,
        "peers": peers,
        "stale": stale,
        "peers_error": peers_error,
    }


def build_valuation(
    code: str,
    years: int = DEFAULT_YEARS,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并生成估值摘要（分位 + 同业对比 + 提示）。"""
    code = normalize_stock_code(code)
    if not code:
        raise ValuationError("请输入股票代码")

    data = ensure_valuation(code, max_age_hours=max_age_hours, force=force)
    stock = get_stock(code)
    return valuation_from_rows(
        data["history"],
        peers=data["peers"],
        years=years,
        name=stock.name if stock is not None else None,
        stale=data.get("stale", False),
        peers_error=data.get("peers_error"),
    )


def valuation_from_rows(
    history: list[dict],
    peers: Iterable[dict] = (),
    years: int = DEFAULT_YEARS,
    name: str | None = None,
    stale: bool = False,
    peers_error: str | None = None,
) -> dict:
    """由逐日估值行生成摘要（纯函数，不碰数据库和网络）。"""
    ordered = sorted(history, key=lambda row: row["trade_date"])
    if not ordered:
        return {
            "available": False,
            "code": None,
            "name": name,
            "flags": [],
            "summary": f"{name or '该股票'}：暂无估值数据",
        }

    latest = ordered[-1]
    window = _window(ordered, latest["trade_date"], years)
    pe_series = [row["pe_ttm"] for row in window if _is_positive(row.get("pe_ttm"))]
    pb_series = [row["pb_mrq"] for row in window if _is_positive(row.get("pb_mrq"))]

    valuation = {
        "available": True,
        "code": latest.get("code"),
        "name": name or latest.get("name"),
        "as_of": latest["trade_date"],
        "fetched_at": latest.get("fetched_at"),
        "price": {
            "close": latest.get("close"),
            "market_cap": latest.get("total_mv"),
        },
        "valuation": {
            "pe_ttm": _stats(pe_series, latest.get("pe_ttm"), years),
            "pb_mrq": _stats(pb_series, latest.get("pb_mrq"), years),
        },
        "industry": _industry(ordered, list(peers), latest),
        "stale": stale,
        "peers_error": peers_error,
        "sources": {"valuation": SOURCE_LABEL},
        "flags": [],
    }
    valuation["flags"] = _flags(valuation)
    valuation["summary"] = format_valuation(valuation)
    return valuation


def format_valuation(valuation: dict) -> str:
    """把估值摘要渲染成一段可读文本。"""
    if not valuation.get("available"):
        return f"{valuation.get('name') or '该股票'}：暂无估值数据"

    title = (
        f"{valuation['name']}({valuation['code']})"
        if valuation.get("name")
        else valuation["code"]
    )
    price = valuation["price"]
    head = f"{title} · {valuation['as_of']}"
    if price.get("close") is not None:
        head += f" 收盘 {price['close']:.2f} 元"
    if price.get("market_cap") is not None:
        head += f"，总市值 {_yi(price['market_cap'])}"
    lines = [head]

    window_label = f"近 {valuation['valuation']['pe_ttm']['years']} 年"
    for field, label in (("pe_ttm", "PE(TTM)"), ("pb_mrq", "PB(MRQ)")):
        stat = valuation["valuation"][field]
        line = _stat_line(label, stat, window_label)
        if line:
            lines.append(line)

    industry = valuation.get("industry") or {}
    if industry.get("count"):
        lines.append(_industry_line(industry))
        if industry.get("peers"):
            lines.append(
                "同业（按总市值）："
                + "  ".join(_peer_text(peer) for peer in industry["peers"])
            )

    if valuation.get("flags"):
        lines.append("")
        lines.append("提示：")
        for flag in valuation["flags"]:
            lines.append(f"- [{LEVEL_LABELS.get(flag['level'], flag['level'])}] {flag['message']}")

    lines.append("")
    source = f"数据来源：{valuation['sources']['valuation']}"
    if valuation.get("fetched_at"):
        source += f"，抓取于 {valuation['fetched_at']}"
    if valuation.get("stale"):
        source += "（本次抓取失败，使用本地缓存）"
    lines.append(source)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 分位与同业
# ---------------------------------------------------------------------------
def _stats(values: list[float], current, years: int) -> dict:
    """一个估值倍数的当前位置与历史分布。"""
    stats = {
        "value": current,
        "years": years,
        "samples": len(values),
        "min": None,
        "median": None,
        "max": None,
        "percentile": None,
    }
    if not values:
        return stats

    stats.update({
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    })
    # 亏损公司的 PE 是负数，算分位没有意义，交给提示去说明
    if _is_positive(current):
        stats["percentile"] = sum(1 for value in values if value <= current) / len(values) * 100
    return stats


def _industry(history: list[dict], peers: list[dict], latest: dict) -> dict:
    """行业名称、家数、中值与本公司在行业里的位置。"""
    valid = [peer for peer in peers if _is_positive(peer.get("pe_ttm"))]
    pe_values = [peer["pe_ttm"] for peer in valid]
    pb_values = [peer["pb_mrq"] for peer in peers if _is_positive(peer.get("pb_mrq"))]

    code = latest.get("code")
    own_pe = latest.get("pe_ttm")
    rank = None
    if _is_positive(own_pe) and pe_values:
        # 排名只在盈利公司之间比，亏损公司的 PE 没有大小意义
        rank = sum(1 for value in pe_values if value < own_pe) + 1

    median_pe = statistics.median(pe_values) if pe_values else None
    premium = None
    if _is_positive(own_pe) and median_pe:
        premium = (own_pe - median_pe) / median_pe * 100.0

    ordered = sorted(
        peers,
        key=lambda peer: peer.get("total_mv") or 0,
        reverse=True,
    )
    return {
        "board": latest.get("board_name"),
        "board_code": latest.get("board_code"),
        "count": len(peers),
        "median_pe": median_pe,
        "median_pb": statistics.median(pb_values) if pb_values else None,
        "rank_pe": rank,
        "rank_total": len(pe_values),
        "premium_pe": premium,
        "peers": [
            {**peer, "is_self": peer.get("code") == code}
            for peer in ordered[:PEER_LIMIT]
        ],
    }


def _flags(valuation: dict) -> list[dict]:
    flags: list[dict] = []
    pe, pb = valuation["valuation"]["pe_ttm"], valuation["valuation"]["pb_mrq"]

    if pe["value"] is not None and not _is_positive(pe["value"]):
        flags.append({
            "level": "warning",
            "code": "pe_negative",
            "message": f"PE(TTM) 为 {pe['value']:.2f}，公司处于亏损，市盈率与分位不适用",
            "detail": {"pe_ttm": pe["value"]},
        })
    else:
        percentile = pe["percentile"]
        if percentile is not None and percentile >= HIGH_PERCENTILE:
            flags.append({
                "level": "info",
                "code": "pe_high",
                "message": f"PE(TTM) {pe['value']:.2f}，处于近 {pe['years']} 年 {percentile:.0f}% 分位",
                "detail": {"percentile": percentile},
            })
        elif percentile is not None and percentile <= LOW_PERCENTILE:
            flags.append({
                "level": "info",
                "code": "pe_low",
                "message": f"PE(TTM) {pe['value']:.2f}，处于近 {pe['years']} 年 {percentile:.0f}% 分位",
                "detail": {"percentile": percentile},
            })

    percentile = pb["percentile"]
    if percentile is not None and percentile >= HIGH_PERCENTILE:
        flags.append({
            "level": "info",
            "code": "pb_high",
            "message": f"PB(MRQ) {pb['value']:.2f}，处于近 {pb['years']} 年 {percentile:.0f}% 分位",
            "detail": {"percentile": percentile},
        })
    elif percentile is not None and percentile <= LOW_PERCENTILE:
        flags.append({
            "level": "info",
            "code": "pb_low",
            "message": f"PB(MRQ) {pb['value']:.2f}，处于近 {pb['years']} 年 {percentile:.0f}% 分位",
            "detail": {"percentile": percentile},
        })

    # 样本太少时（次新股），分位数字容易误导
    if 0 < pe["samples"] < MIN_SAMPLES:
        flags.append({
            "level": "info",
            "code": "short_history",
            "message": f"可比的估值历史只有 {pe['samples']} 个交易日，分位参考意义有限",
            "detail": {"samples": pe["samples"]},
        })

    industry = valuation.get("industry") or {}
    premium = industry.get("premium_pe")
    if premium is not None and abs(premium) >= PEER_GAP_PCT:
        cheaper = premium < 0
        flags.append({
            "level": "info",
            "code": "discount_to_peers" if cheaper else "premium_to_peers",
            "message": (
                f"PE(TTM) {valuation['valuation']['pe_ttm']['value']:.2f}，"
                f"较行业中值 {industry['median_pe']:.2f} "
                + (f"折价 {abs(premium):.0f}%" if cheaper else f"溢价 {premium:.0f}%")
                + f"（{industry['rank_pe']}/{industry['rank_total']}）"
            ),
            "detail": {
                "premium": premium,
                "median_pe": industry["median_pe"],
                "rank": industry["rank_pe"],
                "count": industry["rank_total"],
            },
        })

    if valuation.get("stale"):
        flags.append({
            "level": "info",
            "code": "stale_data",
            "message": "本次抓取失败，估值数据来自本地缓存，可能不是最新收盘",
            "detail": {},
        })

    return flags


# ---------------------------------------------------------------------------
# 取数与缓存
# ---------------------------------------------------------------------------
def _ensure_history(code: str, max_age_hours: float, force: bool) -> tuple[list[dict], bool]:
    cached = _cached_history(code)
    if not force and cached and _age_hours(cached) <= max_age_hours:
        return cached, False

    try:
        rows = fetch_history(code)
    except ValuationError:
        if cached:
            return cached, True
        raise
    if not rows:
        if cached:
            return cached, True
        return [], False

    _save_history(code, rows)
    return _cached_history(code), False


def _ensure_peers(board_code: str, trade_date: str, max_age_hours: float, force: bool) -> list[dict]:
    cached = _cached_peers(board_code, trade_date)
    if not force and cached and _age_hours(cached) <= max_age_hours:
        return cached

    rows = fetch_peers(board_code, trade_date)
    if not rows:
        return cached
    _save_peers(board_code, trade_date, rows)
    return _cached_peers(board_code, trade_date)


def fetch_history(code: str, page_size: int = 3000) -> list[dict]:
    """抓取个股逐日估值（按交易日倒序返回，调用方自行排序）。"""
    fetched_at = _now()
    rows = []
    for raw in _request(
        filter_expr=f'(SECURITY_CODE="{code}")',
        sort="-1:TRADE_DATE",
        page_size=page_size,
    ):
        trade_date = _date_text(raw.get("TRADE_DATE"))
        if not trade_date:
            continue
        rows.append({
            "code": code,
            "trade_date": trade_date,
            "name": _text(raw.get("SECURITY_NAME_ABBR")),
            "pe_ttm": _num(raw.get("PE_TTM")),
            "pb_mrq": _num(raw.get("PB_MRQ")),
            "total_mv": _num(raw.get("TOTAL_MARKET_CAP")),
            "close": _num(raw.get("CLOSE_PRICE")),
            "board_code": _text(raw.get("BOARD_CODE")),
            "board_name": _text(raw.get("BOARD_NAME")),
            "fetched_at": fetched_at,
        })
    return rows


def fetch_peers(board_code: str, trade_date: str) -> list[dict]:
    """抓取某个行业在指定交易日的全部公司估值。"""
    fetched_at = _now()
    rows = []
    for raw in _request(
        filter_expr=f"(BOARD_CODE=\"{board_code}\")(TRADE_DATE='{trade_date}')",
        sort="-1:TOTAL_MARKET_CAP",
        page_size=500,
    ):
        code = _text(raw.get("SECURITY_CODE"))
        if not code:
            continue
        rows.append({
            "board_code": board_code,
            "trade_date": trade_date,
            "code": code,
            "name": _text(raw.get("SECURITY_NAME_ABBR")),
            "pe_ttm": _num(raw.get("PE_TTM")),
            "pb_mrq": _num(raw.get("PB_MRQ")),
            "total_mv": _num(raw.get("TOTAL_MARKET_CAP")),
            "fetched_at": fetched_at,
        })
    return rows


def _request(filter_expr: str, sort: str, page_size: int) -> list[dict]:
    sort_type, sort_column = sort.split(":", 1)
    params = {
        "reportName": "RPT_VALUEANALYSIS_DET",
        "columns": "ALL",
        "quoteColumns": "",
        "filter": filter_expr,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortTypes": sort_type,
        "sortColumns": sort_column,
        "source": "WEB",
        "client": "WEB",
    }
    try:
        response = requests.get(
            ENDPOINT,
            params=params,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            # 本机系统代理会拦掉东方财富的接口，这里和行情请求一样直连
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        raise ValuationError(f"获取估值数据失败：{error}") from error

    if not payload.get("success"):
        raise ValuationError(f"估值接口返回错误：{payload.get('message') or '未知错误'}")
    result = payload.get("result") or {}
    return result.get("data") or []


# ---------------------------------------------------------------------------
# 本地缓存
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        create table if not exists {HISTORY_TABLE} (
            code text not null,
            trade_date text not null,
            name text,
            pe_ttm real,
            pb_mrq real,
            total_mv real,
            close real,
            board_code text,
            board_name text,
            fetched_at text,
            primary key (code, trade_date)
        )
        """
    )
    conn.execute(
        f"""
        create table if not exists {PEER_TABLE} (
            board_code text not null,
            trade_date text not null,
            code text not null,
            name text,
            pe_ttm real,
            pb_mrq real,
            total_mv real,
            fetched_at text,
            primary key (board_code, trade_date, code)
        )
        """
    )


def _cached_history(code: str) -> list[dict]:
    with closing(_connect()) as conn:
        with conn:
            _ensure_tables(conn)
        rows = conn.execute(
            f"select * from {HISTORY_TABLE} where code = ? order by trade_date",
            (code,),
        ).fetchall()
    return [dict(row) for row in rows]


def _cached_peers(board_code: str, trade_date: str) -> list[dict]:
    with closing(_connect()) as conn:
        with conn:
            _ensure_tables(conn)
        rows = conn.execute(
            f"""
            select * from {PEER_TABLE}
            where board_code = ? and trade_date = ?
            order by total_mv desc
            """,
            (board_code, trade_date),
        ).fetchall()
    return [dict(row) for row in rows]


def _save_history(code: str, rows: list[dict]) -> None:
    columns = ["code", "trade_date", "name", "pe_ttm", "pb_mrq",
               "total_mv", "close", "board_code", "board_name", "fetched_at"]
    with closing(_connect()) as conn:
        with conn:
            _ensure_tables(conn)
            conn.execute(f"delete from {HISTORY_TABLE} where code = ?", (code,))
            conn.executemany(
                f"""
                insert or replace into {HISTORY_TABLE} ({', '.join(columns)})
                values ({', '.join('?' for _ in columns)})
                """,
                [tuple(row.get(column) for column in columns) for row in rows],
            )


def _save_peers(board_code: str, trade_date: str, rows: list[dict]) -> None:
    columns = ["board_code", "trade_date", "code", "name",
               "pe_ttm", "pb_mrq", "total_mv", "fetched_at"]
    with closing(_connect()) as conn:
        with conn:
            _ensure_tables(conn)
            conn.execute(
                f"delete from {PEER_TABLE} where board_code = ? and trade_date = ?",
                (board_code, trade_date),
            )
            conn.executemany(
                f"""
                insert or replace into {PEER_TABLE} ({', '.join(columns)})
                values ({', '.join('?' for _ in columns)})
                """,
                [tuple(row.get(column) for column in columns) for row in rows],
            )


def _age_hours(rows: list[dict]) -> float:
    """缓存最后一次写入距今多久；没有可用时间戳时当成无限旧。"""
    stamps = [row.get("fetched_at") for row in rows if row.get("fetched_at")]
    if not stamps:
        return float("inf")
    try:
        fetched = datetime.strptime(max(stamps), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return float("inf")
    return (datetime.now() - fetched).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _stat_line(label: str, stat: dict, window_label: str) -> str | None:
    if stat["value"] is None:
        return None
    if stat["percentile"] is None:
        return f"{label} {stat['value']:.2f}（分位不适用）"
    return (
        f"{label} {stat['value']:.2f}，{window_label} {stat['percentile']:.0f}% 分位"
        f"（区间 {stat['min']:.2f} ~ {stat['max']:.2f}，中位 {stat['median']:.2f}）"
    )


def _industry_line(industry: dict) -> str:
    parts = [f"行业 {industry['board'] or '未知'}（{industry['count']} 家）"]
    if industry.get("median_pe") is not None:
        parts.append(f"PE 中值 {industry['median_pe']:.2f}")
    if industry.get("median_pb") is not None:
        parts.append(f"PB 中值 {industry['median_pb']:.2f}")
    if industry.get("rank_pe"):
        parts.append(f"本公司 PE 由低到高第 {industry['rank_pe']}/{industry['rank_total']}")
    return " · ".join(parts)


def _peer_text(peer: dict) -> str:
    name = peer.get("name") or peer.get("code")
    values = [
        f"PE {peer['pe_ttm']:.1f}" if _is_positive(peer.get("pe_ttm")) else "亏损"
    ]
    if _is_positive(peer.get("pb_mrq")):
        values.append(f"PB {peer['pb_mrq']:.2f}")
    text = f"{name} " + "/".join(values)
    return f"[{text}]" if peer.get("is_self") else text


def _yi(value) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.1f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f}万"
    return f"{value:.0f}元"


def _num(value) -> float | None:
    """接口用 "-" 表示没有数据。"""
    if value is None or value == "-" or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    return text


def _date_text(value) -> str | None:
    """接口返回 '2026-10-23 00:00:00'，只保留日期部分。"""
    text = _text(value)
    return text[:10] if text else None


def _is_positive(value) -> bool:
    return value is not None and value > 0


def _window(rows: list[dict], latest_date: str, years: int) -> list[dict]:
    """截取最近 N 年的数据。"""
    try:
        end = date.fromisoformat(latest_date)
    except ValueError:
        return rows
    cutoff = (end - timedelta(days=365 * years)).isoformat()
    return [row for row in rows if row["trade_date"] >= cutoff] or rows[-1:]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m stock.valuation 605499 600036 ...")
        raise SystemExit(1)

    for argument in sys.argv[1:]:
        try:
            print(format_valuation(build_valuation(argument)))
        except ValuationError as error:
            print(f"{argument}: {error}")
        print()
