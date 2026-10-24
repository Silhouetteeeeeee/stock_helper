"""公告事件：最近发生了什么，回答"有没有该留意的事"。

财报、主营、估值都是存量数据，公告是增量事件。这里取东方财富的公告列表，
按关键词分成"异常"（减持、诉讼、风险警示等）与"关注"（业绩预告、回购、
分红、增持等）两类，只把最近的高信号条目抛出来。

* ``ensure_announcements(code)``   按需取数，本地缓存六小时
* ``build_announcements(code)``    生成摘要：近期公告列表与提示
* ``announcements_from_rows(...)`` 纯函数，由已入库的行生成，便于测试
* ``format_announcements(...)``    渲染成可读文本

``build_announcements`` 会访问网络，界面里请放到后台线程调用。
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

import requests

import stock.utils as su
from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://np-anotice-stock.eastmoney.com/api/security/ann"
DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
SOURCE_LABEL = "东方财富公告"

TABLE = "announcement"

# 公告随时可能发，缓存短一点，切换标的时不至于回回都打接口
DEFAULT_CACHE_HOURS = 6
# 窗口内太多公告没意义，摘要只留最近这些
DEFAULT_DAYS = 90
SUMMARY_ITEMS = 8
PANEL_ITEMS = 2
# 本地只留一年多的记录，够算窗口也够回头看
RETENTION_DAYS = 400
# 接口单页上限就是 100
PAGE_SIZE = 100

# 先判断利空，再判断利好：同一份公告里两类词都有时按利空算
WARNING_RULES = (
    ("减持", ("减持",)),
    ("诉讼处罚", ("诉讼", "仲裁", "处罚", "立案", "问询", "警示函")),
    ("风险警示", ("退市风险", "风险警示", "停牌")),
    ("质押冻结", ("质押", "冻结", "司法拍卖")),
    ("业绩预减", ("预亏", "预减", "业绩下滑")),
)
INFO_RULES = (
    ("业绩预告", ("业绩预告", "业绩快报", "预增", "扭亏")),
    ("回购", ("回购",)),
    ("增持", ("增持",)),
    ("分红", ("权益分派", "利润分配", "分红", "分配方案")),
    ("重大合同", ("重大合同", "中标", "框架协议")),
    ("股权激励", ("股权激励", "员工持股", "股票期权")),
    ("并购重组", ("重大资产重组", "收购", "重组")),
)

LEVEL_LABELS = {"warning": "异常", "info": "关注"}


class AnnouncementError(Exception):
    """公告操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def ensure_announcements(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取某公司的公告。

    返回 ``{"rows": [...], "stale": bool}``；``stale`` 表示网络抓取失败、
    返回的是旧缓存。
    """
    code = normalize_stock_code(code)
    if not code:
        raise AnnouncementError("请输入股票代码")

    cached = _cached_rows(code)
    if not force and cached and _age_hours(cached) <= max_age_hours:
        return {"rows": cached, "stale": False}

    try:
        rows = fetch_announcements(code)
    except AnnouncementError:
        if cached:
            return {"rows": cached, "stale": True}
        raise

    if not rows:
        if cached:
            return {"rows": cached, "stale": True}
        return {"rows": [], "stale": False}

    _save_rows(code, rows)
    return {"rows": _cached_rows(code), "stale": False}


def build_announcements(
    code: str,
    days: int = DEFAULT_DAYS,
    limit: int = SUMMARY_ITEMS,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并生成近期公告摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise AnnouncementError("请输入股票代码")

    data = ensure_announcements(code, max_age_hours=max_age_hours, force=force)
    if not data["rows"]:
        raise AnnouncementError(f"{code}：没有取到公告数据")

    stock = get_stock(code)
    return announcements_from_rows(
        data["rows"],
        code=code,
        name=stock.name if stock is not None else None,
        days=days,
        limit=limit,
        stale=data.get("stale", False),
    )


def announcements_from_rows(
    rows: list[dict],
    code: str | None = None,
    name: str | None = None,
    days: int = DEFAULT_DAYS,
    limit: int = SUMMARY_ITEMS,
    stale: bool = False,
    today: date | None = None,
) -> dict:
    """由已入库的公告行生成摘要（纯函数）。

    只保留 ``days`` 天内的公告；窗口内没有记录时返回可读的占位文本。
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = [
        row for row in rows
        if row.get("notice_date") and row.get("title") and row["notice_date"] >= cutoff
    ]
    rows.sort(key=lambda row: (row["notice_date"], row.get("art_code") or ""), reverse=True)
    if not rows:
        return _empty(code, name, days)

    items = []
    for row in rows:
        title = _clean_title(row["title"])
        tags, level = classify(title, row.get("categories") or "")
        items.append({
            "art_code": row.get("art_code"),
            "date": row["notice_date"],
            "title": title,
            "categories": _split_categories(row.get("categories")),
            "tags": tags,
            "level": level,
            "url": row.get("url"),
        })

    announcements = {"available": True, "code": code or rows[0].get("code"), "name": name, "days": days,
                     "as_of": items[0]["date"], "count": len(items), "items": items, "stale": stale,
                     "sources": {SOURCE_LABEL: items[0]["date"]}, "flags": _flags(items)}
    if stale:
        announcements["flags"].append({
            "level": "info",
            "code": "stale_data",
            "message": "本次抓取失败，公告来自本地缓存，可能不是最新的",
            "detail": {},
        })
    announcements["summary"] = format_announcements(announcements, limit=limit)
    return announcements


def format_announcements(announcements: dict, limit: int = SUMMARY_ITEMS) -> str:
    """渲染成可读文本：标题一行、公告若干条、提示若干条。"""
    if not announcements.get("available"):
        return (
            f"{announcements.get('name') or '该股票'}："
            f"近 {announcements.get('days') or DEFAULT_DAYS} 天没有公告"
        )

    title = (
        f"{announcements['name']}({announcements['code']})"
        if announcements.get("name")
        else announcements.get("code") or ""
    )
    lines = [f"{title} · 近期公告（近 {announcements['days']} 天 {announcements['count']} 条）"]
    lines.extend(format_items(announcements, limit))
    for flag in announcements.get("flags") or []:
        lines.append(f"[{LEVEL_LABELS.get(flag['level'], flag['level'])}] {flag['message']}")
    return "\n".join(lines)


def format_items(announcements: dict, limit: int | None = SUMMARY_ITEMS) -> list[str]:
    """公告明细，每条一行；``limit=None`` 时不截断。

    同名公告只留最新一条——H 股日历、月报表这类会连着发很多天，
    全列出来会把真正有信息量的条目挤掉。
    """
    lines = []
    seen: set[str] = set()
    for item in announcements.get("items") or []:
        if limit is not None and len(lines) >= limit:
            break
        title = item["title"]
        if title in seen:
            continue
        seen.add(title)
        tag = f"[{item['tags'][0]}] " if item.get("tags") else ""
        lines.append(f"- {item['date']} {tag}{title}")
    return lines


# ---------------------------------------------------------------------------
# 取数与分类
# ---------------------------------------------------------------------------
def fetch_announcements(code: str, page_size: int = PAGE_SIZE) -> list[dict]:
    """抓取最近一页公告（接口单页最多 100 条）。"""
    code = normalize_stock_code(code)
    prefix, digits = su.get_stock_prefix(code)
    if prefix not in ("SH", "SZ", "BJ"):
        raise AnnouncementError(f"{code}：无法判断交易所")

    try:
        response = requests.get(
            ENDPOINT,
            params={
                "sr": -1,
                "page_size": min(page_size, PAGE_SIZE),
                "page_index": 1,
                "ann_type": "A",
                "client_source": "web",
                "stock_list": digits,
            },
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            # 本机系统代理会拦掉东方财富的接口，和行情请求一样直连
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except AnnouncementError:
        raise
    except Exception as error:
        raise AnnouncementError(f"获取公告失败：{error}") from error

    data = payload.get("data") or {}
    if not data.get("list"):
        raise AnnouncementError(f"{code}：公告接口没有返回数据")

    fetched_at = _now()
    rows: dict[str, dict] = {}
    for raw in data["list"]:
        art_code = _text(raw.get("art_code"))
        notice_date = _date_text(raw.get("notice_date"))
        title = _text(raw.get("title_ch") or raw.get("title"))
        if not art_code or not notice_date or not title:
            continue
        title = _strip_prefix(title, raw.get("codes") or [])
        categories = [
            str(column.get("column_name")).strip()
            for column in raw.get("columns") or []
            if column.get("column_name")
        ]
        rows[art_code] = {
            "code": code,
            "art_code": art_code,
            "notice_date": notice_date,
            "title": title,
            "categories": "、".join(categories),
            "url": DETAIL_URL.format(code=code, art_code=art_code),
            "fetched_at": fetched_at,
        }
    return list(rows.values())


def classify(title: str, categories: str = "") -> tuple[list[str], str | None]:
    """按标题和栏目名打标签；有利空标签时按"异常"算。

    返回 ``(tags, level)``，``level`` 为 ``"warning"`` / ``"info"`` / ``None``。
    """
    haystack = f"{title} {categories}"
    tags: list[str] = []
    level = None
    for tag, keywords in WARNING_RULES:
        if any(keyword in haystack for keyword in keywords):
            tags.append(tag)
            level = "warning"
    for tag, keywords in INFO_RULES:
        if any(keyword in haystack for keyword in keywords):
            tags.append(tag)
            if level is None:
                level = "info"
    return tags, level


def _flags(items: list[dict]) -> list[dict]:
    """最各取一条：一条利空 + 一条利好，按时间倒序找最新。"""
    flags: list[dict] = []
    for level in ("warning", "info"):
        chosen = next(
            (item for item in items if item.get("level") == level),
            None,
        )
        if chosen is None:
            continue
        tag = chosen["tags"][0]
        flags.append({
            "level": level,
            "code": f"ann_{level}",
            "message": f"{_md(chosen['date'])} {tag}：{_clip(chosen['title'], 40)}",
            "detail": {"art_code": chosen.get("art_code"), "date": chosen["date"]},
        })
    return flags


def _empty(code: str | None, name: str | None, days: int) -> dict:
    return {
        "available": False,
        "code": code,
        "name": name,
        "days": days,
        "as_of": None,
        "count": 0,
        "items": [],
        "flags": [],
        "stale": False,
        "sources": {},
        "summary": f"{name or '该股票'}：近 {days} 天没有公告",
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
            art_code text not null,
            notice_date text not null,
            title text,
            categories text,
            url text,
            fetched_at text,
            primary key (code, art_code)
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
            order by notice_date desc, art_code desc
            """,
            (code,),
        ).fetchall()
    return [dict(row) for row in rows]


def _save_rows(code: str, rows: list[dict]) -> None:
    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(
                f"delete from {TABLE} where code = ? and notice_date < ?",
                (code, cutoff),
            )
            conn.executemany(
                f"""
                insert or replace into {TABLE} (
                    code, art_code, notice_date, title, categories, url, fetched_at
                ) values (
                    :code, :art_code, :notice_date, :title, :categories, :url, :fetched_at
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
def _strip_prefix(title: str, codes: list[dict]) -> str:
    """标题一般以"公司简称:"开头，去掉后省地方。"""
    for item in codes:
        short_name = _text(item.get("short_name"))
        if not short_name:
            continue
        for separator in (":", "："):
            prefix = f"{short_name}{separator}"
            if title.startswith(prefix):
                return title[len(prefix):].strip()
    return title


_COMPANY_PREFIX = re.compile(r"^(?!关于)[^。；：]{2,30}?(?:股份有限公司|有限责任公司)")


def _clean_title(title: str) -> str:
    """去掉标题开头重复的公司全名：``XX股份有限公司关于…`` -> ``关于…``。

    只处理"公司名在句首"的情况，``关于收购某某有限公司…`` 这种不能动。
    """
    cleaned = _COMPANY_PREFIX.sub("", title, count=1).strip()
    return cleaned or title


def _split_categories(value) -> list[str]:
    text = _text(value)
    return [part for part in text.split("、") if part] if text else []


def _md(value: str) -> str:
    """``2026-10-08`` -> ``10-08``。"""
    return value[5:10] if len(value) >= 10 else value


def _clip(text: str, max_width: int) -> str:
    width = 0
    for index, char in enumerate(text):
        width += 2 if ord(char) > 0x2E80 else 1
        if width > max_width:
            return text[:index].rstrip("，、；： ") + "…"
    return text


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
        print("用法: python -m stock.announcements 605499 300750 ...")
        raise SystemExit(1)

    for argument in sys.argv[1:]:
        try:
            print(build_announcements(argument)["summary"])
        except AnnouncementError as error:
            print(f"{argument}: {error}")
        print()
