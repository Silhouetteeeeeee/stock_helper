"""主窗口接线验证：财报调度器随窗口显示启动、随窗口关闭停止。

不用真的 ``window.show()`` 和 ``sendEvent``：offscreen 平台下让 Qt 派发
窗口事件会在进程退出时崩溃（改动前的版本同样如此），和这里要验证的
接线无关，所以直接调用窗口的 showEvent / closeEvent。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import QApplication

import market_widget.main_window as mw


class FakeScheduler(QObject):
    """替身：只记录启停次数，不发任何网络请求。"""

    financials_updated = Signal(str)
    sync_finished = Signal(dict)
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class HeadlessWidget(mw.MarketWidget):
    """跳过依赖真实屏幕的窗口定位（offscreen 下会崩溃）。"""

    def _apply_geometry(self) -> None:
        pass


def main() -> None:
    app = QApplication.instance() or QApplication([])

    mw.FinanceScheduler = FakeScheduler  # type: ignore[assignment]

    window = HeadlessWidget()
    scheduler = window.finance_scheduler
    assert isinstance(scheduler, FakeScheduler), scheduler
    assert scheduler.started == 0, "构造窗口时不应该开始抓取"

    updated: list[str] = []
    statuses: list[str] = []
    window.financials_updated.connect(updated.append)
    window.finance_status.connect(statuses.append)

    # 窗口显示 → 启动调度
    window.showEvent(QShowEvent())
    assert scheduler.started == 1, scheduler.started
    window.start_finance_monitor()
    assert scheduler.started == 1, "重复启动必须无效"

    # 调度器的结果要转发给外部数据层
    scheduler.financials_updated.emit("605499")
    scheduler.sync_finished.emit({"status": "synced", "codes": ["605499"], "failed": {}})
    scheduler.error.emit("数据源超时")
    assert updated == ["605499"], updated
    assert statuses == ["已同步 1 只股票的财报", "财报同步失败：数据源超时"], statuses

    # 窗口关闭 → 停止调度
    window.closeEvent(QCloseEvent())
    assert scheduler.stopped == 1, scheduler.stopped

    # 关掉开关时完全不创建调度器，启停调用也不能出错
    mw.FINANCE_MONITOR = False
    window._setup_finance_monitor()
    assert window.finance_scheduler is None
    window.start_finance_monitor()
    window.stop_finance_monitor()

    app.quit()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
