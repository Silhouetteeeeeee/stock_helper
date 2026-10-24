"""调度器异步路径验证：线程池里跑任务，结果以信号回到主线程。"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

import stock.finance_scheduler as fs
from stock.utils import CHINA_TZ

NOW = datetime(2026, 10, 30, 16, 0, tzinfo=CHINA_TZ)


class FakeDisclosure:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.pending_calls = 0

    def refresh_active_periods(self, today: date) -> dict[str, int]:
        time.sleep(0.2)  # 模拟网络耗时，确保回调确实是异步回来的
        self.refresh_calls += 1
        return {"2026-09-30": 5}

    def disclosure_periods(self, today: date) -> list[str]:
        return ["2026-09-30"]

    def pending_sync(self, codes=None, periods=None) -> list[dict]:
        self.pending_calls += 1
        # 只有第一次（日历刷新后）有待同步的股票，避免无限循环
        if self.pending_calls == 1:
            return [{"code": "605499"}]
        return []

    def due_today(self, codes=None, today=None) -> list[dict]:
        return []


class FakeFinance:
    def __init__(self) -> None:
        self.synced: list[str] = []

    def sync_statements(self, code: str) -> dict[str, int]:
        time.sleep(0.1)
        self.synced.append(code)
        return {"balance": 1}


class FakeStock:
    def __init__(self, code: str) -> None:
        self.code = code


def wait(loop: QEventLoop, timeout_ms: int) -> None:
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def main() -> None:
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    disclosure = FakeDisclosure()
    finance = FakeFinance()
    fs.disclosure_service = disclosure  # type: ignore[assignment]
    fs.finance_service = finance  # type: ignore[assignment]
    fs.get_watchlist = lambda: [FakeStock("605499")]  # type: ignore[assignment]

    loop = QEventLoop()
    scheduler = fs.FinanceScheduler(clock=lambda: NOW)
    calendars: list[dict] = []
    synced: list[dict] = []
    updated: list[str] = []
    scheduler.calendar_refreshed.connect(calendars.append)
    scheduler.sync_finished.connect(synced.append)
    scheduler.sync_finished.connect(lambda _result: loop.quit())
    scheduler.financials_updated.connect(updated.append)

    scheduler.start()          # 非阻塞：日历任务提交到线程池
    wait(loop, 5000)

    assert disclosure.refresh_calls == 1, disclosure.refresh_calls
    assert calendars == [{"2026-09-30": 5}], calendars
    assert finance.synced == ["605499"], finance.synced
    assert updated == ["605499"], updated
    assert synced == [{"status": "synced", "codes": ["605499"], "failed": {}}], synced
    assert scheduler._busy is False

    # 回到事件循环后没有残留任务再触发同步
    wait(loop, 300)
    assert finance.synced == ["605499"], finance.synced

    scheduler.stop()
    app.quit()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
