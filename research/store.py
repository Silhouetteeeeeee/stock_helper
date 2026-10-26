"""模块 6 的存档层：把每日判断写进 SQLite，供之后复盘。

表只有一张：一天一条判断，重复保存覆盖当天记录。存档内容是规则引擎算出的
大盘状态、行业强弱和候选股名单，不保存模型自己写的文字结论——复盘要评估的
是能被事后行情检验的判断，而不是回答的措辞。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "research.db"

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS daily_reports (
    trade_date TEXT PRIMARY KEY,
    saved_at   TEXT NOT NULL,
    payload    TEXT NOT NULL
)
"""


def db_path() -> Path:
    """``STOCK_RESEARCH_DB`` 可以指向别的库，测试用它避免碰真实存档。"""
    override = os.environ.get("STOCK_RESEARCH_DB")
    return Path(override) if override else DEFAULT_PATH


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.execute(TABLE_SQL)
    return connection


def save_report(report: dict, path: str | Path | None = None) -> dict:
    """保存一天的判断；同一天再次保存会覆盖，并说明是否覆盖了旧记录。"""
    trade_date = str((report or {}).get("trade_date") or "").strip()
    if not trade_date:
        raise ValueError("存档缺少 trade_date")
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_connect(path)) as connection:
        replaced = connection.execute(
            "SELECT 1 FROM daily_reports WHERE trade_date = ?", (trade_date,)
        ).fetchone() is not None
        connection.execute(
            "INSERT INTO daily_reports (trade_date, saved_at, payload) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(trade_date) DO UPDATE SET "
            "saved_at = excluded.saved_at, payload = excluded.payload",
            (trade_date, saved_at, payload),
        )
        connection.commit()
    return {"trade_date": trade_date, "saved_at": saved_at, "replaced": replaced}


def load_reports(limit: int = 60, path: str | Path | None = None) -> list[dict]:
    """按交易日倒序返回存档，最新的在前。"""
    size = max(1, int(limit))
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            "SELECT trade_date, saved_at, payload FROM daily_reports "
            "ORDER BY trade_date DESC LIMIT ?",
            (size,),
        ).fetchall()
    reports: list[dict] = []
    for trade_date, saved_at, payload in rows:
        try:
            report = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(report, dict):
            continue
        report["trade_date"] = trade_date
        report["saved_at"] = saved_at
        reports.append(report)
    return reports


def latest_report(path: str | Path | None = None) -> dict | None:
    reports = load_reports(limit=1, path=path)
    return reports[0] if reports else None
