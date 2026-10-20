"""演示入口：随机行情驱动界面，用于查看视觉效果。

真实接入时请不要改本文件，而是在自己的数据层里调用
``market_widget.main_window.MarketWidget`` 提供的数据接口。
"""

from __future__ import annotations

import random
import signal
import sys

from PySide6.QtWidgets import QApplication

from .config import Quote
from .main_window import MarketWidget

class DemoFeed:
    """仅用于演示界面效果：生成随机行情，正式接入时请替换为真实数据源。"""

    def __init__(self, window: MarketWidget) -> None:
        self._window = window

    def publish(self) -> None:
        quotes = [
            Quote("sh000001", "上证指数", 3210.45 + random.uniform(-30, 30), random.uniform(-2, 2)),
            Quote("sz399001", "深证成指", 10120.30 + random.uniform(-80, 80), random.uniform(-2, 2)),
            Quote("sz399006", "创业板指", 2005.80 + random.uniform(-30, 30), random.uniform(-2.5, 2.5)),
        ]
        for item in self._window.watchlist_items():
            quotes.append(Quote(item.code, item.name, random.uniform(8, 200), random.uniform(-5, 5)))
        self._window.push_quotes(quotes)
        self._window.set_live(False, "示例数据（未接入真实行情）")

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