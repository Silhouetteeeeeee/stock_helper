"""公司概况：这家公司自己怎么描述生意。

财报回答"赚了多少"，概况回答"卖什么、在哪个行业、规模多大"。
数据取自东方财富 F10 公司概况，本地缓存一周——概况一年也变不了几次。

* ``ensure_profile(code)``   按需取数，返回 ``{"data": ..., "stale": bool}``
* ``build_profile(code)``    补上来源与摘要，供工具层直接使用
* ``profile_from_payload``   纯函数，便于测试
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime

import requests

import stock.utils as su
from common.db_utils import DB_PATH
from stock.db_service import normalize_stock_code

ENDPOINT = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
SOURCE_LABEL = "东方财富 F10 公司概况"

TABLE = "company_profile"
DEFAULT_CACHE_HOURS = 24 * 7
# 经营范围动辄上千字，回答"卖什么"用不到后面那些
SCOPE_MAX_CHARS = 200


class ProfileError(Exception):
    """公司概况操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def profile_from_payload(payload: dict, code: str) -> dict:
    """从 F10 接口的 ``jbzl`` 里取出概况（纯函数）。"""
    rows = payload.get("jbzl") or []
    if not rows:
        raise ProfileError(f"{code}：没有取到公司概况")

    raw = rows[0]
    return {
        "code": code,
        "name": _text(raw.get("SECURITY_NAME_ABBR")),
        "industry": _text(raw.get("EM2016")),
        "profile": _text(raw.get("ORG_PROFILE")),
        "scope": _clip(_text(raw.get("BUSINESS_SCOPE")), SCOPE_MAX_CHARS),
        "employees": _int(raw.get("EMP_NUM")),
        "as_of": date.today().isoformat(),
        "fetched_at": _now(),
    }


def fetch_profile(code: str) -> dict:
    """抓取公司概况。"""
    code = normalize_stock_code(code)
    prefix, digits = su.get_stock_prefix(code)
    if prefix not in ("SH", "SZ", "BJ"):
        raise ProfileError(f"{code}：无法判断交易所")

    try:
        response = requests.get(
            ENDPOINT,
            params={"code": f"{prefix}{digits}"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            # 本机系统代理会拦掉东方财富的接口，这里和行情请求一样直连
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except ProfileError:
        raise
    except Exception as error:
        raise ProfileError(f"获取公司概况失败：{error}") from error

    return profile_from_payload(payload, code)


def ensure_profile(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取公司概况，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ProfileError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_profile(code)
    except ProfileError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": data, "stale": False}


def build_profile(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise ProfileError("请输入股票代码")

    result = ensure_profile(code, max_age_hours=max_age_hours, force=force)
    row = result["data"]
    as_of = row.get("as_of") or (row.get("fetched_at") or "")[:10]
    return {
        "available": True,
        "code": code,
        "name": row.get("name"),
        "industry": row.get("industry"),
        "profile": row.get("profile"),
        "scope": row.get("scope"),
        "employees": row.get("employees"),
        "as_of": as_of,
        "fetched_at": row.get("fetched_at") or "",
        "stale": result.get("stale", False),
        "sources": {SOURCE_LABEL: as_of},
        "summary": format_profile(row, code, as_of),
    }


def format_profile(row: dict, code: str, as_of: str) -> str:
    """渲染成可读文本：一行身份，一行主营业务。"""
    name = row.get("name")
    parts = [f"{name}({code})" if name else code]
    if row.get("industry"):
        parts.append(row["industry"])
    if row.get("employees") is not None:
        parts.append(f"员工 {row['employees']} 人")
    if as_of:
        parts.append(f"概况 {as_of}")
    lines = [" · ".join(parts)]
    if row.get("profile"):
        lines.append(f"主营业务：{row['profile']}")
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
            name text,
            industry text,
            profile text,
            scope text,
            employees integer,
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
    data = dict(row)
    # 表里不单独存 as_of，概况的"数据日期"就是抓取那一天
    data.setdefault("as_of", (data.get("fetched_at") or "")[:10] or None)
    return data


def _save_row(data: dict) -> None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(
                f"""
                insert or replace into {TABLE} (
                    code, name, industry, profile, scope, employees, fetched_at
                ) values (
                    :code, :name, :industry, :profile, :scope, :employees, :fetched_at
                )
                """,
                data,
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
def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text == "-" else text


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clip(text: str | None, max_chars: int) -> str | None:
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，、；： ") + "…"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
