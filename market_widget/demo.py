"""演示入口：拉取实时行情驱动界面，用于查看视觉效果。

真实接入时请不要改本文件，而是在自己的数据层里调用
``market_widget.main_window.MarketWidget`` 提供的数据接口。
"""

from __future__ import annotations

import os
import signal
import sys
from datetime import datetime, time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication

import stock.utils as su
from stock.briefing import build_briefing
from stock.utils import (
    CHINA_TZ,
    get_sina_current_stock_info_list,
    is_trading_day,
    is_trading_time,
)
from .config import INDEX_CODES
from .main_window import MarketWidget
from .ui import MinutePoint
from .ui.timeshare import SESSION_MINUTES

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


def _normalize_code(code: str) -> str:
    """把自选股/指数代码统一成小写交易所前缀形式，例如 605499 -> sh605499。"""
    prefix, digits = su.get_stock_prefix(code)
    if prefix == "UNKNOWN" or not digits:
        return code.lower()
    return f"{prefix.lower()}{digits}"


def realtime_intraday(
    code: str,
    is_index: bool = False,
) -> tuple[list[MinutePoint], float]:
    """获取最近交易日的分时点与昨收。

    股票均价用累计成交额 / 累计成交量；
    指数的 amount / volume 是成分股平均股价而非点位，
    因此指数改用累计（点位 × 成交量）/ 累计成交量。
    """
    df, pre_close = su.get_stock_minute_price(_normalize_code(code))
    points: list[MinutePoint] = []
    total_weighted = 0.0
    total_volume = 0.0

    for row in df.itertuples(index=False):
        price = float(row.close)
        volume = float(row.volume)
        amount = float(row.amount)
        weighted = price * volume if is_index else amount

        if volume > 0 and weighted == weighted:  # weighted == weighted 用于排除 NaN
            total_weighted += weighted
            total_volume += volume
        avg = total_weighted / total_volume if total_volume else price

        points.append(
            MinutePoint(
                price=price,
                avg=avg,
                volume=volume,
                minute=_minute_index(row.day),
                amount=amount if amount == amount else 0.0,
            )
        )

    return points, pre_close


class _IntradaySignals(QObject):
    finished = Signal(int, object)  # 请求序号 / (list[MinutePoint], float) 或 Exception


class _IntradayWorker(QRunnable):
    """后台抓取分时数据，避免网络请求阻塞界面。"""

    def __init__(self, code: str, is_index: bool, request_id: int) -> None:
        super().__init__()
        self._code = code
        self._is_index = is_index
        self._request_id = request_id
        self.signals = _IntradaySignals()

    def run(self) -> None:
        try:
            result = realtime_intraday(self._code, self._is_index)
        except Exception as exc:
            result = exc
        self.signals.finished.emit(self._request_id, result)


class _BriefingSignals(QObject):
    finished = Signal(str, object)  # 股票代码 / briefing 字典或 Exception


class _BriefingWorker(QRunnable):
    """后台生成基本面速览：财报缓存没命中时会联网抓取，不能放在界面线程。"""

    def __init__(self, code: str) -> None:
        super().__init__()
        self._code = code
        self.signals = _BriefingSignals()

    def run(self) -> None:
        try:
            result = build_briefing(self._code)
        except Exception as exc:
            result = exc
        self.signals.finished.emit(self._code, result)


class DemoFeed(QObject):
    """仅用于演示界面效果：定时拉取行情，正式接入时请替换为你的数据层。"""

    def __init__(self, window: MarketWidget) -> None:
        super().__init__(window)
        self._window = window
        self.is_first = True
        self._first_intraday = True
        self._busy = False
        self._last_codes: tuple[str, ...] = ()   # 上次成功刷新的标的集合

        self._code, self._name, self._is_index = window.selected_symbol()
        self._request_id = 0

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        # 基本面速览：抓一次缓存一份，财报更新后由 financials_updated 清除
        self._briefings: dict[str, dict] = {}
        self._briefing_pending: set[str] = set()
        self._briefing_pool = QThreadPool(self)
        self._briefing_pool.setMaxThreadCount(1)

        self._intraday_timer = QTimer(self)
        self._intraday_timer.setInterval(INTRADAY_REFRESH_MS)
        self._intraday_timer.timeout.connect(self.refresh_intraday)

    def start(self) -> None:
        """启动分时定时刷新，并立刻拉取一次。"""
        self._intraday_timer.start()
        self.refresh_intraday()
        # 窗口构造时就选好了默认标的，那时还没有监听者，这里补一次
        if self._code and not self._is_index:
            self.request_briefing(self._code)

    def set_symbol(self, code: str, name: str, is_index: bool) -> None:
        """界面切换选中标的后立即拉取对应分时，并让旧请求的结果失效。"""
        self._code = code
        self._name = name
        self._is_index = is_index
        self._request_id += 1
        self._busy = False
        self._first_intraday = True
        # 清掉尚未执行的旧请求，避免快速切换时排队抓取过期标的
        self._pool.clear()
        self.refresh_intraday()

    def publish(self) -> None:
        codes = list(INDEX_CODES)
        codes.extend(item.code for item in self._window.watchlist_items())
        # 增删自选股是用户主动操作，即使不在交易时段也拉一次，
        # 否则新加的股票要等到下一个交易时段才有价格。
        watchlist_changed = tuple(codes) != self._last_codes
        if not self.is_first and not watchlist_changed and not is_trading_time():
            print("当前非交易时间")
            return
        print("刷新数据")
        quotes = get_sina_current_stock_info_list(codes)
        self._window.push_quotes(quotes)
        self._window.set_live(bool(quotes), "实时行情" if quotes else "行情获取失败")
        self._last_codes = tuple(codes)
        self.is_first = False

    def refresh_intraday(self) -> None:
        """每分钟刷新一次分时图；首次加载和收盘后补刷不受交易时段限制。"""
        if self._busy or not self._code:
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
        self._request_id += 1
        worker = _IntradayWorker(self._code, self._is_index, self._request_id)
        worker.signals.finished.connect(self._on_intraday_ready)
        self._pool.start(worker)

    # -- 基本面 -------------------------------------------------------------
    def request_briefing(self, code: str) -> None:
        """选中股票时取基本面速览：有缓存立即显示，否则放后台抓一次。"""
        if not code:
            return
        cached = self._briefings.get(code)
        if cached is not None:
            self._window.set_fundamentals(code, cached)
            return
        if code in self._briefing_pending:
            return

        self._briefing_pending.add(code)
        worker = _BriefingWorker(code)
        worker.signals.finished.connect(self._on_briefing_ready)
        self._briefing_pool.start(worker)

    def invalidate_briefing(self, code: str) -> None:
        """财报刚同步完，丢掉旧速览，等界面重新请求。"""
        self._briefings.pop(code, None)

    def _on_briefing_ready(self, code: str, result: object) -> None:
        self._briefing_pending.discard(code)
        if isinstance(result, Exception):
            print(f"基本面加载失败（{code}）: {result}")
            self._window.set_fundamentals(code, {
                "available": False,
                "summary": "基本面数据获取失败",
            })
            return
        self._briefings[code] = result
        self._window.set_fundamentals(code, result)

    def _on_intraday_ready(self, request_id: int, result: object) -> None:
        # 切换标的后旧请求可能才返回，直接丢弃，避免覆盖当前曲线
        if request_id != self._request_id:
            return
        self._busy = False
        if isinstance(result, Exception):
            print(f"分时刷新失败（{self._name}）: {result}")
            return

        points, pre_close = result
        if not points:
            return

        self._first_intraday = False
        self._window.set_timeshare(points, pre_close)


def main() -> int:
    from assistant import server as assistant_server
    from assistant.env import load_env

    load_env()   # 从项目根的 .env 读配置（雪球 token 等）
    app = QApplication.instance() or QApplication(sys.argv)
    # Qt 在 Windows 上会吞掉 Ctrl+C，这里恢复默认的 SIGINT 行为让其正常退出。
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    # 不管从哪条路径退出（关窗口、Ctrl+C、app.quit），都停掉 Web 服务
    app.aboutToQuit.connect(assistant_server.stop_server)

    window = MarketWidget()
    window.show()

    feed = DemoFeed(window)
    window.data_requested.connect(feed.publish)
    window.symbol_selected.connect(feed.set_symbol)
    window.fundamentals_requested.connect(feed.request_briefing)
    window.financials_updated.connect(feed.invalidate_briefing)
    feed.start()
    feed.publish()
    code = app.exec()
    assistant_server.stop_server()
    # 后台线程可能还卡在网络请求里，解释器退出会一直等它；这里直接结束进程
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    raise SystemExit(main())
