"""业绩预告：公司自己对下一报告期的利润、收入与每股收益区间。

同一份预告会按指标拆成多行（归母净利润、扣非净利润、每股收益、营业
收入等），这里按报告期与公告日归并成一条事件，并保留同一报告期最新
的一次修订。预告是初步测算，可能被修正，最终以定期报告为准。

* ``ensure_forecasts(code)``    按需取数，本地缓存一天
* ``build_forecasts(code)``     生成分析用摘要
* ``forecasts_from_rows(...)``  纯函数，由接口返回生成，便于测试
* ``format_forecasts(...)``     渲染成可读文本

口径：金额为元，每股收益为元/股，同比为百分数。``build_forecasts``
会访问网络，界面里请放到后台线程调用。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime

import requests

from common.db_utils import DB_PATH
from .db_service import get_stock, normalize_stock_code

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_PUBLIC_OP_NEWPREDICT"
PAGE_URL = "https://data.eastmoney.com/bbsj/yjyg.html?code={code}"
SOURCE_LABEL = "东方财富 业绩预告"

TABLE = "company_forecasts"

DEFAULT_CACHE_HOURS = 24
DEFAULT_YEARS = 3
MAX_YEARS = 10
MAX_EVENTS = 30

FINANCE_LABELS = {
    "001": "净利润",
    "002": "利润总额",
    "003": "每股收益",
    "004": "归母净利润",
    "005": "扣非净利润",
    "006": "营业收入",
}
FINANCE_ORDER = ("004", "005", "003", "006", "001", "002")
PER_SHARE_CODES = {"003"}


class ForecastError(Exception):
    """业绩预告数据操作失败，消息可直接展示给用户。"""


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def forecasts_from_rows(
    rows: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """把接口返回的多指标行归并成报告期事件（纯函数）。"""
    if not isinstance(rows, (list, tuple)):
        raise ForecastError(f"{code}：业绩预告接口返回格式异常")

    metrics = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric = _normalize_row(row)
        if metric is not None:
            metrics.append(metric)
    return forecasts_from_events(metrics, code, years=years, today=today)


def forecasts_from_events(
    metrics: list[dict],
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
) -> dict:
    """由已规范化的指标行生成事件、年度与类型汇总（纯函数）。"""
    if not isinstance(metrics, (list, tuple)):
        raise ForecastError(f"{code}：业绩预告记录格式异常")

    years = _window_years(years)
    today_date = _parse_date(today) or date.today()
    start = f"{today_date.year - years + 1}-01-01"
    end = today_date.isoformat()

    all_events = _group(metrics)
    window = [
        event for event in all_events
        if start <= event["notice_date"] <= end
    ]
    latest = all_events[0] if all_events else None
    annual = _annual(window)
    type_counts = _type_counts(window)
    counts = _counts(window, latest)
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
        "has_forecasts": bool(all_events),
        "latest": latest,
        "events": window[:MAX_EVENTS],
        "annual": annual,
        "type_counts": type_counts,
        "events_truncated": max(0, len(window) - MAX_EVENTS),
        "counts": counts,
        "notes": notes,
    }
    data["summary"] = format_forecasts(data, code=code)
    return data


def fetch_forecasts(code: str) -> dict:
    """抓取业绩预告指标行；只缓存规范化后的记录。"""
    code = normalize_stock_code(code)
    if not code:
        raise ForecastError("请输入股票代码")

    try:
        rows = _request(code)
    except ForecastError:
        raise
    except Exception as error:  # noqa: BLE001 - 网络层细节统一折叠
        raise ForecastError(f"获取业绩预告数据失败：{error}") from error

    metrics = [
        metric
        for row in rows
        if isinstance(row, dict)
        for metric in [_normalize_row(row)]
        if metric is not None
    ]
    return {
        "code": code,
        "events": metrics,
        "count": len(metrics),
    }


def ensure_forecasts(
    code: str,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """按需读取业绩预告，缓存过期才重新抓取。

    抓取失败但本地有缓存时返回旧数据并标记 ``stale``。
    """
    code = normalize_stock_code(code)
    if not code:
        raise ForecastError("请输入股票代码")

    cached = _cached_row(code)
    if not force and cached and _age_hours(cached.get("fetched_at")) <= max_age_hours:
        return {"data": cached, "stale": False}

    try:
        data = fetch_forecasts(code)
    except ForecastError:
        if cached:
            return {"data": cached, "stale": True}
        raise

    _save_row(data)
    return {"data": _cached_row(code) or data, "stale": False}


def build_forecasts(
    code: str,
    years: int = DEFAULT_YEARS,
    today: str | None = None,
    max_age_hours: float = DEFAULT_CACHE_HOURS,
    force: bool = False,
) -> dict:
    """取数并补上公司名、来源与摘要。"""
    code = normalize_stock_code(code)
    if not code:
        raise ForecastError("请输入股票代码")

    result = ensure_forecasts(
        code,
        max_age_hours=max_age_hours,
        force=force,
    )
    metrics = result["data"].get("events") or []
    data = forecasts_from_events(metrics, code, years=years, today=today)

    stock = get_stock(code)
    name = stock.name if stock is not None else None
    if not name and data.get("latest"):
        name = data["latest"].get("security_name")
    data["name"] = name
    data["fetched_at"] = result["data"].get("fetched_at") or ""
    data["stale"] = result.get("stale", False)
    data["sources"] = {SOURCE_LABEL: data.get("as_of")}
    data["url"] = PAGE_URL.format(code=code)
    data["summary"] = format_forecasts(
        data,
        code=code,
        name=name,
        as_of=data.get("as_of"),
    )
    return data


def format_forecasts(
    data: dict,
    code: str | None = None,
    name: str | None = None,
    as_of: str | None = None,
) -> str:
    """渲染成可读文本：最新一期 + 指标区间 + 近三年类型分布。"""
    code = code or data.get("code")
    as_of = as_of or data.get("as_of")
    years = data.get("years") or DEFAULT_YEARS
    latest = data.get("latest")
    counts = data.get("counts") or {}
    title = f"{name}({code})" if name else (code or "业绩预告")

    parts = [title, f"业绩预告近{years}年"]
    if as_of:
        parts.append(f"数据截至 {as_of}")
    lines = [" · ".join(parts)]

    if latest and counts.get("events"):
        lines.append(
            f"最新：{latest.get('report_date')}"
            f"（公告 {latest.get('notice_date')}）{latest.get('type')}"
        )
        for metric in latest.get("metrics") or []:
            text = _metric_text(metric)
            if text:
                lines.append(text)
        lines.append(
            f"近{years}年：{counts['events']} 期"
            + _type_text(data.get("type_counts") or [])
        )
    elif latest:
        lines.append(
            f"近{years}年没有已披露的业绩预告；最近一次为 "
            f"{latest.get('report_date')} {latest.get('type')}"
        )
    else:
        lines.append("暂无已披露的业绩预告")

    if data.get("notes"):
        lines.append("提示：" + "；".join(data["notes"][:3]))
    lines.append(f"数据来源：{SOURCE_LABEL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析与归并
# ---------------------------------------------------------------------------
def _normalize_row(row: dict) -> dict | None:
    report_date = _date(row.get("REPORT_DATE"))
    notice_date = _date(row.get("NOTICE_DATE"))
    finance_code = _text(row.get("PREDICT_FINANCE_CODE"))
    if not report_date or not notice_date or not finance_code:
        return None

    lower = _number(row.get("PREDICT_AMT_LOWER"))
    upper = _number(row.get("PREDICT_AMT_UPPER"))
    return {
        "report_date": report_date,
        "notice_date": notice_date,
        "year": int(report_date[:4]),
        "finance_code": finance_code,
        "label": FINANCE_LABELS.get(
            finance_code, _text(row.get("PREDICT_FINANCE")) or finance_code
        ),
        "unit": "per_share" if finance_code in PER_SHARE_CODES else "yuan",
        "lower": lower,
        "upper": upper,
        "yoy_low": _number(row.get("ADD_AMP_LOWER")),
        "yoy_high": _number(row.get("ADD_AMP_UPPER")),
        "type": _text(row.get("PREDICT_TYPE")),
        "prior": _number(row.get("PREYEAR_SAME_PERIOD")),
        "mid": _number(row.get("FORECAST_JZ")),
        "reason": _text(row.get("CHANGE_REASON_EXPLAIN")),
        "security_name": _text(row.get("SECURITY_NAME_ABBR")),
        "key": f"{report_date}|{notice_date}|{finance_code}",
    }


def _group(metrics: list[dict]) -> list[dict]:
    """按报告期 + 公告日归并，再用报告期保留最新修订。"""
    groups: dict[tuple, dict] = {}
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        report_date = metric.get("report_date")
        notice_date = metric.get("notice_date")
        code = metric.get("finance_code")
        if not report_date or not notice_date or not code:
            continue
        key = (report_date, notice_date)
        event = groups.get(key)
        if event is None:
            event = {
                "report_date": report_date,
                "notice_date": notice_date,
                "year": int(report_date[:4]),
                "type": None,
                "reason": None,
                "security_name": None,
                "metrics": {},
            }
            groups[key] = event
        event["metrics"][code] = metric
        if not event["reason"] and metric.get("reason"):
            event["reason"] = metric["reason"]
        if not event["security_name"] and metric.get("security_name"):
            event["security_name"] = metric["security_name"]

    events = []
    for event in groups.values():
        event["metrics"] = _ordered_metrics(event["metrics"])
        event["type"] = _event_type(event["metrics"])
        events.append(event)

    by_report: dict[str, dict] = {}
    for event in events:
        current = by_report.get(event["report_date"])
        if current is None or _event_stamp(event) > _event_stamp(current):
            by_report[event["report_date"]] = event
    return sorted(
        by_report.values(),
        key=lambda item: item["report_date"],
        reverse=True,
    )


def _ordered_metrics(by_code: dict) -> list[dict]:
    order = [
        code for code in FINANCE_ORDER if code in by_code
    ] + [
        code for code in by_code if code not in FINANCE_ORDER
    ]
    return [by_code[code] for code in order]


def _event_type(metrics: list[dict]) -> str | None:
    by_code = {item["finance_code"]: item for item in metrics}
    for code in ("004", "001", "005"):
        if code in by_code and by_code[code].get("type"):
            return by_code[code]["type"]
    for metric in metrics:
        if metric.get("type"):
            return metric["type"]
    return None


def _event_stamp(event: dict) -> tuple:
    return (
        event["notice_date"],
        _completeness(event),
    )


def _completeness(event: dict) -> int:
    metrics = event["metrics"]
    if isinstance(metrics, dict):
        metrics = metrics.values()
    return sum(
        1 for metric in metrics
        if metric.get("lower") is not None or metric.get("upper") is not None
    )


def _annual(events: list[dict]) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for event in events:
        by_year.setdefault(event["year"], []).append(event)
    return [
        {
            "year": year,
            "events": len(by_year[year]),
            "report_dates": sorted(
                {event["report_date"] for event in by_year[year]},
                reverse=True,
            ),
        }
        for year in sorted(by_year)
    ]


def _type_counts(events: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for event in events:
        if event.get("type"):
            counts[event["type"]] = counts.get(event["type"], 0) + 1
    rows = [
        {"type": name, "events": count}
        for name, count in counts.items()
    ]
    rows.sort(key=lambda item: (-item["events"], item["type"]))
    return rows


def _counts(events: list[dict], latest: dict | None) -> dict:
    empty = {
        "events": 0,
        "latest_report_date": None,
        "latest_notice_date": None,
        "latest_type": None,
        "latest_net_profit_low": None,
        "latest_net_profit_high": None,
        "latest_net_profit_yoy_low": None,
        "latest_net_profit_yoy_high": None,
        "latest_eps_low": None,
        "latest_eps_high": None,
        "latest_revenue_low": None,
        "latest_revenue_high": None,
    }
    if latest is None:
        return empty
    by_code = {
        metric["finance_code"]: metric for metric in latest["metrics"]
    }
    net = by_code.get("004") or {}
    eps = by_code.get("003") or {}
    revenue = by_code.get("006") or {}
    return {
        "events": len(events),
        "latest_report_date": latest["report_date"],
        "latest_notice_date": latest["notice_date"],
        "latest_type": latest.get("type"),
        "latest_net_profit_low": net.get("lower"),
        "latest_net_profit_high": net.get("upper"),
        "latest_net_profit_yoy_low": net.get("yoy_low"),
        "latest_net_profit_yoy_high": net.get("yoy_high"),
        "latest_eps_low": eps.get("lower"),
        "latest_eps_high": eps.get("upper"),
        "latest_revenue_low": revenue.get("lower"),
        "latest_revenue_high": revenue.get("upper"),
    }


def _notes(
    all_events: list[dict],
    window: list[dict],
    latest: dict | None,
    years: int,
) -> list[str]:
    if not all_events or latest is None:
        return ["没有取到业绩预告，可能公司未披露或数据源未收录"]

    if not window:
        return [
            f"近{years}年没有已披露的业绩预告；"
            f"最近一次为{latest['report_date']}",
            "预告可能被修订，最终以定期报告为准",
        ]

    notes = [
        "业绩预告是公司初步测算，可能被修订，最终以定期报告为准",
        "预告多为区间或约数，不是确定值，同比基数受非经常性损益影响",
        "只含已披露预告，未覆盖未发布预告的期间",
    ]
    net = next(
        (item for item in latest["metrics"] if item["finance_code"] == "004"),
        None,
    )
    if net and net.get("prior") is not None and net["prior"] < 0 \
            and net.get("yoy_low") is not None:
        notes.append("亏损公司的同比增速方向可能失真")
    return notes


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _request(code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 20:
        payload = _get_page(code, page)
        if payload.get("success") is False:
            message = payload.get("message") or "接口返回失败"
            if payload.get("result") is None and "为空" in message:
                return rows
            raise ForecastError(
                f"{code}：获取业绩预告数据失败（{message}）"
            )
        result = payload.get("result")
        if result is None:
            break
        if not isinstance(result, dict):
            raise ForecastError(f"{code}：业绩预告接口返回格式异常")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise ForecastError(f"{code}：业绩预告接口数据格式异常")
        rows.extend(item for item in data if isinstance(item, dict))
        try:
            pages = int(result.get("pages") or 1)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages:
            break
        page += 1
    return rows


def _get_page(code: str, page: int) -> dict:
    response = requests.get(
        ENDPOINT,
        params={
            "reportName": REPORT_NAME,
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": str(page),
            "pageSize": "100",
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
        raise ForecastError(f"{code}：业绩预告接口返回格式异常")
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


def _number(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_text(metric: dict) -> str | None:
    text = f"{metric.get('label')}：{_range(metric)}"
    if text.endswith("："):
        return None
    yoy = _yoy_text(metric)
    if yoy:
        text += f"，同比 {yoy}"
    return text


def _range(metric: dict) -> str:
    lower = metric.get("lower")
    upper = metric.get("upper")
    unit = metric.get("unit")
    if lower is None and upper is None:
        return ""
    if lower is not None and upper is not None:
        if lower == upper:
            return _value_text(lower, unit)
        return f"{_value_text(lower, unit)}~{_value_text(upper, unit)}"
    value = lower if lower is not None else upper
    return _value_text(value, unit)


def _value_text(value, unit: str | None) -> str:
    if unit == "per_share":
        return f"{value:.4f}元"
    return _amount(value)


def _yoy_text(metric: dict) -> str:
    low = metric.get("yoy_low")
    high = metric.get("yoy_high")
    if low is None and high is None:
        return ""
    if low is not None and high is not None:
        if low == high:
            return _yoy(low)
        return f"{_yoy(low)}~{_yoy(high)}"
    return _yoy(low if low is not None else high)


def _yoy(value) -> str:
    return f"{value:+.1f}%"


def _type_text(rows: list[dict]) -> str:
    items = [
        f"{row['type']} {row['events']}"
        for row in rows if row.get("type")
    ]
    return f"；类型：{' / '.join(items)}" if items else ""


def _amount(value) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:.2f}亿元"
    if value >= 1e4:
        return f"{sign}{value / 1e4:.2f}万元"
    return f"{sign}{value:.2f}元"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
