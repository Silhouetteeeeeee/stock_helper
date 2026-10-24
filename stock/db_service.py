from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable

from common.db_utils import DB_PATH
from common.stock import Stock

QUERY_SQL_STOCK = """
    select code, name
    from stock
"""

QUERY_SQL_STOCK_BY_NAME = """
    select code, name
    from stock where name = ?
"""

WATCHLIST_COLUMN = "watchlist_order"

QUERY_SQL_WATCHLIST = f"""
    select code, name
    from stock
    where {WATCHLIST_COLUMN} is not null
    order by {WATCHLIST_COLUMN}, code
"""


class WatchlistError(Exception):
    """自选股操作失败，消息可直接展示给用户。"""


def get_all_stocks() -> list[Stock]:
    with closing(_connect()) as conn:
        rows = conn.execute(QUERY_SQL_STOCK).fetchall()
    return [_row_to_stock(row) for row in rows]


def get_stock_by_name(name: str) -> Stock | None:
    with closing(_connect()) as conn:
        row = conn.execute(QUERY_SQL_STOCK_BY_NAME, (name,)).fetchone()
    return None if row is None else _row_to_stock(row)


# ---------------------------------------------------------------------------
# 自选股：复用 stock 表，watchlist_order 为 NULL 表示不在自选股中
# ---------------------------------------------------------------------------
def normalize_stock_code(code: str) -> str:
    """把 600036 / SH600036 / 600036.SH 统一成六位数字代码。"""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits.zfill(6) if digits else ""


def get_watchlist(seed_codes: Iterable[str] | None = None) -> list[Stock]:
    """读取自选股；首次新增字段时用 seed_codes 初始化默认列表。"""
    try:
        with closing(_connect()) as conn:
            with conn:
                _ensure_watchlist_column(conn, seed_codes)
                rows = conn.execute(QUERY_SQL_WATCHLIST).fetchall()
            return [_row_to_stock(row) for row in rows]
    except sqlite3.Error as exc:
        raise WatchlistError(f"读取自选股失败：{exc}") from exc


def add_to_watchlist(code: str) -> Stock:
    """把股票加入自选股并返回最终条目；失败时抛出 ``WatchlistError``。"""
    code = normalize_stock_code(code)
    if not code:
        raise WatchlistError("请输入股票代码")

    try:
        with closing(_connect()) as conn:
            with conn:
                _ensure_watchlist_column(conn)
                row = conn.execute(
                    f"""
                    select code, name, {WATCHLIST_COLUMN}
                    from stock where code = ? limit 1
                    """,
                    (code,),
                ).fetchone()
                if row is None:
                    raise WatchlistError(f"未找到股票代码 {code}")
                if row[WATCHLIST_COLUMN] is not None:
                    raise WatchlistError("该股票已在自选股中")

                next_order = conn.execute(
                    f"""
                    select coalesce(max({WATCHLIST_COLUMN}) + 1, 0)
                    from stock where {WATCHLIST_COLUMN} is not null
                    """
                ).fetchone()[0]
                conn.execute(
                    f"update stock set {WATCHLIST_COLUMN} = ? where code = ?",
                    (next_order, code),
                )
            return _row_to_stock(row)
    except sqlite3.Error as exc:
        raise WatchlistError(f"自选股保存失败：{exc}") from exc


def remove_from_watchlist(code: str) -> Stock | None:
    """从自选股移除并返回被移除的条目；本来就不在自选股时返回 ``None``。"""
    code = normalize_stock_code(code)
    if not code:
        raise WatchlistError("请输入股票代码")

    try:
        with closing(_connect()) as conn:
            with conn:
                _ensure_watchlist_column(conn)
                row = conn.execute(
                    f"""
                    select code, name, {WATCHLIST_COLUMN}
                    from stock where code = ? limit 1
                    """,
                    (code,),
                ).fetchone()
                if row is None:
                    raise WatchlistError(f"未找到股票代码 {code}")
                if row[WATCHLIST_COLUMN] is None:
                    return None

                conn.execute(
                    f"update stock set {WATCHLIST_COLUMN} = null where code = ?",
                    (code,),
                )
            return _row_to_stock(row)
    except sqlite3.Error as exc:
        raise WatchlistError(f"自选股保存失败：{exc}") from exc


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    """独立连接：写操作需要事务，也避免依赖 db_utils 的单线程全局连接。"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_watchlist_column(
    conn: sqlite3.Connection,
    seed_codes: Iterable[str] | None = None,
) -> None:
    """确保标记字段存在；首次新增字段时写入默认自选股。"""
    columns = {row["name"] for row in conn.execute("pragma table_info(stock)")}
    if not columns:
        raise sqlite3.OperationalError("stock 表不存在")
    if WATCHLIST_COLUMN in columns:
        return

    conn.execute(f"alter table stock add column {WATCHLIST_COLUMN} integer")
    for order, value in enumerate(seed_codes or ()):
        code = normalize_stock_code(value)
        if not code:
            continue
        conn.execute(
            f"update stock set {WATCHLIST_COLUMN} = ? where code = ?",
            (order, code),
        )


def _row_to_stock(row: sqlite3.Row) -> Stock:
    return Stock(code=row["code"], name=row["name"] or row["code"])
