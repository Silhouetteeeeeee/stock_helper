"""财报同步调度器：披露日历 → 待披露队列 → 自动抓取三表。

调度逻辑
--------
* 每 10 分钟唤醒一次（本地 SQLite 查询，开销可忽略）
* 07:00 之前不工作，夜里没有披露
* 日历刷新：队列里有"预约日已到但未披露"的股票时每 30 分钟一次，
  否则每 6 小时一次
* 日历刷新后，把"已实际披露但本地没有财报数据"的股票依次同步

网络请求全部在 QThreadPool 里执行，不阻塞界面；结果通过信号回传。
``poll_once(blocking=True)`` 用于命令行和测试，直接在当前线程执行。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from . import disclosure_service, finance_service
from .db_service import get_watchlist
from .utils import CHINA_TZ

TICK_MS = 10 * 60 * 1000          # 调度器唤醒间隔
ACTIVE_START = time(7, 0)         # 在此之前不发起任何网络请求
DISCLOSURE_POLL = timedelta(minutes=30)   # 有股票待披露时的日历刷新间隔
IDLE_POLL = timedelta(hours=6)            # 平时刷新间隔


def is_active_hours(now: datetime) -> bool:
    """夜间不工作：A 股财报不会在凌晨披露。"""
    return now.time() >= ACTIVE_START


def calendar_refresh_due(
    now: datetime,
    last_refresh: datetime | None,
    has_due_today: bool,
) -> bool:
    """是否该刷新披露日历。披露日当天加密到每 30 分钟一次。"""
    if last_refresh is None:
        return True
    interval = DISCLOSURE_POLL if has_due_today else IDLE_POLL
    return now - last_refresh >= interval


class _JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _CalendarJob(QRunnable):
    def __init__(self, today: date) -> None:
        super().__init__()
        self._today = today
        self.signals = _JobSignals()

    def run(self) -> None:
        try:
            result = disclosure_service.refresh_active_periods(self._today)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


class _SyncJob(QRunnable):
    def __init__(self, codes: list[str]) -> None:
        super().__init__()
        self._codes = codes
        self.signals = _JobSignals()

    def run(self) -> None:
        done: list[str] = []
        failed: dict[str, str] = {}
        for code in self._codes:
            try:
                finance_service.sync_statements(code)
                done.append(code)
            except Exception as exc:
                failed[code] = str(exc)
        self.signals.finished.emit({"status": "synced", "codes": done, "failed": failed})


class FinanceScheduler(QObject):
    """把披露日历和财报同步串起来的后台调度器。"""

    calendar_refreshed = Signal(dict)      # {报告期: 写入行数}
    financials_updated = Signal(str)       # 已同步完成的股票代码
    sync_finished = Signal(dict)           # {"done": [...], "failed": {...}}
    error = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(parent)
        self._clock = clock or (lambda: datetime.now(CHINA_TZ))
        self._last_calendar_refresh: datetime | None = None
        self._busy = False
        self._stopped = False

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.poll_once)

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        """启动定时器并立即执行一次。"""
        self._stopped = False
        self._timer.start()
        self.poll_once()

    def stop(self) -> None:
        """停止定时器；已在执行的请求跑完就不再继续。"""
        self._stopped = True
        self._timer.stop()
        self._pool.clear()

    # -- 调度决策 ---------------------------------------------------------
    def poll_once(self, blocking: bool = False) -> dict:
        """执行一次调度决策，返回本次做了什么，便于日志和测试。

        ``blocking=True`` 时在当前线程同步执行，供命令行和测试使用。
        """
        if self._busy:
            return {"status": "skipped", "reason": "busy"}

        now = self._clock()
        if not is_active_hours(now):
            return {"status": "skipped", "reason": "quiet_hours"}

        has_due = self._has_due_today(now)
        if calendar_refresh_due(now, self._last_calendar_refresh, has_due):
            if blocking:
                return self._refresh_calendar_blocking(now)
            self._busy = True
            self._submit_calendar(now.date())
            return {"status": "calendar_submitted"}

        pending = self._pending_sync_codes(now)
        if not pending:
            return {"status": "idle"}
        if blocking:
            return self._sync_blocking(pending)
        self._busy = True
        self._submit_sync(pending)
        return {"status": "sync_submitted", "codes": pending}

    # -- 同步执行（命令行 / 测试）------------------------------------------
    def _refresh_calendar_blocking(self, now: datetime) -> dict:
        try:
            counts = disclosure_service.refresh_active_periods(now.date())
        except Exception as exc:
            self.error.emit(str(exc))
            return {"status": "error", "reason": str(exc)}

        self._last_calendar_refresh = now
        self.calendar_refreshed.emit(counts)

        result = {"status": "calendar_done", "calendar": counts}
        pending = self._pending_sync_codes(now)
        if pending:
            result.update(self._sync_blocking(pending))
        return result

    def _sync_blocking(self, codes: list[str]) -> dict:
        done: list[str] = []
        failed: dict[str, str] = {}
        for code in codes:
            try:
                finance_service.sync_statements(code)
                done.append(code)
                self.financials_updated.emit(code)
            except Exception as exc:
                failed[code] = str(exc)
                self.error.emit(f"{code}: {exc}")
        result = {"status": "synced", "codes": done, "failed": failed}
        self.sync_finished.emit(result)
        return result

    # -- 异步执行（界面）----------------------------------------------------
    def _submit_calendar(self, today: date) -> None:
        job = _CalendarJob(today)
        job.signals.finished.connect(self._on_calendar_done)
        job.signals.failed.connect(self._on_job_failed)
        self._pool.start(job)

    def _submit_sync(self, codes: list[str]) -> None:
        job = _SyncJob(codes)
        job.signals.finished.connect(self._on_sync_done)
        job.signals.failed.connect(self._on_job_failed)
        self._pool.start(job)

    def _on_calendar_done(self, counts: dict) -> None:
        self._busy = False
        self._last_calendar_refresh = self._clock()
        self.calendar_refreshed.emit(counts)
        self._submit_pending_sync()

    def _submit_pending_sync(self) -> None:
        if self._busy or self._stopped:
            return
        pending = self._pending_sync_codes(self._clock())
        if not pending:
            return
        self._busy = True
        self._submit_sync(pending)

    def _on_sync_done(self, result: dict) -> None:
        self._busy = False
        for code, message in result.get("failed", {}).items():
            self.error.emit(f"{code}: {message}")
        for code in result.get("codes", []):
            self.financials_updated.emit(code)
        self.sync_finished.emit(result)

    def _on_job_failed(self, message: str) -> None:
        self._busy = False
        self.error.emit(message)

    # -- 状态查询 ---------------------------------------------------------
    def _watchlist_codes(self) -> list[str]:
        return [stock.code for stock in get_watchlist()]

    def _has_due_today(self, now: datetime) -> bool:
        codes = self._watchlist_codes()
        if not codes:
            return False
        return bool(disclosure_service.due_today(codes=codes, today=now.date()))

    def _pending_sync_codes(self, now: datetime) -> list[str]:
        codes = self._watchlist_codes()
        if not codes:
            return []
        periods = disclosure_service.disclosure_periods(now.date())
        if not periods:
            return []
        items = disclosure_service.pending_sync(codes=codes, periods=periods)
        return [item["code"] for item in items]


if __name__ == "__main__":
    import sys

    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication(sys.argv)

    scheduler = FinanceScheduler()
    scheduler.error.connect(lambda message: print(f"[错误] {message}"))
    scheduler.calendar_refreshed.connect(lambda counts: print(f"日历已刷新：{counts}"))
    scheduler.financials_updated.connect(lambda code: print(f"财报已更新：{code}"))
    print(scheduler.poll_once(blocking=True))
