"""非交易时段增删自选股时应允许拉取一次行情。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

import market_widget.demo as demo
from common.stock import Quote
from market_widget.config import WatchItem


class FakeWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.items = [WatchItem("600036", "招商银行")]
        self.pushed: list[Quote] = []
        self.live: tuple[bool, str] | None = None

    def selected_symbol(self) -> tuple[str, str, bool]:
        return "600036", "招商银行", False

    def watchlist_items(self) -> list[WatchItem]:
        return list(self.items)

    def push_quotes(self, quotes: list[Quote]) -> None:
        self.pushed = list(quotes)

    def set_live(self, live: bool, tip: str) -> None:
        self.live = (live, tip)


def main() -> None:
    app = QApplication.instance() or QApplication([])

    calls: list[list[str]] = []

    def fake_quotes(codes: list[str]) -> list[Quote]:
        calls.append(list(codes))
        return [Quote(code=code, name=code, price=1.0, pre_close=1.0) for code in codes]

    demo.get_sina_current_stock_info_list = fake_quotes
    demo.is_trading_time = lambda now=None: False

    window = FakeWindow()
    feed = demo.DemoFeed(window)  # type: ignore[arg-type]

    # 首次启动：非交易时段也要拉一次
    feed.publish()
    assert len(calls) == 1, calls

    # 标的没变：非交易时段不再重复请求
    feed.publish()
    assert len(calls) == 1, calls

    # 新增自选股：即使非交易时段也要拉一次，保证新股票有价格
    window.items.append(WatchItem("600519", "贵州茅台"))
    feed.publish()
    assert len(calls) == 2, calls
    assert "600519" in calls[-1], calls[-1]

    # 再次刷新：标的没变，继续跳过
    feed.publish()
    assert len(calls) == 2, calls

    app.quit()
    print("PASS")


if __name__ == "__main__":
    main()
