"""演示入口：拉取实时行情驱动界面，用于查看视觉效果。

真实接入时请不要改本文件，而是在自己的数据层里调用
``market_widget.main_window.MarketWidget`` 提供的数据接口。
"""

from __future__ import annotations

import signal
import sys
from datetime import datetime, time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication

import stock.utils as su
from stock.utils import (
    CHINA_TZ,
    get_sina_current_stock_info_list,
    is_trading_day,
    is_trading_time,
)
from .main_window import MarketWidget
from .ui import MinutePoint
from .ui.timeshare import SESSION_MINUTES

INDEX_CODES = [
    "sh000001",  # 上证指数
    "sh000300",  # 沪深300
    "sh000688",  # 科创50
    "sz399006",  # 创业板指
    "sh000905",  # 中证500
    "sh000852",  # 中证1000
]

TIMESHARE_CODE = "sh605499"
INTRADAY_REFRESH_MS = 60_000


def _minute_index(ts) -> int:
    """把分钟时间戳映射到分时图的 240 个横坐标。

    09:31 是第一根分钟线，对应 09:30 的位置；
    15:00 是最后一根，对应最右侧。
    """
    minute = ts.hour * 60 + ts.minute
    if minute <= 11 * 60 + 30:
        return max(0, minute - (9 * 60 + 30) - 1)
    return min(SESSION_MINUTES - 1, 120 + max(0, minute - 13 * 60 - 1))


def realtime_intraday(code: str) -> tuple[list[MinutePoint], float]:
    """获取最近交易日的分时点与昨收。"""
    df, pre_close = su.get_stock_minute_price(code)
    points: list[MinutePoint] = []
    total_amount = 0.0
    total_volume = 0.0

    for row in df.itertuples(index=False):
        price = float(row.close)
        volume = float(row.volume)
        amount = float(row.amount)

        # 分时均价 = 累计成交额 / 累计成交量
        if volume > 0 and amount == amount:  # amount == amount 用于排除 NaN
            total_amount += amount
            total_volume += volume
        avg = total_amount / total_volume if total_volume else price

        points.append(
            MinutePoint(
                price=price,
                avg=avg,
                volume=volume,
                minute=_minute_index(row.day),
            )
        )

    return points, pre_close


class _IntradaySignals(QObject):
    finished = Signal(object)  # (list[MinutePoint], float) 或 Exception


class _IntradayWorker(QRunnable):
    """后台抓取分时数据，避免网络请求阻塞界面。"""

    def __init__(self, code: str) -> None:
        super().__init__()
        self._code = code
        self.signals = _IntradaySignals()

    def run(self) -> None:
        try:
            result = realtime_intraday(self._code)
        except Exception as exc:
            result = exc
        self.signals.finished.emit(result)


class DemoFeed(QObject):
    """仅用于演示界面效果：定时拉取行情，正式接入时请替换为你的数据层。"""

    def __init__(self, window: MarketWidget) -> None:
        super().__init__(window)
        self._window = window
        self.is_first = True
        self._first_intraday = True
        self._busy = False

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        self._intraday_timer = QTimer(self)
        self._intraday_timer.setInterval(INTRADAY_REFRESH_MS)
        self._intraday_timer.timeout.connect(self.refresh_intraday)

    def start(self) -> None:
        """启动分时定时刷新，并立刻拉取一次。"""
        self._intraday_timer.start()
        self.refresh_intraday()

    def publish(self) -> None:
        if not self.is_first and not is_trading_time():
            print("当前非交易时间")
            return
        print("刷新数据")
        tmp = INDEX_CODES.copy()
        tmp.extend([item.code for item in self._window.watchlist_items()])
        quotes = get_sina_current_stock_info_list(tmp)
        self._window.push_quotes(quotes)
        self._window.set_live(bool(quotes), "实时行情" if quotes else "行情获取失败")
        self.is_first = False

    def refresh_intraday(self) -> None:
        """每分钟刷新一次分时图；首次加载和收盘后补刷不受交易时段限制。"""
        if self._busy:
            return

        if not self._first_intraday:
            now = datetime.now(CHINA_TZ)
            in_close_window = (
                is_trading_day(now.date())
                and time(15, 0) < now.time() <= time(15, 5)
            )
            if not is_trading_time(now) and not in_close_window:
                return

        self._busy = True
        worker = _IntradayWorker(TIMESHARE_CODE)
        worker.signals.finished.connect(self._on_intraday_ready)
        self._pool.start(worker)

    def _on_intraday_ready(self, result: object) -> None:
        self._busy = False
        if isinstance(result, Exception):
            print(f"分时刷新失败: {result}")
            return

        points, pre_close = result
        if not points:
            return

        self._first_intraday = False
        self._window.set_timeshare(points, pre_close)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # Qt 在 Windows 上会吞掉 Ctrl+C，这里恢复默认的 SIGINT 行为让其正常退出。
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    window = MarketWidget()
    window.show()

    feed = DemoFeed(window)
    window.data_requested.connect(feed.publish)
    feed.start()
    feed.publish()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
