"""财报披露日历与待披露队列。

数据源是东方财富的预约披露时间表（``akshare.stock_yysj_em``），一次请求
返回某个报告期全市场约 5000 家公司的预约日、变更日和实际披露日。

用途是给同步调度器提供触发点：预约日不等于实际日，可能提前也可能延期，
所以队列里同时保留预约日、多次变更日和实际披露日，由调用方决定轮询频率。

* ``refresh_period(period)``        拉取并覆盖某报告期的全市场日历
* ``refresh_active_periods()``      刷新当前披露窗口内的报告期
* ``pending_disclosures(...)``      未来若干天（含逾期未披露）的记录
* ``pending_sync(...)``             已实际披露但本地还没有财报数据的记录
* ``latest_disclosed_period(code)`` 某只股票已实际披露的最新报告期
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Iterable

import akshare as ak
import pandas as pd

from common.db_utils import DB_PATH
from .db_service import get_watchlist, normalize_stock_code

TABLE = "fin_disclosure"
FIN_INCOME_TABLE = "fin_income"

# 预约日在未来这么多天内即进入待披露队列
DEFAULT_LOOKAHEAD_DAYS = 7
# 预约日已过但迟迟未披露的记录，仍然保留在队列里提醒
DEFAULT_LOOKBACK_DAYS = 7

_DATE_COLUMNS = ("首次预约时间", "一次变更日期", "二次变更日期", "三次变更日期", "实际披露时间")
_EFFECTIVE_DATE_SQL = "coalesce(change_3, change_2, change_1, first_scheduled)"


class DisclosureError(Exception):
    """披露日历操作失败，消息可直接展示给用户。"""


def refresh_period(period: str) -> int:
    """拉取并覆盖某个报告期的全市场预约披露日历，返回写入行数。"""
    _validate_period(period)
    df = _fetch_calendar(period)
    rows = _normalize_frame(period, df)
    if not rows:
        return 0

    with closing(_connect()) as conn:
        with conn:
            _ensure_table(conn)
            conn.executemany(
                f"""
                insert or replace into {TABLE} (
                    code, name, report_period, first_scheduled,
                    change_1, change_2, change_3, actual_date, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["code"], row["name"], row["report_period"],
                        row["first_scheduled"], row["change_1"], row["change_2"],
                        row["change_3"], row["actual_date"], row["fetched_at"],
                    )
                    for row in rows
                ],
            )
    return len(rows)


def refresh_active_periods(today: date | None = None) -> dict[str, int]:
    """刷新当前处于披露窗口内的所有报告期，返回 {报告期: 写入行数}。"""
    return {period: refresh_period(period) for period in disclosure_periods(today)}


def disclosure_periods(today: date | None = None) -> list[str]:
    """当前需要关注的报告期。

    提前一个月纳入下一个报告期，这样在披露季开始前就能拿到预约日历，
    排好待披露队列。
    """
    today = today or date.today()
    year, month = today.year, today.month
    periods: list[str] = []

    if 1 <= month <= 4:      # 年报：1~4 月；一季报：3~4 月
        periods.append(f"{year - 1}-12-31")
        if month >= 3:
            periods.append(f"{year}-03-31")
    elif 6 <= month <= 8:    # 中报：6~8 月
        periods.append(f"{year}-06-30")
    elif 9 <= month <= 10:   # 三季报：9~10 月
        periods.append(f"{year}-09-30")
    return periods


def get_disclosure(code: str, period: str) -> dict | None:
    """读取某只股票某个报告期的披露记录。"""
    with closing(_connect()) as conn:
        _ensure_table(conn)
        row = conn.execute(
            f"select * from {TABLE} where code = ? and report_period = ?",
            (normalize_stock_code(code), period),
        ).fetchone()
    return None if row is None else dict(row)


def pending_disclosures(
    codes: Iterable[str] | None = None,
    days: int = DEFAULT_LOOKAHEAD_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: date | None = None,
) -> list[dict]:
    """待披露队列：预约日在窗口内、且尚未实际披露的记录。

    包含两类：未来 ``days`` 天内要披露的，以及预约日已过 ``lookback_days``
    天仍未披露的（可能延期）。``codes`` 为空表示不限制股票。
    """
    today = today or date.today()
    start = (today - timedelta(days=lookback_days)).isoformat()
    end = (today + timedelta(days=days)).isoformat()

    sql = f"""
        select * from {TABLE}
        where actual_date is null
          and {_EFFECTIVE_DATE_SQL} between ? and ?
    """
    params: list[str] = [start, end]

    code_list = _normalize_codes(codes)
    if code_list:
        sql += f" and code in ({', '.join('?' for _ in code_list)})"
        params.extend(code_list)

    sql += f" order by {_EFFECTIVE_DATE_SQL}, code"

    with closing(_connect()) as conn:
        _ensure_table(conn)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def pending_for_watchlist(
    days: int = DEFAULT_LOOKAHEAD_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: date | None = None,
) -> list[dict]:
    """自选股的待披露队列。"""
    codes = [stock.code for stock in get_watchlist()]
    if not codes:
        return []
    return pending_disclosures(codes, days=days, lookback_days=lookback_days, today=today)


def pending_sync(
    codes: Iterable[str] | None = None,
    periods: Iterable[str] | None = None,
) -> list[dict]:
    """已实际披露、但本地财报库里还没有该报告期数据的记录。

    调度器拿到这批记录后直接调用 ``finance_service.sync_statements`` 即可。
    """
    periods = list(periods) if periods is not None else disclosure_periods()
    if not periods:
        return []

    sql = f"""
        select d.* from {TABLE} d
        where d.actual_date is not null
          and d.report_period in ({', '.join('?' for _ in periods)})
    """
    params: list[str] = list(periods)

    code_list = _normalize_codes(codes)
    if code_list:
        sql += f" and d.code in ({', '.join('?' for _ in code_list)})"
        params.extend(code_list)

    with closing(_connect()) as conn:
        _ensure_table(conn)
        if not _table_exists(conn, FIN_INCOME_TABLE):
            rows = conn.execute(sql, params).fetchall()
        else:
            sql += f"""
              and not exists (
                  select 1 from {FIN_INCOME_TABLE} i
                  where i.code = d.code and i.report_date = d.report_period
              )
            """
            rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def latest_disclosed_period(code: str, periods: Iterable[str] | None = None) -> str | None:
    """本地日历里该股票最近一个已实际披露的报告期。

    用来判断本地财报库是否落后于披露进度：日历表还没刷新过时返回 None，
    调用方应当退回到按时间判断缓存是否过期。
    """
    sql = f"""
        select max(report_period) as period from {TABLE}
        where code = ? and actual_date is not null
    """
    params: list[str] = [normalize_stock_code(code)]

    period_list = list(periods) if periods is not None else []
    if period_list:
        sql += f" and report_period in ({', '.join('?' for _ in period_list)})"
        params.extend(period_list)

    with closing(_connect()) as conn:
        _ensure_table(conn)
        row = conn.execute(sql, params).fetchone()
    return row["period"] if row and row["period"] else None


def due_today(
    codes: Iterable[str] | None = None,
    today: date | None = None,
) -> list[dict]:
    """预约日已到、尚未实际披露的记录，用于披露日当天的密集轮询。"""
    today = today or date.today()
    sql = f"""
        select * from {TABLE}
        where actual_date is null
          and {_EFFECTIVE_DATE_SQL} <= ?
    """
    params: list[str] = [today.isoformat()]

    code_list = _normalize_codes(codes)
    if code_list:
        sql += f" and code in ({', '.join('?' for _ in code_list)})"
        params.extend(code_list)

    sql += f" order by {_EFFECTIVE_DATE_SQL}, code"

    with closing(_connect()) as conn:
        _ensure_table(conn)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_calendar(period: str) -> pd.DataFrame:
    """单独抽出来，便于测试替换。"""
    try:
        return ak.stock_yysj_em(symbol="沪深A股", date=period.replace("-", ""))
    except Exception as exc:
        raise DisclosureError(f"获取 {period} 披露日历失败：{exc}") from exc


def _normalize_frame(period: str, df: pd.DataFrame) -> list[dict]:
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for _, raw in df.iterrows():
        code = normalize_stock_code(raw.get("股票代码"))
        if not code:
            continue
        row = {
            "code": code,
            "name": _text(raw.get("股票简称")),
            "report_period": period,
            "fetched_at": fetched_at,
        }
        for column, field in zip(
            _DATE_COLUMNS,
            ("first_scheduled", "change_1", "change_2", "change_3", "actual_date"),
        ):
            row[field] = _parse_date(raw.get(column))
        rows.append(row)
    return rows


def _parse_date(value) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value)[:10]
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    return text


def _text(value) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _normalize_codes(codes: Iterable[str] | None) -> list[str]:
    if codes is None:
        return []
    result = []
    for code in codes:
        normalized = normalize_stock_code(code)
        if normalized:
            result.append(normalized)
    return result


def _validate_period(period: str) -> None:
    if period[5:] not in ("03-31", "06-30", "09-30", "12-31"):
        raise DisclosureError(f"报告期必须是季度末日期，收到：{period}")


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        create table if not exists {TABLE} (
            code text not null,
            name text,
            report_period text not null,
            first_scheduled text,
            change_1 text,
            change_2 text,
            change_3 text,
            actual_date text,
            fetched_at text,
            primary key (code, report_period)
        )
        """
    )
    _ensure_columns(
        conn,
        {
            "name": "text",
            "first_scheduled": "text",
            "change_1": "text",
            "change_2": "text",
            "change_3": "text",
            "actual_date": "text",
            "fetched_at": "text",
        },
    )


def _ensure_columns(conn: sqlite3.Connection, wanted: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"pragma table_info({TABLE})")}
    for name, kind in wanted.items():
        if name not in existing:
            conn.execute(f"alter table {TABLE} add column {name} {kind}")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "refresh"

    if command == "refresh":
        counts = refresh_active_periods()
        for period, count in counts.items():
            print(f"{period}: 写入 {count} 条")
    elif command == "queue":
        items = pending_for_watchlist()
        if not items:
            print("自选股暂无待披露记录")
        for item in items:
            scheduled = (
                item["change_3"] or item["change_2"] or item["change_1"]
                or item["first_scheduled"]
            )
            print(f"{item['code']} {item['name']} 预约 {scheduled}（{item['report_period']}）")
    else:
        print("用法: python -m stock.disclosure_service [refresh|queue]")
