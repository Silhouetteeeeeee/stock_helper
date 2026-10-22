"""演示入口：随机行情驱动界面，用于查看视觉效果。

真实接入时请不要改本文件，而是在自己的数据层里调用
``market_widget.main_window.MarketWidget`` 提供的数据接口。
"""

from __future__ import annotations

import signal
import sys

from PySide6.QtWidgets import QApplication

from stock.utils import get_sina_current_stock_info_list, is_trading_time
from common.stock import *
from .main_window import MarketWidget

INDEX_CODES = [
    "sh000001",  # 上证指数
    "sh000300",  # 沪深300
    "sh000688",  # 科创50
    "sz399006",  # 创业板指
    "sh000905",  # 中证500
    "sh000852",  # 中证1000
]

class DemoFeed:
    """仅用于演示界面效果：生成随机行情，正式接入时请替换为真实数据源。"""

    def __init__(self, window: MarketWidget) -> None:
        self._window = window
        self.is_first = True

    def publish(self) -> None:
        if not self.is_first and not is_trading_time():
            print("当前非交易时间")
            return
        print("刷新数据")
        tmp = INDEX_CODES.copy()
        tmp.extend([item.code for item in self._window.watchlist_items()])
        quotes = get_sina_current_stock_info_list(tmp)
        self._window.push_quotes(quotes)
        self._window.set_live(False, "示例数据（未接入真实行情）")
        self.is_first = False

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # Qt 在 Windows 上会吞掉 Ctrl+C，这里恢复默认的 SIGINT 行为让其正常退出。
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = MarketWidget()
    window.show()

    feed = DemoFeed(window)
    window.data_requested.connect(feed.publish)
    feed.publish()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())