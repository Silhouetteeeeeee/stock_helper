"""机构调研：公司披露的投资者关系活动记录。

接口按“参与机构”逐行返回同一次调研（``RPT_ORG_SURVEY``），这里按
调研日期、公告日与地点归并成一场活动，统计参与机构家数、机构类型、
接待高管与调研方式，并保留最新一场的纪要摘要。

* ``ensure_research(code)``    按需取数，本地缓存一天
* ``build_research(code)``     生成分析用摘要
* ``research_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_research(...)``     渲染成可读文本

口径：机构家数取披露的接待对象数（同一机构重复出现只算一次）；调研
热度不等于基本面。``build_research`` 会访问网络，界面里请放到后台线程。
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_ORG_SURVEY"
PAGE_URL = "https://data.eastmoney.com/jgdy/"
SOURCE_LABEL = "东方财富 机构调研"

TABLE = "company_research"
NOTES_TABLE = "research_notes"

DEFAULT_CACHE_HOURS = 24
NOTE_CACHE_HOURS = 24 * 30
DEFAULT_YEARS = 3
MAX_YEARS = 10
MAX_EVENTS = 20
MAX_INSTITUTIONS = 20
MAX_CONTENT_CHARS = 4000
MAX_NOTE_CHARS = 20_000
CONTENT_EXCERPT = 200
PAGE_SIZE = 80
MAX_PAGES = 30

DEFAULT_NOTE_EVENTS = 2
MAX_NOTE_EVENTS = 10
DEFAULT_NOTE_BUDGET = 12_000
MIN_NOTE_BUDGET = 3_000
MAX_NOTE_BUDGET = 40_000
LEAD_CLIP = 1_200
ANSWER_CLIP = 2_500


class ResearchError(Exception):
    """机构调研数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def research_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """把接口返回的机构行归并成调研活动（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise ResearchError(f"{code}：机构调研接口返回格式异常")

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _normalize_row(row)
        if item is not None:
            normalized.append(item)
    return research_from_events(
        normalized, code, years=years, today=today
    )


def research_from_events(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """由已规范化的机构行生成活动、年度与类型汇总（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise ResearchError(f"{code}：机构调研记录格式异常")

    years = _window_years(years)
    today_date = _parse_date(today) or date.today()
    start = f"{today_date.year - years + 1}-01-01"
    end = today_date.isoformat()

    all_events = _group(rows)
    window = [
        event for event in all_events
        if start <= event["date"] <= end
    ]
    latest = window[0] if window else (
        all_events[0] if all_events else None
    )
    counts = _counts(window, latest, today_date)
    notes = _notes(all_events, window, latest, years)

    data = {
        "available": True,
        "code": code,
        "as_of": (
            max(event["notice_date"] for event in all_events)
            if all_events else None
        ),
        "window_start": start,
        "window_end": end,
        "years": years,
        "has_research": bool(all_events),
        "latest": latest,
        "events": window[:MAX_EVENTS],
        "events_truncated": max(0, len(window) - MAX_EVENTS),
        "annual": _annual(window),
        "org_types": _org_type_totals(window),
        "counts": counts,
        "notes": notes,
    }
    data["summary"] = format_research(data, code=code)
    return data


def fetch_research(code: str, years: int = DEFAULT_YEARS) -> dict:
    """抓取调研参与机构行；只缓存规范化后的记录。"""
    code = normalize_stock_code(code)
    if not code:
        raise ResearchError("请输入股票代码")

    years = _window_years(years)
    start = f"{date.today().year - years + 1}-01-01"

    try:
        raw_rows, truncated = _request(code, start)
    except ResearchError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise ResearchError(f"获取机构调研数据失败：{error}") from error

    rows = [
        item
        for row in raw_rows
        if isinstance(row, dict)
        for item in [_normalize_row(row)]
        if item is not None
    ]

    # 纪要原文很长，只给最近一场附加摘要，其余活动不重复存储。
    if rows:
        first = rows[0]
        content = _request_content(
            code, first["notice_date"], first["date"]
        )
        if content:
            first["content"] = _clip_text(content, MAX_CONTENT_CHARS)

    return {
        "code": code,
        "years": years,
        "events": rows,
        "count": len(rows),
        "truncated": truncated,
    }


def ensure_research(
    code: str,
    years: int = DEFAULT_YEARS,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取机构调研，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ResearchError("请输入股票代码")
    years = _window_years(years)

    cached = _cached_row(code, years)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_research(code, years=years)
    except ResearchError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(code, years, data)
    return {"data": _cached_row(code, years) or data, "stale": False}


def fetch_note(
    code: str,
    notice_date: str,
    receive_date: str | None = None,
) -> str | None:
    """抓取一场调研的问答原文。"""
    code = normalize_stock_code(code)
    if not code or not notice_date:
        return None
    content = _request_content(code, notice_date, receive_date or notice_date)
    return _clip_text(content, MAX_NOTE_CHARS)


def ensure_note(
    code: str,
    notice_date: str | None,
    receive_date: str | None = None,
    max_age_hours: float = NOTE_CACHE_HOURS,
    force: bool = False,
) -> str | None:
    """按活动读取纪要原文，缓存命中就不再抓取。

    纪要发布后基本不再变化，缓存保留 30 天；抓取失败时回退旧缓存。
    """
    code = normalize_stock_code(code)
    if not code or not notice_date:
        return None
    key = f"{receive_date or notice_date}|{notice_date}"

    cached = _cached_note(code, key)
    if (
        cached and not force
        and _age_hours(cached.get("fetched_at")) <= max_age_hours
    ):
        return cached.get("content")

    content = fetch_note(code, notice_date, receive_date)
    if content:
        _save_note(code, key, content)
        return content
    return cached.get("content") if cached else None


def build_research(
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise ResearchError("请输入股票代码")
    years = _window_years(years)

    result = ensure_research(
        code,
        years=years,
        max_age_hours=max_age_hours,
        force=force,
    )
    rows = result["data"].get("events") or []
    data = research_from_events(rows, code, years=years, today=today)
    data["truncated"] = bool(result["data"].get("truncated"))

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name:
        name = next(
            (row.get("security_name") for row in rows
             if row.get("security_name")),
            None,
        )
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL
    data["summary"] = format_research(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def build_research_notes(
    code: str,
    events: int = DEFAULT_NOTE_EVENTS,
    years: int = DEFAULT_YEARS,
    max_chars: int = DEFAULT_NOTE_BUDGET,
    today: str | None = None,
    max_age_hours: float = NOTE_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """最近几场调研的问答原文，交给大模型自己概括。

    纪要原文按活动缓存在 ``research_notes`` 表里，同一场不会重复抓取。
    返回的原文按 ``max_chars`` 控制总篇幅，放不下的问答标记 ``truncated``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ResearchError("请输入股票代码")
    years = _window_years(years)
    try:
        limit = min(max(int(events), 1), MAX_NOTE_EVENTS)
    except (TypeError, ValueError):
        limit = DEFAULT_NOTE_EVENTS
    budget = _note_budget(max_chars)

    base = build_research(code, years=years, today=today)
    selected = (base.get("events") or [])[:limit]

    contents = [
        ensure_note(
            code,
            event.get("notice_date"),
            event.get("date"),
            max_age_hours=max_age_hours,
            force=force,
        )
        for event in selected
    ]
    with_note = sum(1 for content in contents if content)
    share = budget // with_note if with_note else 0

    rows = []
    for event, content in zip(selected, contents):
        item = {
            "key": event.get("key"),
            "date": event.get("date"),
            "notice_date": event.get("notice_date"),
            "place": event.get("place"),
            "ways": event.get("ways") or [],
            "receptionists": event.get("receptionists") or [],
            "institution_count": event.get("institution_count"),
            "disclosed_count": event.get("disclosed_count"),
            "has_note": bool(content),
            "lead": None,
            "qa": [],
            "text": None,
            "total_chars": len(content or ""),
            "returned_chars": 0,
            "truncated": False,
            "omitted": 0,
        }
        if content:
            item.update(_note_body(content, share))
        rows.append(item)

    counts = {
        "events": len(rows),
        "events_with_notes": sum(1 for item in rows if item["has_note"]),
        "questions": sum(len(item["qa"]) for item in rows),
        "omitted": sum(item["omitted"] for item in rows),
        "total_chars": sum(item["total_chars"] for item in rows),
        "returned_chars": sum(item["returned_chars"] for item in rows),
    }
    notes = [
        "纪要是公司整理披露的，问题经过筛选，回答不代表承诺",
        "原文按篇幅截取，可调大 max_chars 或减少 events 读取更多问答",
        f"只读取最近 {len(rows)} 场已披露的纪要",
    ]
    if not counts["events_with_notes"]:
        notes = ["最近几场没有取到纪要原文，可能公司未披露全文"]

    data = {
        "available": True,
        "code": code,
        "name": base.get("name"),
        "as_of": base.get("as_of"),
        "years": years,
        "budget": budget,
        "events": rows,
        "counts": counts,
        "notes": notes,
        "sources": base.get("sources") or {SOURCE_LABEL: base.get("as_of")},
        "url": base.get("url") or PAGE_URL,
        "fetched_at": base.get("fetched_at") or "",
        "stale": base.get("stale", False),
    }
    data["summary"] = format_research_notes(
        data,
        code=code,
        name=base.get("name"),
        as_of=base.get("as_of"),
    )
    return data


def format_research_notes(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：最近几场的问答原文与截取情况。"""
    code = code or data.get("code")
    years = data.get("years") or DEFAULT_YEARS
    title = f"{name}({code})" if name else (code or "机构调研纪要")

    parts = [title, f"调研纪要近{years}年"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    counts = data.get("counts") or {}
    text = (
        f"最近 {counts.get('events', 0)} 场："
        f"{counts.get('events_with_notes', 0)} 场有纪要 / "
        f"共 {counts.get('questions', 0)} 问"
    )
    if counts.get("omitted"):
        text += f"（另有 {counts['omitted']} 问未展示）"
    lines.append(text)

    for event in data.get("events") or []:
        head = f"{event.get('date')}"
        if event.get("place"):
            head += f" {event['place']}"
        head += f"（{event.get('institution_count')} 家）"
        lines.append(head)
        if event.get("qa"):
            for item in event["qa"][:2]:
                lines.append(f"  问：{item.get('question')}")
                answer = item.get("answer") or ""
                clipped = answer[:80] + ("…" if len(answer) > 80 else "")
                lines.append(f"  答：{clipped}")
        elif event.get("text"):
            body = event["text"]
            lines.append(f"  {body[:100]}{'…' if len(body) > 100 else ''}")
        else:
            lines.append("  未取到纪要原文")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


def format_research(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：场次与机构家数 + 最近一场 + 机构类型 + 纪要。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    years = data.get("years") or DEFAULT_YEARS
    latest = data.get("latest")
    counts = data.get("counts") or {}
    title = f"{name}({code})" if name else (code or "机构调研")

    parts = [title, f"机构调研近{years}年"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if latest and counts.get("events"):
        lines.append(
            f"近{years}年：{counts['events']} 场 / 机构接待 "
            f"{counts.get('institution_visits', 0)} 家次 / "
            f"最近 {latest.get('date')}"
            f"（{latest.get('institution_count')} 家）"
        )
        lines.append(_latest_line(latest))
        org_text = _org_type_text(data.get("org_types") or [])
        if org_text:
            lines.append(org_text)
        institution_text = _institution_text(latest)
        if institution_text:
            lines.append(institution_text)
        excerpt = _excerpt(latest.get("content") or "", CONTENT_EXCERPT)
        if excerpt:
            lines.append(f"纪要：{excerpt}")
    elif latest:
        lines.append(
            f"近{years}年没有已披露的机构调研；"
            f"最近一次为 {latest.get('date')}"
        )
    else:
        lines.append("暂无已披露的机构调研记录")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析与归并
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    notice_date = _date(row.get("NOTICE_DATE"))
    receive_date = _date(row.get("RECEIVE_START_DATE")) or notice_date
    if not notice_date or not receive_date:
        return None

    place = _text(row.get("RECEIVE_PLACE"))
    institution = _text(row.get("RECEIVE_OBJECT"))
    return {
        "date": receive_date,
        "notice_date": notice_date,
        "place": place,
        "ways": _text(row.get("RECEIVE_WAY_EXPLAIN")),
        "receptionists": _text(row.get("RECEPTIONIST")),
        "institution": institution,
        "org_type": _text(row.get("ORG_TYPE")) or "未分类",
        "investigators": _text(row.get("INVESTIGATORS")),
        "institution_total": _int(row.get("SUM")),
        "content": _clip_text(
            _text(row.get("CONTENT")), MAX_CONTENT_CHARS
        ),
        "security_name": _text(row.get("SECURITY_NAME_ABBR")),
        "key": f"{receive_date}|{notice_date}|{place or ''}",
    }


def _group(rows: list[dict]) -> list[dict]:
    """按调研日期 + 公告日 + 地点归并同一次活动。"""
    groups: dict[tuple, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_date = row.get("date")
        notice_date = row.get("notice_date")
        if not event_date or not notice_date:
            continue
        key = (event_date, notice_date, row.get("place") or "")
        event = groups.get(key)
        if event is None:
            event = {
                "key": f"{event_date}|{notice_date}|{row.get('place') or ''}",
                "date": event_date,
                "notice_date": notice_date,
                "place": row.get("place"),
                "ways": [],
                "receptionists": [],
                "_institutions": {},
                "_totals": [],
                "content": None,
                "security_name": None,
            }
            groups[key] = event

        institution = row.get("institution")
        if institution and institution not in event["_institutions"]:
            event["_institutions"][institution] = (
                row.get("org_type") or "未分类"
            )
        for way in _split(row.get("ways")):
            if way not in event["ways"]:
                event["ways"].append(way)
        for person in _split(row.get("receptionists")):
            if person not in event["receptionists"]:
                event["receptionists"].append(person)
        total = row.get("institution_total")
        if isinstance(total, int) and total > 0:
            event["_totals"].append(total)
        if not event["content"] and row.get("content"):
            event["content"] = row["content"]
        if not event["security_name"] and row.get("security_name"):
            event["security_name"] = row["security_name"]

    events = []
    for event in groups.values():
        institutions = event.pop("_institutions")
        totals = event.pop("_totals")
        disclosed = len(institutions)
        event["institutions"] = list(institutions)
        event["disclosed_count"] = disclosed
        event["institution_count"] = max(totals) if totals else disclosed
        event["org_types"] = _type_rows(institutions.values())
        events.append(event)

    return sorted(
        events,
        key=lambda item: (item["date"], item["notice_date"]),
        reverse=True,
    )


def _type_rows(types) -> list[dict]:
    counts: dict[str, int] = {}
    for name in types:
        counts[name] = counts.get(name, 0) + 1
    rows = [
        {"type": name, "count": count}
        for name, count in counts.items()
    ]
    rows.sort(key=lambda item: (-item["count"], item["type"]))
    return rows


def _org_type_totals(events: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for event in events:
        for item in event.get("org_types") or []:
            name = item["type"]
            counts[name] = counts.get(name, 0) + item["count"]
    rows = [
        {"type": name, "count": count}
        for name, count in counts.items()
    ]
    rows.sort(key=lambda item: (-item["count"], item["type"]))
    return rows


def _annual(events: list[dict]) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for event in events:
        by_year.setdefault(int(event["date"][:4]), []).append(event)
    return [
        {
            "year": year,
            "events": len(by_year[year]),
            "institution_visits": sum(
                event.get("institution_count") or 0
                for event in by_year[year]
            ),
        }
        for year in sorted(by_year)
    ]


def _counts(
    window: list[dict],
    latest: dict | None,
    today_date: date,
) -> dict:
    cutoff = (today_date - timedelta(days=365)).isoformat()
    recent = [event for event in window if event["date"] >= cutoff]
    empty = {
        "events": 0,
        "institution_visits": 0,
        "latest_date": None,
        "latest_notice_date": None,
        "latest_institution_count": None,
        "latest_place": None,
        "events_12m": 0,
        "institution_visits_12m": 0,
        "latest_org_types": [],
    }
    if latest is None:
        return empty
    return {
        "events": len(window),
        "institution_visits": sum(
            event.get("institution_count") or 0 for event in window
        ),
        "latest_date": latest.get("date"),
        "latest_notice_date": latest.get("notice_date"),
        "latest_institution_count": latest.get("institution_count"),
        "latest_place": latest.get("place"),
        "events_12m": len(recent),
        "institution_visits_12m": sum(
            event.get("institution_count") or 0 for event in recent
        ),
        "latest_org_types": latest.get("org_types") or [],
    }


def _notes(
    all_events: list[dict],
    window: list[dict],
    latest: dict | None,
    years: int,
) -> list[str]:
    if not all_events or latest is None:
        return ["没有取到机构调研记录，可能公司未披露或数据源未收录"]

    if not window:
        return [
            f"近{years}年没有已披露的机构调研；"
            f"最近一次为{latest['date']}",
            "调研记录只含已披露的接待，未披露的无法统计",
        ]

    return [
        "调研纪要是公司整理披露的交流记录，问答可能经过筛选，不代表承诺",
        "机构家数按披露的接待对象统计，同一机构多次调研会重复计入",
        "调研热度不等于基本面，需要看问答内容与后续经营数据",
        "只含已披露的调研记录，未披露的接待无法统计",
    ]


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _request(code: str, start: str) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    truncated = False
    page = 1
    while page <= MAX_PAGES:
        payload = _get_page(code, start, page)
        if payload.get("success") is False:
            message = payload.get("message") or "接口返回失败"
            if payload.get("result") is None and "为空" in message:
                return rows, truncated
            raise ResearchError(
                f"{code}：获取机构调研数据失败（{message}）"
            )
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise ResearchError(f"{code}：机构调研接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise ResearchError(f"{code}：机构调研接口数据格式异常")
        rows.extend(item for item in data if isinstance(item, dict))
        try:
            pages = int(result.get("pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages:
            break
        page += 1
    else:
        truncated = True
    return rows, truncated


def _get_page(code: str, start: str, page: int) -> dict:
    response = requests.get(
        ENDPOINT,
        params={
            "reportName": REPORT_NAME,
            "columns": (
                "SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,"
                "RECEIVE_START_DATE,RECEIVE_PLACE,RECEIVE_WAY_EXPLAIN,"
                "RECEPTIONIST,RECEIVE_OBJECT,ORG_TYPE,INVESTIGATORS,"
                "SUM,NUM"
            ),
            "filter": (
                f'(SECURITY_CODE="{code}")'
                f"(NOTICE_DATE>='{start}')"
            ),
            "pageNumber": str(page),
            "pageSize": str(PAGE_SIZE),
            "sortColumns": "NOTICE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        },
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ResearchError(f"{code}：机构调研接口返回格式异常")
    return payload


def _request_content(
    code: str,
    notice_date: str,
    receive_date: str,
) -> str | None:
    """只取最近一场的纪要原文，失败不影响主体数据。"""
    filters = [
        f'(SECURITY_CODE="{code}")',
        f"(NOTICE_DATE='{notice_date}')",
    ]
    if receive_date:
        filters.append(f"(RECEIVE_START_DATE='{receive_date}')")
    try:
        response = requests.get(
            ENDPOINT,
            params={
                "reportName": REPORT_NAME,
                "columns": "CONTENT",
                "filter": "".join(filters),
                "pageNumber": "1",
                "pageSize": "1",
                "sortColumns": "NOTICE_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - 纪要属锦上添花，失败就跳过
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    data = (result or {}).get("data") or []
    if not data or not isinstance(data[0], dict):
        return None
    return _text(data[0].get("CONTENT"))


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
            years integer not null,
            data text,
            fetched_at text,
            primary key (code, years)
        )
        """
    )


def _cached_row(code: str, years: int) -> dict | None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
        row = conn.execute(
            f"select * from {TABLE} where code = ? and years = ?",
            (code, years),
        ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["data"] or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    data.setdefault("code", code)
    data["fetched_at"] = row["fetched_at"]
    return data


def _save_row(code: str, years: int, data: dict) -> None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(
                f"""
                insert or replace into {TABLE}
                    (code, years, data, fetched_at)
                values (?, ?, ?, ?)
                """,
                (
                    code,
                    years,
                    json.dumps(
                        {key: value for key, value in data.items()
                         if key != "fetched_at"},
                        ensure_ascii=False,
                    ),
                    _now(),
                ),
            )


def _ensure_notes_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        create table if not exists {NOTES_TABLE} (
            code text not null,
            note_key text not null,
            content text,
            fetched_at text,
            primary key (code, note_key)
        )
        """
    )


def _cached_note(code: str, key: str) -> dict | None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_notes_table(conn)
        row = conn.execute(
            f"select * from {NOTES_TABLE} where code = ? and note_key = ?",
            (code, key),
        ).fetchone()
    if row is None or not row["content"]:
        return None
    return {"content": row["content"], "fetched_at": row["fetched_at"]}


def _save_note(code: str, key: str, content: str) -> None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_notes_table(conn)
            conn.execute(
                f"""
                insert or replace into {NOTES_TABLE}
                    (code, note_key, content, fetched_at)
                values (?, ?, ?, ?)
                """,
                (code, key, content, _now()),
            )


def _age_hours(fetched_at: str | None) -> float:
    if not fetched_at:
        return float("inf")
    try:
        fetched = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return float("inf")
    return (datetime.now() - fetched).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _window_years(years) -> int:
    try:
        return min(max(int(years), 1), MAX_YEARS)
    except (TypeError, ValueError):
        return DEFAULT_YEARS


def _parse_date(value) -> date | None:
    text = _date(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date(value) -> str | None:
    text = _text(value)
    return text[:10] if text else None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "-" else text


def _int(value) -> int | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _split(value) -> list[str]:
    """按逗号拆分名单；顿号保留在条目内部（如“副总裁、董事会秘书”）。"""
    text = _text(value)
    if not text:
        return []
    for sep in ("，", ";", "；"):
        text = text.replace(sep, ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def _clip_text(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= limit else text[:limit]


def _excerpt(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "…"


def _latest_line(event: dict) -> str:
    parts = [f"最近：{event.get('date')}"]
    if event.get("place"):
        parts.append(str(event["place"]))
    text = " ".join([parts[0], parts[1]]) if len(parts) > 1 else parts[0]
    if event.get("ways"):
        text += " / " + "、".join(event["ways"])
    if event.get("receptionists"):
        text += " / 接待：" + "、".join(event["receptionists"])
    return text


def _org_type_text(rows: list[dict]) -> str:
    items = [
        f"{row['type']} {row['count']} 家次"
        for row in rows[:6] if row.get("count")
    ]
    return f"机构类型：{'、'.join(items)}" if items else ""


def _institution_text(event: dict) -> str:
    names = event.get("institutions") or []
    if not names:
        return ""
    shown = names[:5]
    text = f"参与机构：{'、'.join(shown)}"
    total = event.get("disclosed_count") or len(names)
    if total > len(shown):
        text += f" 等 {total} 家"
    return text


def _split_blocks(content: str) -> list[str]:
    """把纪要拆成问答块；没有“问：”标记时退化为段落。"""
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [
        block.strip()
        for block in re.split(r"(?=问\s*[：:])", text)
        if block.strip()
    ]
    if len(blocks) <= 1:
        blocks = [
            block.strip()
            for block in re.split(r"\n\s*\n", text)
            if block.strip()
        ]
    return blocks


def _question_of(block: str) -> str | None:
    match = re.match(
        r"问\s*[：:]\s*(.*?)(?=答\s*[：:]|\Z)", block, re.S
    )
    if not match:
        return None
    text = _one_line(match.group(1))
    return text[:300] if text else None


def _answer_of(block: str) -> str:
    match = re.search(r"答\s*[：:]\s*(.*)", block, re.S)
    return _one_line(match.group(1) if match else "")


def _note_budget(value) -> int:
    try:
        budget = int(value)
    except (TypeError, ValueError):
        return DEFAULT_NOTE_BUDGET
    return min(max(budget, MIN_NOTE_BUDGET), MAX_NOTE_BUDGET)


def _note_body(content: str, budget: int) -> dict:
    """把一场纪要整理成开场说明 + 问答对，按字数预算截取。

    问答原文交给大模型概括，这里只做清洗、去重与篇幅控制，不做检索。
    """
    blocks = _note_blocks(content)
    lead = None
    if blocks and not blocks[0]["question"]:
        lead = _clip_text(blocks[0]["text"], min(LEAD_CLIP, budget))
        blocks = blocks[1:]
    questions = [block for block in blocks if block["question"]]

    if not questions:
        text = _clip_text(_one_line(content), budget) or ""
        return {
            "lead": None,
            "qa": [],
            "text": text,
            "returned_chars": len(text),
            "truncated": len(_one_line(content)) > len(text),
            "omitted": 0,
        }

    used = len(lead or "")
    qa: list[dict] = []
    omitted = 0
    for block in questions:
        if omitted:
            omitted += 1
            continue
        room = budget - used - len(block["question"]) - 4
        if room < 60:
            omitted = 1
            continue
        answer = block["text"][:min(room, ANSWER_CLIP)]
        qa.append({"question": block["question"], "answer": answer})
        used += len(block["question"]) + len(answer) + 4

    return {
        "lead": lead,
        "qa": qa,
        "text": None,
        "returned_chars": used,
        "truncated": omitted > 0,
        "omitted": omitted,
    }


def _note_blocks(content: str) -> list[dict]:
    """把纪要拆成 ``{"question", "text"}``；开场等非问答段落 question 为空。"""
    blocks = []
    seen = set()
    for block in _split_blocks(content):
        question = _question_of(block)
        if question:
            if question in seen:
                continue
            seen.add(question)
            blocks.append({"question": question, "text": _answer_of(block)})
        else:
            text = _one_line(block)
            if text:
                blocks.append({"question": None, "text": text})
    return blocks


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
