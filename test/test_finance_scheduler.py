"""财报调度器验证：时段判断、刷新节奏、待同步触发。"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication

import stock.finance_scheduler as fs
from stock.utils import CHINA_TZ


class FakeDisclosure:
    def __init__(self) -> None:
        self.refresh_calls: list[date] = []
        self.pending_items: list[dict] = []
        self.due_items: list[dict] = []

    def refresh_active_periods(self, today: date) -> dict[str, int]:
        self.refresh_calls.append(today)
        return {"2026-09-30": 5}

    def disclosure_periods(self, today: date) -> list[str]:
        return ["2026-09-30"]

    def pending_sync(self, codes=None, periods=None) -> list[dict]:
        return list(self.pending_items)

    def due_today(self, codes=None, today=None) -> list[dict]:
        return list(self.due_items)


class FakeFinance:
    def __init__(self) -> None:
        self.synced: list[str] = []
        self.failing: set[str] = set()

    def sync_statements(self, code: str) -> dict[str, int]:
        if code in self.failing:
            raise RuntimeError("数据源超时")
        self.synced.append(code)
        return {"balance": 1}


class FakeStock:
    def __init__(self, code: str) -> None:
        self.code = code


def main() -> None:
    QCoreApplication.instance() or QCoreApplication(sys.argv)

    disclosure = FakeDisclosure()
    finance = FakeFinance()
    fs.disclosure_service = disclosure  # type: ignore[assignment]
    fs.finance_service = finance  # type: ignore[assignment]
    fs.get_watchlist = lambda: [FakeStock("605499"), FakeStock("600036")]  # type: ignore[assignment]

    state = {"now": datetime(2026, 10, 30, 16, 0, tzinfo=CHINA_TZ)}
    scheduler = fs.FinanceScheduler(clock=lambda: state["now"])

    events: list[tuple[str, object]] = []
    scheduler.calendar_refreshed.connect(lambda counts: events.append(("calendar", counts)))
    scheduler.financials_updated.connect(lambda code: events.append(("updated", code)))
    scheduler.sync_finished.connect(lambda result: events.append(("sync", result)))
    scheduler.error.connect(lambda message: events.append(("error", message)))

    # -- 纯函数 ---------------------------------------------------------
    assert fs.is_active_hours(datetime(2026, 10, 30, 7, 0, tzinfo=CHINA_TZ))
    assert not fs.is_active_hours(datetime(2026, 10, 30, 6, 59, tzinfo=CHINA_TZ))

    base = datetime(2026, 10, 30, 16, 0, tzinfo=CHINA_TZ)
    assert fs.calendar_refresh_due(base, None, False)
    assert not fs.calendar_refresh_due(base + timedelta(minutes=10), base, True)
    assert fs.calendar_refresh_due(base + timedelta(minutes=30), base, True)
    assert not fs.calendar_refresh_due(base + timedelta(hours=5), base, False)
    assert fs.calendar_refresh_due(base + timedelta(hours=6), base, False)

    # -- 夜间不工作 ------------------------------------------------------
    state["now"] = datetime(2026, 10, 30, 3, 0, tzinfo=CHINA_TZ)
    assert scheduler.poll_once(blocking=True)["reason"] == "quiet_hours"
    assert disclosure.refresh_calls == []

    # -- 首次运行：刷新日历 + 同步已披露但缺数据的股票 --------------------
    state["now"] = datetime(2026, 10, 30, 16, 0, tzinfo=CHINA_TZ)
    disclosure.pending_items = [{"code": "605499"}]
    result = scheduler.poll_once(blocking=True)
    assert disclosure.refresh_calls == [date(2026, 10, 30)], disclosure.refresh_calls
    assert finance.synced == ["605499"], finance.synced
    assert result["status"] == "synced", result
    assert result["calendar"] == {"2026-09-30": 5}
    assert ("calendar", {"2026-09-30": 5}) in events
    assert ("updated", "605499") in events

    # -- 队列清空后既不刷新也不同步 --------------------------------------
    disclosure.pending_items = []
    state["now"] += timedelta(minutes=5)
    assert scheduler.poll_once(blocking=True)["status"] == "idle"
    assert len(disclosure.refresh_calls) == 1

    # -- 有股票预约日已到：30 分钟后就该再刷新 ---------------------------
    disclosure.due_items = [{"code": "605499"}]
    state["now"] += timedelta(minutes=26)          # 距上次刷新 31 分钟
    assert scheduler.poll_once(blocking=True)["status"] == "calendar_done"
    assert len(disclosure.refresh_calls) == 2

    # -- 都已披露后回到 6 小时节奏 ---------------------------------------
    disclosure.due_items = []
    state["now"] += timedelta(minutes=9)           # 距上次刷新 9 分钟
    scheduler.poll_once(blocking=True)
    assert len(disclosure.refresh_calls) == 2

    state["now"] += timedelta(hours=6)             # 距上次刷新超过 6 小时
    scheduler.poll_once(blocking=True)
    assert len(disclosure.refresh_calls) == 3

    # -- 忙时跳过 -------------------------------------------------------
    scheduler._busy = True
    assert scheduler.poll_once()["reason"] == "busy"
    scheduler._busy = False

    # -- 单只股票失败不影响其它股票，并回报错误 --------------------------
    disclosure.pending_items = [{"code": "600036"}, {"code": "605499"}]
    finance.synced = []
    finance.failing = {"600036"}
    state["now"] = datetime(2026, 10, 31, 9, 0, tzinfo=CHINA_TZ)
    result = scheduler.poll_once(blocking=True)
    assert result["codes"] == ["605499"], result
    assert "600036" in result["failed"], result
    assert ("error", "600036: 数据源超时") in events

    # -- 停掉之后，回调不再触发新的同步 --------------------------------
    scheduler.stop()
    finance.synced = []
    scheduler._on_calendar_done({"2026-09-30": 1})
    assert finance.synced == [], finance.synced

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
