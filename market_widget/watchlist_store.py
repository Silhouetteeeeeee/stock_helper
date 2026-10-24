"""界面侧自选股适配层：数据库操作全部在 ``stock.db_service``。

本文件只负责把 ``Stock`` 转成界面使用的 ``WatchItem``，并在读取失败时
退回 ``DEFAULT_WATCHLIST``，不包含任何 SQL 或连接管理。
"""

from __future__ import annotations

from common.stock import Stock

from stock.db_service import (
    WatchlistError,
    add_to_watchlist as add_stock_to_watchlist,
    get_watchlist,
    remove_from_watchlist as remove_stock_from_watchlist,
)
from .config import DEFAULT_WATCHLIST, WatchItem

__all__ = [
    "WatchlistError",
    "add_to_watchlist",
    "load_watchlist",
    "remove_from_watchlist",
]


def load_watchlist() -> list[WatchItem]:
    """读取自选股；首次运行时把 config 中的初始列表写入数据库。"""
    try:
        stocks = get_watchlist([item.code for item in DEFAULT_WATCHLIST])
    except WatchlistError as exc:
        print(exc)
        return list(DEFAULT_WATCHLIST)
    return [_to_watch_item(stock) for stock in stocks]


def add_to_watchlist(code: str) -> WatchItem:
    """加入自选股；失败时抛出 ``WatchlistError``。"""
    return _to_watch_item(add_stock_to_watchlist(code))


def remove_from_watchlist(code: str) -> WatchItem | None:
    """从自选股移除；本来就不在自选股时返回 ``None``。"""
    stock = remove_stock_from_watchlist(code)
    return None if stock is None else _to_watch_item(stock)


def _to_watch_item(stock: Stock) -> WatchItem:
    return WatchItem(code=stock.code, name=stock.name)
