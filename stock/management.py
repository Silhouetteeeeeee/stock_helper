"""管理层与治理：董事、高管、监事及审计委员会成员。

公告只说明"发生了什么"，管理层数据说明"是谁在决策、拿了多少、持了
多少"。这里取同花顺 F10 的高管介绍接口，按董事会 / 高管 / 监事会 /
审计委员会分组，保留职务、任期、年龄、学历、薪酬、持股与简历。

* ``ensure_management(code)``   按需取数，本地缓存一周
* ``build_management(code)``    生成分析用摘要
* ``management_from_payload``   纯函数，由接口返回生成，便于测试
* ``format_management(...)``    渲染成可读文本

口径：薪酬单位是元；持股数是股；比例是百分数（46.8 表示 46.8%）。
缺失值保留为 None，不填 0。``build_management`` 会访问网络，界面里请
放到后台线程调用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://basic.10jqka.com.cn/mapp/{code}/a_manager_introduce.json"
PAGE_URL = "https://basic.10jqka.com.cn/{code}/company.html"
SOURCE_LABEL = "同花顺 F10 高管介绍"

TABLE = "company_management"

# 董事会 / 高管变动不频繁，缓存一周；也避免切换标的时反复打接口
DEFAULT_CACHE_HOURS = 24 * 7
# 文本摘要每组列出多少人，完整名单仍由工具结果返回
SUMMARY_PER_GROUP = 3

# 接口的四个人员分组；family（家族持股）多数公司为空，有数据才展示
GROUPS = (
    ("directors", "董事会"),
    ("manager", "高管"),
    ("supervisors", "监事会"),
    ("auditors", "审计委员会"),
    ("family", "家族"),
)


class ManagementError(Exception):
    """管理层数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def management_from_payload(payload: dict, code: str) -> dict:
    """把接口返回整理成分组人员名单（纯函数）。"""
    if not isinstance(payload, dict):
        raise ManagementError(f"{code}：高管接口返回格式异常")
    if payload.get("status_code") not in (0, "0", None):
        message = payload.get("status_msg") or "接口返回失败"
        raise ManagementError(f"{code}：获取管理层数据失败（{message}）")

    raw_groups = payload.get("data")
    if not isinstance(raw_groups, dict):
        raise ManagementError(f"{code}：没有取到管理层数据")

    groups: dict[str, dict] = {}
    for key, label in GROUPS:
        raw_group = raw_groups.get(key)
        if not isinstance(raw_group, dict):
            continue
        members = _members(raw_group.get("list"))
        # 空分组（如未设监事会）不展示，避免把"没有数据"误读成"0 人"
        if not members:
            continue
        groups[key] = {
            "label": label,
            "count": len(members),
            "salary_total": _number(raw_group.get("salary")),
            "hold_ratio": _number(raw_group.get("rate")),
            "direct_ratio": _number(raw_group.get("directnum_rate")),
            "indirect_ratio": _number(raw_group.get("indirectnum_rate")),
            "members": members,
        }

    if not any(group["count"] for group in groups.values()):
        raise ManagementError(f"{code}：没有取到管理层数据")

    reports = [
        member["report"]
        for group in groups.values()
        for member in group["members"]
        if member.get("report")
    ]
    as_of = max(reports) if reports else _year_end(raw_groups.get("salary_year"))

    return {
        "code": code,
        "as_of": as_of,
        "salary_year": _text(raw_groups.get("salary_year")),
        "bonus_year": _text(raw_groups.get("bonus_year")),
        "groups": groups,
        "count": sum(group["count"] for group in groups.values()),
    }


def fetch_management(code: str) -> dict:
    """抓取管理层数据。"""
    code = normalize_stock_code(code)
    if not code:
        raise ManagementError("请输入股票代码")

    try:
        response = requests.get(
            ENDPOINT.format(code=code),
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            # 与东方财富接口一样，绕开本机系统代理
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except ManagementError:
        raise
    except Exception as error:
        raise ManagementError(f"获取管理层数据失败：{error}") from error

    return management_from_payload(payload, code)


def ensure_management(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取管理层数据，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ManagementError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_management(code)
    except ManagementError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_management(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise ManagementError("请输入股票代码")

    result = ensure_management(code, max_age_hours=max_age_hours, force=force)
    data = result["data"]
    stock = get_stock(code)
    name = stock.name if stock is not None else None
    as_of = data.get("as_of") or (data.get("fetched_at") or "")[:10]
    return {
        "available": True,
        "code": code,
        "name": name,
        "as_of": as_of,
        "salary_year": data.get("salary_year"),
        "count": data.get("count") or 0,
        "groups": data.get("groups") or {},
        "fetched_at": data.get("fetched_at") or "",
        "stale": result.get("stale", False),
        "sources": {SOURCE_LABEL: as_of},
        "summary": format_management(data, code=code, name=name, as_of=as_of),
    }


def format_management(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：一行身份，每组最多列几个人。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    parts = [f"{name}({code})" if name else (code or "管理层")]
    if as_of:
        parts.append(f"管理层 {as_of}")
    groups = data.get("groups") or {}
    counts = "，".join(
        f"{group.get('label')} {group.get('count')} 人"
        for group in groups.values()
        if group.get("count")
    )
    if counts:
        parts.append(counts)
    lines = [" · ".join(parts)]

    for group in groups.values():
        members = group.get("members") or []
        if not members:
            continue
        shown = []
        for member in members[:SUMMARY_PER_GROUP]:
            duty = "、".join(member.get("duties") or []) or "职务未披露"
            detail = [duty]
            if member.get("tenure"):
                detail.append(member["tenure"])
            if member.get("age"):
                detail.append(member["age"])
            if member.get("salary") is not None:
                detail.append(f"薪酬 {_wan(member['salary'])}")
            if member.get("hold_ratio") is not None:
                detail.append(f"持股 {_pct(member['hold_ratio'])}")
            shown.append(f"{member.get('name')}（{'，'.join(detail)}）")
        lines.append(f"{group.get('label')}：" + "；".join(shown))
    return "\n".join(lines)


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
            code text primary key,
            data text,
            fetched_at text
        )
        """
    )


def _cached_row(code: str) -> dict | None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
        row = conn.execute(
            f"select * from {TABLE} where code = ?",
            (code,),
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
    # 缓存时间以表列为准；数据体里不能留旧值，否则过期判断会失效
    data["fetched_at"] = row["fetched_at"]
    return data


def _save_row(data: dict) -> None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(
                f"""
                insert or replace into {TABLE} (code, data, fetched_at)
                values (?, ?, ?)
                """,
                (
                    data.get("code"),
                    json.dumps(
                        {key: value for key, value in data.items()
                         if key != "fetched_at"},
                        ensure_ascii=False,
                    ),
                    _now(),
                ),
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
def _members(items) -> list[dict]:
    members = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        start = _text(item.get("from"))
        end = _text(item.get("to"))
        members.append({
            "name": name,
            "duty": _text(item.get("duty")),
            "duties": _duties(item.get("duty")),
            "tenure": f"{start} 至今" if start and not end else (
                f"{start}–{end}" if start and end else end
            ),
            "tenure_start": start,
            "tenure_end": end,
            "change": _text(item.get("change")),
            "sex": _text(item.get("sex")),
            "age": _text(item.get("age")),
            "birth_year": _int(item.get("brith_year")),
            "education": _text(item.get("record")),
            "salary": _number(item.get("salary")),
            "hold": _number(item.get("hold")),
            "direct_hold": _number(item.get("directnum")),
            "indirect_hold": _number(item.get("indirectnum")),
            "hold_ratio": _number(item.get("rate")),
            "direct_ratio": _number(item.get("directnum_rate")),
            "indirect_ratio": _number(item.get("indirectnum_rate")),
            "is_control": bool(item.get("is_control")),
            "is_legal_person": bool(item.get("is_legal_person")),
            "report": _text(item.get("report")),
            "resume": _text(item.get("resume")),
        })
    return members


def _duties(value) -> list[str]:
    text = _text(value)
    if not text:
        return []
    normalized = text.replace("，", ",").replace("、", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "-" else text


def _number(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _year_end(value) -> str | None:
    text = _text(value)
    return f"{text}-12-31" if text and text.isdigit() else None


def _pct(value) -> str:
    return f"{value:.1f}%"


def _wan(value: float) -> str:
    return f"{value / 1e4:.1f}万"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
