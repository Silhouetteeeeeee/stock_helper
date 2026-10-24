"""财报数据服务：抓取三大报表、落库、计算派生指标。

数据源是 ``akshare.stock_financial_report_sina``，按公司返回报告期累计值，
一个公司三次请求即可拿到 2017 年以来的完整三表（资产负债表 147 列、
利润表 83 列、现金流量表 71 列），因此不需要解析 PDF。

对外只暴露结构化数据，调用方（研究助手、UI）不关心 SQL 和字段映射：

* ``sync_statements(code)``  抓取并覆盖某公司的三表与指标
* ``ensure_financials(code)`` 按需读取：缓存新鲜就直接返回，否则先抓一次
* ``get_financials(code)``   读取合并后的历史数据，按报告期升序
* ``get_metrics(code)``      只读派生指标

``ensure_financials`` 会访问网络，界面里请放到后台线程里调用。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import akshare as ak
import pandas as pd

from common.db_utils import DB_PATH
from . import disclosure_service
from .db_service import normalize_stock_code
from .finance_fields import (
    BALANCE,
    CASHFLOW,
    INCOME,
    META_COLUMNS,
    STATEMENTS,
    StatementSpec,
    parse_amount,
    parse_report_date,
    validate_columns,
)
from .finance_metrics import METRIC_FIELDS, build_metrics
from .utils import get_stock_prefix

METRICS_TABLE = "fin_metrics"

# 只保留最近 N 年。更早的报表格式不统一、科目大面积缺失（例如银行 1999 年
# 只有经营现金流），既影响同比也容易让分析误读，默认不纳入。
HISTORY_YEARS = 15

# 本地缓存超过这些天就重新抓取
DEFAULT_CACHE_DAYS = 7
# 日历显示有新报告、但数据源还没跟上时的重试间隔，
# 避免每次调用都白抓三次接口
RETRY_INTERVAL = timedelta(minutes=30)


class FinanceError(Exception):
    """财报数据操作失败，消息可直接展示给用户。"""


def to_sina_symbol(code: str) -> str:
    """把 600036 / SH600036 转成新浪接口需要的 sh600036。"""
    prefix, digits = get_stock_prefix(code)
    if prefix == "UNKNOWN" or not digits:
        raise FinanceError(f"无法识别的股票代码：{code}")
    return f"{prefix.lower()}{digits}"


def fetch_statement(code: str, spec: StatementSpec) -> pd.DataFrame:
    """抓取单张报表的原始 DataFrame。"""
    symbol = to_sina_symbol(code)
    try:
        df = ak.stock_financial_report_sina(stock=symbol, symbol=spec.symbol)
    except Exception as exc:
        raise FinanceError(f"获取 {code} 的{spec.symbol}失败：{exc}") from exc
    if df is None or df.empty:
        raise FinanceError(f"{code} 的{spec.symbol}为空")
    return df


def sync_statements(code: str) -> dict[str, int]:
    """抓取并覆盖写入某公司的三张报表与派生指标，返回各表写入行数。"""
    code = normalize_stock_code(code)
    if not code:
        raise FinanceError("请输入股票代码")

    cutoff = _history_cutoff()
    statements: dict[str, list[dict]] = {}
    for spec in STATEMENTS.values():
        df = fetch_statement(code, spec)
        rows = _normalize_frame(code, spec, df)
        statements[spec.key] = [row for row in rows if row["report_date"] >= cutoff]

    metrics = build_metrics(_merge_by_report_date(statements))
    with closing(_connect()) as conn:
        with conn:
            _ensure_tables(conn)
            counts = {
                spec.key: _replace_rows(conn, spec.table, statements[spec.key], code)
                for spec in STATEMENTS.values()
            }
            _replace_metrics(conn, code, metrics)
    return counts


def get_statement(code: str, key: str) -> list[dict]:
    """读取单张报表，按报告期升序。``key`` 取 balance / income / cashflow。"""
    spec = STATEMENTS.get(key)
    if spec is None:
        raise FinanceError(f"未知报表：{key}")

    with closing(_connect()) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            f"select * from {spec.table} where code = ? order by report_date",
            (normalize_stock_code(code),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_metrics(code: str) -> list[dict]:
    """读取派生指标，按报告期升序。"""
    with closing(_connect()) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            f"select * from {METRICS_TABLE} where code = ? order by report_date",
            (normalize_stock_code(code),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_financials(code: str) -> list[dict]:
    """把三张报表和指标按报告期合并成一行，供分析层直接使用。"""
    code = normalize_stock_code(code)
    with closing(_connect()) as conn:
        _ensure_tables(conn)
        parts = [
            conn.execute(
                f"select * from {table} where code = ?",
                (code,),
            ).fetchall()
            for table in (
                BALANCE.table,
                INCOME.table,
                CASHFLOW.table,
                METRICS_TABLE,
            )
        ]

    merged: dict[str, dict] = {}
    for rows in parts:
        for row in rows:
            item = dict(row)
            report_date = item["report_date"]
            target = merged.setdefault(
                report_date,
                {"code": code, "report_date": report_date},
            )
            target.update(item)
    return [merged[key] for key in sorted(merged)]


def ensure_financials(
    code: str,
    max_age_days: int = DEFAULT_CACHE_DAYS,
    force: bool = False,
) -> list[dict]:
    """按需读取某公司的财报：本地数据够新就直接返回，否则先抓取一次。

    判断是否需要重新抓取看三件事：本地有没有数据、缓存放了多久、
    披露日历里有没有比本地更新的报告期。研究助手问到一只股票时调这个
    函数即可，不需要自己判断什么时候该同步。
    """
    code = normalize_stock_code(code)
    if not code:
        raise FinanceError("请输入股票代码")

    history = get_financials(code)
    if force or _needs_sync(code, history, max_age_days):
        sync_statements(code)
        history = get_financials(code)
    return history


def synced_codes() -> list[str]:
    """已经同步过财报的股票代码。"""
    with closing(_connect()) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            f"select distinct code from {INCOME.table} order by code"
        ).fetchall()
    return [row["code"] for row in rows]


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _needs_sync(code: str, history: list[dict], max_age_days: int) -> bool:
    """本地数据落后到需要重新抓取时返回 True。"""
    if not history:
        return True

    age = _cache_age(history)
    if age is None or age > timedelta(days=max_age_days):
        return True

    # 日历里已披露更新的报告期，说明本地漏了。数据源可能还没跟上，
    # 所以两次尝试之间至少隔 RETRY_INTERVAL，免得每次调用都白抓一遍。
    if age < RETRY_INTERVAL:
        return False
    disclosed = disclosure_service.latest_disclosed_period(code)
    return disclosed is not None and disclosed > history[-1]["report_date"]


def _cache_age(history: list[dict]) -> timedelta | None:
    """本地数据最后一次写入距今多久；没有可用时间戳时返回 None。"""
    stamps = [row.get("fetched_at") for row in history if row.get("fetched_at")]
    if not stamps:
        return None
    try:
        fetched = datetime.strptime(max(stamps), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return datetime.now() - fetched


def _history_cutoff() -> str:
    """历史数据保留的起始日期（含）。"""
    today = date.today()
    try:
        cutoff = today.replace(year=today.year - HISTORY_YEARS)
    except ValueError:  # 2 月 29 日往回推时不存在
        cutoff = today.replace(year=today.year - HISTORY_YEARS, day=28)
    return cutoff.isoformat()


def _normalize_frame(code: str, spec: StatementSpec, df: pd.DataFrame) -> list[dict]:
    """把数据源的中文宽表转成统一字段的行列表。"""
    missing = validate_columns(df.columns, spec)
    if missing:
        # 数据源改列名时立刻报错，避免整列静默变空
        raise FinanceError(
            f"{spec.symbol}缺少字段：{', '.join(missing)}（数据源可能已改版）"
        )

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for _, raw in df.iterrows():
        report_date = parse_report_date(raw.get("报告日"))
        if report_date is None:
            continue

        row: dict[str, Any] = {"code": code, "report_date": report_date}
        for field, aliases in spec.fields.items():
            row[field] = _first_amount(raw, aliases)
        # 银行报表没有"所有者权益合计"，用会计恒等式补出来
        if spec.key == "balance" and row.get("total_equity") is None:
            assets, liabilities = row.get("total_assets"), row.get("total_liabilities")
            if assets is not None and liabilities is not None:
                row["total_equity"] = assets - liabilities
        for field, column in META_COLUMNS.items():
            value = raw.get(column) if column in raw.index else None
            row[field] = parse_report_date(value) if field == "ann_date" else _text(value)
        row["fetched_at"] = fetched_at
        rows.append(row)
    return rows


def _first_amount(raw: pd.Series, aliases: Iterable[str]) -> float | None:
    for alias in aliases:
        if alias in raw.index:
            value = parse_amount(raw[alias])
            if value is not None:
                return value
    return None


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _merge_by_report_date(statements: dict[str, list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for rows in statements.values():
        for row in rows:
            merged.setdefault(row["report_date"], {}).update(row)
    return [merged[key] for key in sorted(merged)]


def _ensure_tables(conn: sqlite3.Connection) -> None:
    for spec in STATEMENTS.values():
        _ensure_statement_table(conn, spec)
    _ensure_metrics_table(conn)


def _ensure_statement_table(conn: sqlite3.Connection, spec: StatementSpec) -> None:
    field_columns = ",\n".join(f"    {field} real" for field in spec.fields)
    conn.execute(
        f"""
        create table if not exists {spec.table} (
            code text not null,
            report_date text not null,
        {field_columns},
            ann_date text,
            currency text,
            audited text,
            statement_type text,
            source text,
            fetched_at text,
            primary key (code, report_date)
        )
        """
    )
    wanted = {field: "real" for field in spec.fields}
    wanted.update({field: "text" for field in META_COLUMNS})
    wanted["fetched_at"] = "text"
    _ensure_columns(conn, spec.table, wanted)


def _ensure_metrics_table(conn: sqlite3.Connection) -> None:
    field_columns = ",\n".join(f"    {field} real" for field in METRIC_FIELDS)
    conn.execute(
        f"""
        create table if not exists {METRICS_TABLE} (
            code text not null,
            report_date text not null,
        {field_columns},
            computed_at text,
            primary key (code, report_date)
        )
        """
    )
    wanted = {field: "real" for field in METRIC_FIELDS}
    wanted["computed_at"] = "text"
    _ensure_columns(conn, METRICS_TABLE, wanted)


def _ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    wanted: dict[str, str],
) -> None:
    """新增字段时补列，保证旧库也能直接升级。"""
    existing = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
    for name, kind in wanted.items():
        if name not in existing:
            conn.execute(f"alter table {table} add column {name} {kind}")


def _replace_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict],
    code: str,
) -> int:
    """整表覆盖某公司的数据，避免上游删除的报告期残留在本地。"""
    conn.execute(f"delete from {table} where code = ?", (code,))
    if not rows:
        return 0
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"insert into {table} ({', '.join(columns)}) values ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )
    return len(rows)


def _replace_metrics(conn: sqlite3.Connection, code: str, rows: list[dict]) -> int:
    conn.execute(f"delete from {METRICS_TABLE} where code = ?", (code,))
    if not rows:
        return 0

    computed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = ["code", "report_date", *METRIC_FIELDS, "computed_at"]
    conn.executemany(
        f"""
        insert into {METRICS_TABLE} ({', '.join(columns)})
        values ({', '.join('?' for _ in columns)})
        """,
        [
            tuple([code, row["report_date"], *[row.get(field) for field in METRIC_FIELDS], computed_at])
            for row in rows
        ],
    )
    return len(rows)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m stock.finance_service 605499 600036 ...")
        raise SystemExit(1)

    for argument in sys.argv[1:]:
        try:
            result = sync_statements(argument)
            history = get_financials(argument)
            print(
                f"{argument}: 写入 {result}，"
                f"{len(history)} 期（{history[0]['report_date']} ~ "
                f"{history[-1]['report_date']}）"
            )
        except FinanceError as error:
            print(f"{argument}: {error}")
