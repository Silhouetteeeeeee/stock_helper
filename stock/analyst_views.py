"""卖方研报观点：机构评级、目标价与盈利预测。

数据来自东方财富研报中心，按个股返回每篇研报的机构、分析师、评级、
目标价与未来三年 EPS/PE 预测。这里做三件事：近 N 天研报列表、最新
一篇/家机构的观点、评级与目标价分布，以及按机构最新一篇计算的一致
预期 EPS。

研报是卖方观点，带乐观倾向，评级口径各家不同，不能当成事实。

* ``ensure_reports(code)``     按需取数，本地缓存一天
* ``build_analyst_views(code)`` 生成分析用摘要
* ``reports_from_rows(...)``   纯函数，由接口返回生成，便于测试
* ``format_analyst_views(...)`` 渲染成可读文本
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://reportapi.eastmoney.com/report/list"
PAGE_URL = "https://data.eastmoney.com/report/stock.jshtml?stockcode={code}"
INFO_URL = "https://data.eastmoney.com/report/info/{info}.html"
SOURCE_LABEL = "东方财富 研报"

TABLE = "company_reports"

DEFAULT_CACHE_HOURS = 24
DEFAULT_DAYS = 365
MIN_DAYS = 30
MAX_DAYS = 730
MAX_REPORTS = 30
PAGE_SIZE = 50
MAX_PAGES = 40


class AnalystViewError(Exception):
    """研报数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def reports_from_rows(
    rows: list[dict],
    code: str,
    days: int = DEFAULT_DAYS,
    today: str | None = None,
) -> dict:
    """把接口返回的研报行整理成观点汇总（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise AnalystViewError(f"{code}：研报接口返回格式异常")

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _normalize_row(row)
        if item is not None:
            normalized.append(item)
    return reports_from_events(
        normalized, code, days=days, today=today
    )


def reports_from_events(
    reports: list[dict],
    code: str,
    days: int = DEFAULT_DAYS,
    today: str | None = None,
) -> dict:
    """由已规范化的研报行生成评级、目标价与一致预期（纯函数）。"""
    if not isinstance(reports, (list, tuple)):
        raise AnalystViewError(f"{code}：研报记录格式异常")

    days = _window_days(days)
    today_date = _parse_date(today) or date.today()
    start = (today_date - timedelta(days=days)).isoformat()
    end = today_date.isoformat()

    normalized = [
        item for item in reports
        if isinstance(item, dict) and item.get("publish_date")
    ]
    normalized.sort(key=lambda item: item["publish_date"], reverse=True)

    window = [
        item for item in normalized
        if start <= item["publish_date"] <= end
    ]
    latest_by_org = _latest_by_org(window)
    latest = window[0] if window else (
        normalized[0] if normalized else None
    )
    counts = _counts(window, latest_by_org, today_date)
    notes = _notes(normalized, window, days)

    data = {
        "available": True,
        "code": code,
        "as_of": (
            max(item["publish_date"] for item in normalized)
            if normalized else None
        ),
        "window_start": start,
        "window_end": end,
        "days": days,
        "has_reports": bool(normalized),
        "latest": latest,
        "reports": window[:MAX_REPORTS],
        "reports_truncated": max(0, len(window) - MAX_REPORTS),
        "latest_by_org": latest_by_org,
        "counts": counts,
        "notes": notes,
    }
    data["summary"] = format_analyst_views(
        data, code=code, as_of=data.get("as_of")
    )
    return data


def fetch_reports(code: str, days: int = DEFAULT_DAYS) -> dict:
    """抓取近 N 天的个股研报；只缓存整理后的记录。"""
    code = normalize_stock_code(code)
    if not code:
        raise AnalystViewError("请输入股票代码")
    days = _window_days(days)

    today_date = date.today()
    begin = (today_date - timedelta(days=days)).isoformat()
    end = today_date.isoformat()
    try:
        raw_rows, truncated = _request(code, begin, end)
    except AnalystViewError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise AnalystViewError(f"获取研报数据失败：{error}") from error

    reports = [
        item
        for row in raw_rows
        if isinstance(row, dict)
        for item in [_normalize_row(row)]
        if item is not None
    ]
    return {
        "code": code,
        "days": days,
        "reports": reports,
        "count": len(reports),
        "truncated": truncated,
    }


def ensure_reports(
    code: str,
    days: int = DEFAULT_DAYS,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取研报，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise AnalystViewError("请输入股票代码")
    days = _window_days(days)

    cached = _cached_row(code, days)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_reports(code, days=days)
    except AnalystViewError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(code, days, data)
    return {"data": _cached_row(code, days) or data, "stale": False}


def build_analyst_views(
    code: str,
    days: int = DEFAULT_DAYS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise AnalystViewError("请输入股票代码")
    days = _window_days(days)

    result = ensure_reports(
        code,
        days=days,
        max_age_hours=max_age_hours,
        force=force,
    )
    rows = result["data"].get("reports") or []
    data = reports_from_events(rows, code, days=days, today=today)
    data["reports_truncated"] = data.get("reports_truncated", 0)
    data["truncated"] = bool(result["data"].get("truncated"))

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name:
        name = next(
            (row.get("stock_name") for row in rows
             if row.get("stock_name")),
            None,
        )
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL.format(code=code)
    data["summary"] = format_analyst_views(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_analyst_views(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：研报数量/评级分布/目标价/一致预期/最新一篇。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    days = data.get("days") or DEFAULT_DAYS
    counts = data.get("counts") or {}
    latest = data.get("latest")
    title = f"{name}({code})" if name else (code or "卖方研报")

    parts = [title, f"卖方研报{_window_text(days)}"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if counts.get("reports"):
        lines.append(
            f"{_window_text(days)}：{counts['reports']} 篇 / "
            f"覆盖 {counts.get('orgs', 0)} 家机构"
            + _rating_text(counts.get("ratings") or [])
        )
        price_text = _target_text(counts)
        if price_text:
            lines.append(price_text)
        consensus_text = _consensus_text(counts)
        if consensus_text:
            lines.append(consensus_text)
        if latest:
            lines.append(
                f"最新：{latest.get('publish_date')} "
                f"{latest.get('org') or '未知机构'} "
                f"{latest.get('rating') or '未评级'}"
                f"《{_clip(latest.get('title') or '', 40)}》"
            )
    elif latest:
        lines.append(
            f"{_window_text(days)}没有研报覆盖；"
            f"最近一篇为 {latest.get('publish_date')}"
        )
    else:
        lines.append("暂无已披露的卖方研报")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析与汇总
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    publish_date = _date(row.get("publishDate"))
    if not publish_date:
        return None

    rating = _text(row.get("emRatingName"))
    last_rating = _text(row.get("lastEmRatingName"))
    info_code = _text(row.get("infoCode"))
    return {
        "publish_date": publish_date,
        "year": int(publish_date[:4]),
        "org": _text(row.get("orgSName")) or _text(row.get("orgName")),
        "researcher": _text(row.get("researcher")),
        "title": _text(row.get("title")),
        "rating": rating,
        "last_rating": last_rating,
        "rating_changed": bool(
            rating and last_rating and rating != last_rating
        ),
        "target_price": (
            _number(row.get("indvAimPriceT"))
            or _number(row.get("indvAimPriceL"))
        ),
        "eps_this_year": _number(row.get("predictThisYearEps")),
        "eps_next_year": _number(row.get("predictNextYearEps")),
        "eps_next2_year": _number(row.get("predictNextTwoYearEps")),
        "pe_this_year": _number(row.get("predictThisYearPe")),
        "info_code": info_code,
        "url": (
            INFO_URL.format(info=info_code)
            if info_code else PAGE_URL.format(code=row.get("stockCode"))
        ),
        "stock_name": _text(row.get("stockName")),
    }


def _latest_by_org(reports: list[dict]) -> list[dict]:
    """每家机构只保留最新一篇，用于评级分布与一致预期。"""
    seen: set[str] = set()
    items = []
    for report in reports:
        org = report.get("org") or "未知机构"
        if org in seen:
            continue
        seen.add(org)
        items.append(report)
    return items


def _counts(
    window: list[dict],
    latest_by_org: list[dict],
    today_date: date,
) -> dict:
    ratings: dict[str, int] = {}
    prices: list[float] = []
    for report in latest_by_org:
        name = report.get("rating") or "未评级"
        ratings[name] = ratings.get(name, 0) + 1
        if report.get("target_price") is not None:
            prices.append(report["target_price"])

    rating_rows = [
        {"rating": name, "reports": count}
        for name, count in ratings.items()
    ]
    rating_rows.sort(key=lambda item: (-item["reports"], item["rating"]))

    current = _consensus(latest_by_org, today_date.year)
    following = _consensus(latest_by_org, today_date.year + 1)
    return {
        "reports": len(window),
        "orgs": len(latest_by_org),
        "latest_date": window[0]["publish_date"] if window else None,
        "ratings": rating_rows,
        "rating_changes": sum(
            1 for report in latest_by_org
            if report.get("rating_changed")
        ),
        "target_price_orgs": len(prices),
        "target_price_low": min(prices) if prices else None,
        "target_price_high": max(prices) if prices else None,
        "target_price_median": _median(prices),
        "consensus_current_year": current,
        "consensus_next_year": following,
    }


def _consensus(latest_by_org: list[dict], year: int) -> dict:
    """同一目标年度只取同一批研报，避免新旧股本口径混在一起。

    优先用当年发布的研报里的“当年 EPS”；当年还没有研报时，才退回
    上一年研报里的“次年 EPS”。
    """
    same_year = [
        report["eps_this_year"]
        for report in latest_by_org
        if int(report.get("year") or 0) == year
        and report.get("eps_this_year") is not None
    ]
    if same_year:
        values = same_year
    else:
        values = [
            report["eps_next_year"]
            for report in latest_by_org
            if int(report.get("year") or 0) == year - 1
            and report.get("eps_next_year") is not None
        ]
    return {
        "year": year,
        "eps": _median(values),
        "orgs": len(values),
    }


def _notes(
    all_reports: list[dict],
    window: list[dict],
    days: int,
) -> list[str]:
    if not all_reports:
        return ["没有取到研报，可能近一段时间没有机构覆盖"]
    if not window:
        return [
            f"{_window_text(days)}没有研报覆盖；"
            f"最近一篇为{all_reports[0]['publish_date']}",
            "研报覆盖少不等于公司不好，只说明卖方关注度低",
        ]
    return [
        "卖方研报带乐观倾向，评级与目标价不是独立事实",
        "目标价只覆盖部分研报，且多为报告发布时点的判断",
        "EPS 预测是报告发布时的估计，会随财报和新报告修订",
    ]


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _request(
    code: str,
    begin: str,
    end: str,
) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    truncated = False
    page = 1
    while page <= MAX_PAGES:
        payload = _get_page(code, begin, end, page)
        data = payload.get("data")
        if not isinstance(data, list):
            raise AnalystViewError(f"{code}：研报接口数据格式异常")
        rows.extend(item for item in data if isinstance(item, dict))
        try:
            pages = int(payload.get("TotalPage") or 1)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages:
            break
        page += 1
    else:
        truncated = True
    return rows, truncated


def _get_page(code: str, begin: str, end: str, page: int) -> dict:
    response = requests.get(
        ENDPOINT,
        params={
            "industryCode": "*",
            "pageSize": str(PAGE_SIZE),
            "industry": "*",
            "rating": "",
            "ratingChange": "",
            "beginTime": begin,
            "endTime": end,
            "pageNo": str(page),
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": code,
            "rcode": "",
            "p": str(page),
            "pageNum": str(page),
        },
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/report/",
        },
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise AnalystViewError(f"{code}：研报接口返回格式异常")
    return payload


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
            days integer not null,
            data text,
            fetched_at text,
            primary key (code, days)
        )
        """
    )


def _cached_row(code: str, days: int) -> dict | None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
        row = conn.execute(
            f"select * from {TABLE} where code = ? and days = ?",
            (code, days),
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


def _save_row(code: str, days: int, data: dict) -> None:
    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.execute(
                f"""
                insert or replace into {TABLE}
                    (code, days, data, fetched_at)
                values (?, ?, ?, ?)
                """,
                (
                    code,
                    days,
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
def _window_days(days) -> int:
    try:
        return min(max(int(days), MIN_DAYS), MAX_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_DAYS


def _window_text(days: int) -> str:
    return "近1年" if days == 365 else f"近{days}天"


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


def _number(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _rating_text(rows: list[dict]) -> str:
    items = [
        f"{row['rating']} {row['reports']}"
        for row in rows if row.get("rating")
    ]
    return f" / 评级：{' / '.join(items)}" if items else ""


def _target_text(counts: dict) -> str:
    median = counts.get("target_price_median")
    if median is None:
        return ""
    low = counts.get("target_price_low")
    high = counts.get("target_price_high")
    orgs = counts.get("target_price_orgs") or 0
    if low == high:
        return f"目标价：{median:.2f}元（{orgs} 家）"
    return (
        f"目标价：{low:.2f}~{high:.2f}元"
        f"（中位 {median:.2f}，{orgs} 家）"
    )


def _consensus_text(counts: dict) -> str:
    parts = []
    for key in ("consensus_current_year", "consensus_next_year"):
        item = counts.get(key) or {}
        if item.get("eps") is None:
            continue
        parts.append(
            f"{item.get('year')} {item['eps']:.2f} 元"
            f"（{item.get('orgs', 0)} 家）"
        )
    return f"一致预期 EPS：{' / '.join(parts)}" if parts else ""


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，、；： ") + "…"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
