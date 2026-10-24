"""主营构成：分部收入、占比与同比，回答"钱从哪来"。

财报快照只有合计数，看不出收入来自哪块业务。这里取东方财富 F10 的
业务分析数据（``zygcfx``），按报告期与口径（产品 / 地区 / 行业）整理
成分部列表，并和上年同期对齐算同比。

* ``ensure_segments(code)``   按需取数，本地缓存一天
* ``build_segments(code)``    生成分析用摘要：分部列表、同比、提示
* ``segments_from_rows(...)`` 纯函数，由已入库的行生成，便于测试
* ``format_segments(...)``    渲染成可读文本

口径：金额单位是元；占比与同比都是百分数（72.0 表示 72%）；缺失值保留
为 None，不填 0。``build_segments`` 会访问网络，界面里请放到后台线程调用。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime

import requests

import stock.utils as su
from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"
SOURCE_LABEL = "东方财富 F10 主营构成"

TABLE = "biz_segment"

# 分部构成一个季度才变一次，缓存一天足够，也避免切换标的时反复打接口
DEFAULT_CACHE_HOURS = 24
# 文本里列多少个分部：再往后都是"其他"，参考价值不大
TOP_ITEMS = 5

# --- 提示阈值 -------------------------------------------------------------
CONCENTRATION_PCT = 85.0    # 单一分部占比超过这个值算高度集中
DECLINE_PCT = -10.0         # 主要分部收入同比下滑超过这个比例
FAST_GROWTH_PCT = 30.0      # 非主要分部收入同比增速超过这个比例
FAST_GROWTH_SHARE = 5.0     # 且占比达到这个值才值得提

# 接口用数字区分口径：2 = 按产品/业务，3 = 按地区，1 = 按行业
KIND_LABELS = {"2": "产品", "3": "地区", "1": "行业"}
PREFERRED_KINDS = ("2", "3", "1")

LEVEL_LABELS = {"warning": "异常", "info": "关注"}


class SegmentError(Exception):
    """主营构成操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def ensure_segments(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取某公司的主营构成。

    返回 ``{"rows": [...], "stale": bool}``；``stale`` 表示网络抓取失败、
    返回的是旧缓存。
    """
    code = normalize_stock_code(code)
    if not code:
        raise SegmentError("请输入股票代码")

    cached = _cached_rows(code)
    if not force and cached and _age_hours(cached) <= max_age_hours:
        return {"rows": cached, "stale": False}

    try:
        rows = fetch_segments(code)
    except SegmentError:
        if cached:
            return {"rows": cached, "stale": True}
        raise

    if not rows:
        if cached:
            return {"rows": cached, "stale": True}
        return {"rows": [], "stale": False}

    _save_rows(code, rows)
    return {"rows": _cached_rows(code), "stale": False}


def build_segments(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并生成主营构成摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise SegmentError("请输入股票代码")

    data = ensure_segments(code, max_age_hours=max_age_hours, force=force)
    if not data["rows"]:
        raise SegmentError(f"{code}：没有取到主营构成数据")

    stock = get_stock(code)
    return segments_from_rows(
        data["rows"],
        name=stock.name if stock is not None else None,
        stale=data.get("stale", False),
    )


def segments_from_rows(
    rows: list[dict],
    kind: str | None = None,
    code: str | None = None,
    name: str | None = None,
    stale: bool = False,
) -> dict:
    """由已入库的分部行生成摘要（纯函数）。

    同一天可能有产品 / 地区 / 行业三种口径，优先取产品口径；某口径下
    没有收入数据时依次回退。
    """
    rows = [row for row in rows if row.get("report_date") and row.get("item_name")]
    if not rows:
        return _empty(code, name)

    code = code or rows[0].get("code")
    report_date, kind, latest = _latest_slice(rows, kind)
    if not latest:
        return _empty(code, name)

    total = sum(row["income"] for row in latest if row.get("income") is not None)
    previous_date = _previous_year(report_date)
    previous = {
        row["item_name"]: row
        for row in rows
        if row["report_date"] == previous_date
        and row["kind"] == kind
        and not _is_total(row["item_name"])
        and not _is_sub_item(row["item_name"])
    }
    # 上一期的占比也按同一套口径重算，两期才可比
    previous_total = sum(
        row["income"] for row in previous.values() if row.get("income") is not None
    )

    items = []
    for row in sorted(latest, key=lambda row: row.get("income") or 0, reverse=True):
        income = row.get("income")
        share = income / total * 100.0 if total and income is not None else row.get("share")
        before = previous.get(row["item_name"]) or {}
        before_income = before.get("income")
        before_share = (
            before_income / previous_total * 100.0
            if before_income is not None and previous_total
            else before.get("share")
        )
        yoy = share_delta = None
        if before_income and income is not None:
            yoy = (income - before_income) / before_income * 100.0
        if share is not None and before_share is not None:
            share_delta = share - before_share
        items.append({
            "name": row["item_name"],
            "income": income,
            "share": share,
            "gross_margin": row.get("gross_margin"),
            "yoy": yoy,
            "share_delta": share_delta,
            "is_other": _is_other(row["item_name"]),
        })

    segments = {
        "available": bool(items),
        "code": code,
        "name": name,
        "as_of": report_date,
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind or "", "构成"),
        "total_income": total or None,
        "previous_date": previous_date if previous else None,
        "count": len(items),
        "items": items,
        "stale": stale,
        "sources": {SOURCE_LABEL: report_date},
    }
    segments["flags"] = _flags(segments)
    segments["summary"] = format_segments(segments)
    return segments


def format_segments(segments: dict) -> str:
    """渲染成可读文本：一行标题 + 每个分部一行。"""
    if not segments.get("available"):
        return f"{segments.get('name') or '该股票'}：暂无主营构成数据"

    title = (
        f"{segments['name']}({segments['code']})"
        if segments.get("name")
        else segments.get("code") or ""
    )
    lines = [f"{title} · 主营构成 {segments['as_of']}（按{segments['kind_label']}）"]
    lines.extend(format_items(segments))
    for flag in segments.get("flags") or []:
        lines.append(f"[{LEVEL_LABELS.get(flag['level'], flag['level'])}] {flag['message']}")
    return "\n".join(lines)


def format_items(segments: dict, limit: int | None = TOP_ITEMS) -> list[str]:
    """分部明细，每项一行；``limit=None`` 时不截断。"""
    lines = []
    for item in (segments.get("items") or [])[:limit]:
        part = f"{item['name']} {_yi(item['income'])}"
        if item["share"] is not None:
            part += f"，占比 {item['share']:.1f}%"
        if item["yoy"] is not None:
            part += f"（同比 {item['yoy']:+.1f}%）"
        if item["gross_margin"] is not None:
            part += f"，毛利率 {item['gross_margin']:.1f}%"
        lines.append(part)
    return lines


# ---------------------------------------------------------------------------
# 取数与计算
# ---------------------------------------------------------------------------
def fetch_segments(code: str) -> list[dict]:
    """抓取全部报告期的分部数据（接口一次最多返回 200 行）。"""
    code = normalize_stock_code(code)
    prefix, digits = su.get_stock_prefix(code)
    if prefix not in ("SH", "SZ", "BJ"):
        raise SegmentError(f"{code}：无法判断交易所")

    try:
        response = requests.get(
            ENDPOINT,
            params={"code": f"{digits}.{prefix}"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            # 本机系统代理会拦掉东方财富的接口，这里和行情请求一样直连
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except SegmentError:
        raise
    except Exception as error:
        raise SegmentError(f"获取主营构成失败：{error}") from error

    fetched_at = _now()
    rows = []
    for raw in payload.get("zygcfx") or []:
        report_date = _date_text(raw.get("REPORT_DATE"))
        kind = _text(raw.get("MAINOP_TYPE"))
        name = _text(raw.get("ITEM_NAME"))
        if not report_date or not kind or not name:
            continue
        rows.append({
            "code": code,
            "report_date": report_date,
            "kind": kind,
            "item_name": name,
            "income": _num(raw.get("MAIN_BUSINESS_INCOME")),
            "share": _percent(raw.get("MBI_RATIO")),
            "cost": _num(raw.get("MAIN_BUSINESS_COST")),
            "profit": _num(raw.get("MAIN_BUSINESS_RPOFIT")),
            "gross_margin": _percent(raw.get("GROSS_RPOFIT_RATIO")),
            "rank": _num(raw.get("RANK")),
            "fetched_at": fetched_at,
        })
    return rows


def _latest_slice(
    rows: list[dict], kind: str | None = None
) -> tuple[str | None, str | None, list[dict]]:
    """取最近一个"有收入数据"的报告期与口径。

    指定了 ``kind`` 就优先用它；该口径完全没有数据时再按产品 → 地区 →
    行业的顺序回退。
    """
    dates = sorted({row["report_date"] for row in rows}, reverse=True)
    passes = [[kind]] if kind else []
    passes.append(list(PREFERRED_KINDS))
    for kinds in passes:
        for report_date in dates:
            for candidate in kinds:
                items = [
                    row for row in rows
                    if row["report_date"] == report_date
                    and row["kind"] == candidate
                    and row.get("income") is not None
                    and not _is_total(row["item_name"])
                    and not _is_sub_item(row["item_name"])
                ]
                if items:
                    return report_date, candidate, items
    return None, None, []


def _previous_year(report_date: str) -> str:
    """"2026-06-30" -> "2025-06-30"，只换年份，不动月日。"""
    try:
        year = int(report_date[:4])
    except ValueError:
        return ""
    return f"{year - 1}{report_date[4:]}"


def _is_total(name: str) -> bool:
    return name.strip() in ("合计", "总计")


def _is_sub_item(name: str) -> bool:
    """``其中:铜精矿`` 这类是上一级分部拆出来的子项，钱会重复算，不能进合计。"""
    return name.strip().startswith(("其中:", "其中："))


def _is_other(name: str) -> bool:
    """汇总 / 抵销项：解释总数时有用，但不适合当"主营"的代表。"""
    name = name.strip()
    if _is_total(name) or _is_sub_item(name):
        return True
    if "抵销" in name:
        return True
    return name in (
        "其他", "其他业务", "其他业务收入", "其他主营业务", "其他(补充)", "其他（补充）",
    )


def _share_tail(item: dict) -> str:
    share = item.get("share")
    return f"，占比 {share:.0f}%" if share is not None else ""


def _flags(segments: dict) -> list[dict]:
    flags: list[dict] = []
    main = [item for item in segments["items"] if not item["is_other"]]
    if not main:
        return flags

    top = main[0]
    if top.get("share") is not None and top["share"] >= CONCENTRATION_PCT:
        flags.append({
            "level": "info",
            "code": "segment_concentrated",
            "message": f"收入集中在{top['name']}（{top['share']:.0f}%），看这块业务就够了",
            "detail": {"name": top["name"], "share": top["share"]},
        })

    if top.get("yoy") is not None and top["yoy"] <= DECLINE_PCT:
        flags.append({
            "level": "warning",
            "code": "segment_decline",
            "message": f"{top['name']}收入同比 {top['yoy']:+.1f}%" + _share_tail(top),
            "detail": {"name": top["name"], "yoy": top["yoy"]},
        })

    # 只提示"已经有点规模、还在快速长"的分部，避免新品从零起步也报喜
    growing = [
        item for item in main[1:]
        if item.get("yoy") is not None
        and item["yoy"] >= FAST_GROWTH_PCT
        and (item.get("share") or 0) >= FAST_GROWTH_SHARE
    ]
    if growing:
        item = max(growing, key=lambda item: item["yoy"])
        flags.append({
            "level": "info",
            "code": "segment_growth",
            "message": f"{item['name']}收入同比 {item['yoy']:+.1f}%" + _share_tail(item),
            "detail": {"name": item["name"], "yoy": item["yoy"], "share": item["share"]},
        })

    if segments.get("stale"):
        flags.append({
            "level": "info",
            "code": "stale_data",
            "message": "本次抓取失败，主营构成来自本地缓存，可能不是最新报告期",
            "detail": {},
        })
    return flags


def _empty(code: str | None, name: str | None) -> dict:
    return {
        "available": False,
        "code": code,
        "name": name,
        "kind": None,
        "kind_label": "构成",
        "as_of": None,
        "items": [],
        "flags": [],
        "stale": False,
        "sources": {},
        "summary": f"{name or '该股票'}：暂无主营构成数据",
    }


# ---------------------------------------------------------------------------
# 本地缓存
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        create table if not exists {TABLE} (
            code text not null,
            report_date text not null,
            kind text not null,
            item_name text not null,
            income real,
            share real,
            cost real,
            profit real,
            gross_margin real,
            rank real,
            fetched_at text,
            primary key (code, report_date, kind, item_name)
        )
        """
    )


def _cached_rows(code: str) -> list[dict]:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
        rows = conn.execute(
            f"""
            select * from {TABLE}
            where code = ?
            order by report_date desc, kind, income desc
            """,
            (code,),
        ).fetchall()
    return [dict(row) for row in rows]


def _save_rows(code: str, rows: list[dict]) -> None:
    """整份替换：接口每次返回全部历史，留着旧行只会越积越多。"""
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(f"delete from {TABLE} where code = ?", (code,))
            conn.executemany(
                f"""
                insert or replace into {TABLE} (
                    code, report_date, kind, item_name,
                    income, share, cost, profit, gross_margin, rank, fetched_at
                ) values (
                    :code, :report_date, :kind, :item_name,
                    :income, :share, :cost, :profit, :gross_margin, :rank, :fetched_at
                )
                """,
                rows,
            )


def _age_hours(rows: list[dict]) -> float:
    stamps = [row.get("fetched_at") for row in rows if row.get("fetched_at")]
    if not stamps:
        return float("inf")
    try:
        fetched = datetime.strptime(max(stamps), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return float("inf")
    return (datetime.now() - fetched).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _yi(value) -> str:
    if value is None:
        return "—"
    return f"{value / 1e8:.1f}亿"


def _num(value) -> float | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(value) -> float | None:
    """接口给的占比 / 毛利率是 0~1 的小数，统一换成百分数。"""
    number = _num(value)
    return None if number is None else number * 100.0


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "-" else text


def _date_text(value) -> str | None:
    text = _text(value)
    return text[:10] if text else None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m stock.segments 605499 600036 ...")
        raise SystemExit(1)

    for argument in sys.argv[1:]:
        try:
            print(build_segments(argument)["summary"])
        except SegmentError as error:
            print(f"{argument}: {error}")
        print()
