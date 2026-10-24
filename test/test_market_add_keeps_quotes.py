"""通过真实 MarketWidget 验证：新增自选股不会清空已有行情。"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import stock.db_service as db
from common.stock import Quote
from market_widget.config import WatchItem
from market_widget.main_window import MarketWidget

DB_COPY = os.path.join(os.path.dirname(__file__), "test_stock_ui.db")
REAL_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "stock.db")


def row_texts(panel, code: str) -> tuple[str, str]:
    _, price, change = panel._rows[code]
    return price.text(), change.text()


def fake_quotes(codes: list[str]) -> list[Quote]:
    return [Quote(code=code, name=code, price=10.0, pre_close=9.0) for code in codes]


def main() -> None:
    shutil.copyfile(REAL_DB, DB_COPY)
    db.DB_PATH = DB_COPY

    # 用固定的两只自选股，避免依赖真实库里已有的自选状态
    with sqlite3.connect(DB_COPY) as conn:
        columns = {row[1] for row in conn.execute("pragma table_info(stock)")}
        if "watchlist_order" not in columns:
            conn.execute("alter table stock add column watchlist_order integer")
        conn.execute("update stock set watchlist_order = null")
        for code, order in (("600036", 0), ("601318", 1)):
            updated = conn.execute(
                "update stock set watchlist_order = ? where code = ?", (order, code)
            ).rowcount
            assert updated == 1, code
        assert conn.execute(
            "select 1 from stock where code = '600519'"
        ).fetchone(), "测试库缺少 600519"

    app = QApplication.instance() or QApplication([])
    window = MarketWidget()
    panel = window._watchlist
    items = window.watchlist_items()
    assert items, "自选股为空"
    print("watchlist:", [item.code for item in items])

    # 首次行情
    window.push_quotes(fake_quotes([item.code for item in items]))
    first_code = items[0].code
    before = row_texts(panel, first_code)
    assert before != ("--", "--"), before

    # 不接任何 data_requested 处理器，模拟非交易时段被数据层跳过
    window.add_watch_item(WatchItem("600519", ""))
    print("after add:", row_texts(panel, first_code))
    assert row_texts(panel, first_code) == before, (
        before,
        row_texts(panel, first_code),
    )
    assert row_texts(panel, "600519") == ("--", "--"), row_texts(panel, "600519")

    # 新增的股票能在下一次行情中正常显示
    window.push_quotes(fake_quotes([item.code for item in window.watchlist_items()]))
    assert row_texts(panel, "600519") == ("10.00", "+11.11%"), row_texts(panel, "600519")

    # 数据库里也确实保存了
    with sqlite3.connect(DB_COPY) as conn:
        value = conn.execute(
            "select watchlist_order from stock where code = '600519'"
        ).fetchone()[0]
    assert value is not None, value

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
