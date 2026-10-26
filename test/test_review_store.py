"""模块 6 存档层验证：写入、覆盖、读取顺序与坏数据跳过。"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.store as store


def _report(trade_date: str, state: str = "震荡") -> dict:
    return {
        "trade_date": trade_date,
        "market": {"state": state},
        "candidates": [{"code": "sh600519", "name": "贵州茅台"}],
    }


def test_save_and_load() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "research.db"
        saved = store.save_report(_report("2026-10-23"), path=path)
        assert saved["trade_date"] == "2026-10-23"
        assert saved["replaced"] is False
        assert saved["saved_at"]

        reports = store.load_reports(path=path)
        assert len(reports) == 1
        assert reports[0]["trade_date"] == "2026-10-23"
        assert reports[0]["market"]["state"] == "震荡"
        assert reports[0]["saved_at"] == saved["saved_at"]


def test_save_overwrites_same_day() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "research.db"
        store.save_report(_report("2026-10-23", "震荡"), path=path)
        second = store.save_report(_report("2026-10-23", "弱势"), path=path)
        assert second["replaced"] is True

        reports = store.load_reports(path=path)
        assert len(reports) == 1
        assert reports[0]["market"]["state"] == "弱势"


def test_load_orders_newest_first_and_limits() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "research.db"
        for day in ("2026-10-20", "2026-10-21", "2026-10-22"):
            store.save_report(_report(day), path=path)
        reports = store.load_reports(limit=2, path=path)
        assert [item["trade_date"] for item in reports] == [
            "2026-10-22", "2026-10-21",
        ]
        assert store.latest_report(path=path)["trade_date"] == "2026-10-22"


def test_bad_payload_is_skipped() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "research.db"
        store.save_report(_report("2026-10-23"), path=path)
        connection = sqlite3.connect(path)
        connection.execute(
            "INSERT INTO daily_reports VALUES (?, ?, ?)",
            ("2026-10-22", "2026-10-22 15:00:00", "not json"),
        )
        connection.commit()
        connection.close()

        reports = store.load_reports(path=path)
        assert [item["trade_date"] for item in reports] == ["2026-10-23"]


def test_missing_trade_date_raises() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "research.db"
        try:
            store.save_report({"market": {}}, path=path)
        except ValueError as error:
            assert "trade_date" in str(error)
        else:
            raise AssertionError("缺少 trade_date 应当报错")


def test_env_override() -> None:
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "custom.db"
        original = os.environ.get("STOCK_RESEARCH_DB")
        os.environ["STOCK_RESEARCH_DB"] = str(target)
        try:
            assert store.db_path() == target
        finally:
            if original is None:
                os.environ.pop("STOCK_RESEARCH_DB", None)
            else:
                os.environ["STOCK_RESEARCH_DB"] = original


def main() -> None:
    test_save_and_load()
    test_save_overwrites_same_day()
    test_load_orders_newest_first_and_limits()
    test_bad_payload_is_skipped()
    test_missing_trade_date_raises()
    test_env_override()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
