"""按公司持久化的研究档案库。

v1 的字段与操作边界见 docs/superpowers/specs/2026-10-27-research-memory-design.md。
写操作都是短事务，模型只通过记录级接口写入，不能改历史变化。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "research_memory.db"
DB_PATH = Path(os.environ.get("MARKET_RESEARCH_MEMORY_DB", DEFAULT_PATH))

SECTIONS = ("conclusions", "facts", "hypotheses", "changes", "questions")
VERIFICATION_STATUSES = ("已核验", "待核验")
MAX_FIELD_BYTES = 20 * 1024
MAX_RECORD_BYTES = 50 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS company_memory (
  code             TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  last_reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_fact (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  code            TEXT NOT NULL,
  fact_id         TEXT NOT NULL,
  subject         TEXT NOT NULL,
  fact_type       TEXT NOT NULL,
  content         TEXT NOT NULL,
  metrics_json    TEXT NOT NULL DEFAULT '[]',
  report_period   TEXT NOT NULL,
  published_at    TEXT,
  source_id       TEXT NOT NULL,
  source_label    TEXT NOT NULL DEFAULT '',
  source_locator  TEXT NOT NULL DEFAULT '',
  source_url      TEXT NOT NULL DEFAULT '',
  verification_status TEXT NOT NULL DEFAULT '已核验',
  dedupe_key      TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (code, fact_id),
  UNIQUE (code, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_fact_company ON memory_fact(code);
"""


class MemoryError(ValueError):
    """可读的研究档案校验错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _code(code: str) -> str:
    value = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", value):
        raise MemoryError("公司代码必须是六位数字")
    return value


def _text(value: Any, field: str, *, required: bool = False) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise MemoryError(f"缺少 {field}")
    if len(text.encode("utf-8")) > MAX_FIELD_BYTES:
        raise MemoryError(f"{field} 超过 20KB")
    return text


def _json_array(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MemoryError(f"{field} 必须是数组")
    return value


def _record_size(record: dict) -> int:
    try:
        payload = json.dumps(record, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as error:
        raise MemoryError(f"记录无法序列化：{error}") from error
    size = len(payload.encode("utf-8"))
    if size > MAX_RECORD_BYTES:
        raise MemoryError("单条记录超过 50KB")
    return size


def _normalize_content(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", normalized).lower()


def _validate_fact(record: dict) -> dict:
    if not isinstance(record, dict):
        raise MemoryError("record 必须是对象")
    subject = _text(record.get("subject"), "subject")
    fact_type = _text(record.get("fact_type"), "fact_type", required=True)
    content = _text(record.get("content"), "content", required=True)
    report_period = _text(record.get("report_period"), "report_period", required=True)
    source_id = _text(record.get("source_id"), "source_id", required=True)
    published_at = _text(record.get("published_at"), "published_at")
    source_label = _text(record.get("source_label"), "source_label")
    source_locator = _text(record.get("source_locator"), "source_locator")
    source_url = _text(record.get("source_url"), "source_url")
    status = _text(record.get("verification_status"), "verification_status") or "已核验"
    if status not in VERIFICATION_STATUSES:
        raise MemoryError("verification_status 只允许 已核验/待核验")

    metrics = []
    for index, metric in enumerate(_json_array(record.get("metrics"), "metrics")):
        if not isinstance(metric, dict):
            raise MemoryError(f"metrics[{index}] 必须是对象")
        name = _text(metric.get("name"), f"metrics[{index}].name", required=True)
        unit = _text(metric.get("unit"), f"metrics[{index}].unit")
        item = {"name": name}
        if "value" in metric and metric["value"] is not None:
            item["value"] = metric["value"]
        if unit:
            item["unit"] = unit
        if metric.get("yoy") is not None:
            item["yoy"] = metric["yoy"]
        metrics.append(item)

    fact = {
        "subject": subject,
        "fact_type": fact_type,
        "content": content,
        "metrics": metrics,
        "report_period": report_period,
        "published_at": published_at or None,
        "source_id": source_id,
        "source_label": source_label,
        "source_locator": source_locator,
        "source_url": source_url,
        "verification_status": status,
    }
    _record_size(fact)
    return fact


def _dedupe_key(fact: dict) -> str:
    if fact["metrics"]:
        parts = sorted(
            f"{item.get('name', '')}|{item.get('value', '')}|{item.get('unit', '')}"
            for item in fact["metrics"]
        )
        raw = "|".join((fact["fact_type"], fact["report_period"], *parts))
    else:
        raw = "|".join((
            fact["fact_type"],
            fact["report_period"],
            _normalize_content(fact["content"]),
        ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _fact_row(row: sqlite3.Row) -> dict:
    try:
        metrics = json.loads(row["metrics_json"] or "[]")
    except (TypeError, ValueError):
        metrics = []
    return {
        "fact_id": row["fact_id"],
        "subject": row["subject"],
        "fact_type": row["fact_type"],
        "content": row["content"],
        "metrics": metrics if isinstance(metrics, list) else [],
        "report_period": row["report_period"],
        "published_at": row["published_at"],
        "source_id": row["source_id"],
        "source_label": row["source_label"],
        "source_locator": row["source_locator"],
        "source_url": row["source_url"],
        "verification_status": row["verification_status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ResearchMemoryStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._session() as connection:
            connection.executescript(_SCHEMA)
            connection.execute("PRAGMA user_version=1")

    def _write(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        for attempt in range(2):
            with self._session() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    result = operation(connection)
                    connection.execute("COMMIT")
                    return result
                except sqlite3.OperationalError as error:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    message = str(error).lower()
                    if attempt == 0 and ("locked" in message or "busy" in message):
                        time.sleep(0.05)
                        continue
                    raise
                except Exception:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise

    def _ensure_company(self, connection: sqlite3.Connection, code: str, name: str = "") -> sqlite3.Row:
        code = _code(code)
        row = connection.execute("SELECT * FROM company_memory WHERE code = ?", (code,)).fetchone()
        now = _now()
        if row is None:
            connection.execute(
                "INSERT INTO company_memory (code, name, created_at, updated_at, last_reviewed_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (code, name.strip() or code, now, now),
            )
            return connection.execute("SELECT * FROM company_memory WHERE code = ?", (code,)).fetchone()
        if name.strip() and not (row["name"] or "").strip():
            connection.execute(
                "UPDATE company_memory SET name = ?, updated_at = ? WHERE code = ?",
                (name.strip(), now, code),
            )
            return connection.execute("SELECT * FROM company_memory WHERE code = ?", (code,)).fetchone()
        return row

    def _counts(self, connection: sqlite3.Connection, code: str) -> dict:
        row = connection.execute("SELECT COUNT(*) AS n FROM memory_fact WHERE code = ?", (code,)).fetchone()
        return {"facts": int(row["n"])}

    def ensure_company(self, code: str, name: str = "") -> dict:
        code = _code(code)

        def operation(connection: sqlite3.Connection) -> None:
            self._ensure_company(connection, code, name)

        self._write(operation)
        result = self.company(code)
        if result is None:  # pragma: no cover - 刚写入不可能读不到
            raise MemoryError("公司建档失败")
        return result

    def company(self, code: str) -> dict | None:
        code = _code(code)
        with self._session() as connection:
            row = connection.execute("SELECT * FROM company_memory WHERE code = ?", (code,)).fetchone()
            if row is None:
                return None
            counts = self._counts(connection, code)
        return {
            "code": row["code"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_reviewed_at": row["last_reviewed_at"],
            "counts": counts,
            "conclusion": "",
        }

    def companies(self, query: str = "", limit: int = 100) -> list[dict]:
        pattern = f"%{str(query or '').strip()}%"
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM company_memory "
                "WHERE code LIKE ? OR name LIKE ? "
                "ORDER BY updated_at DESC, code ASC LIMIT ?",
                (pattern, pattern, max(1, min(int(limit), 500))),
            ).fetchall()
            results = []
            for row in rows:
                results.append({
                    "code": row["code"],
                    "name": row["name"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_reviewed_at": row["last_reviewed_at"],
                    "counts": self._counts(connection, row["code"]),
                    "conclusion": "",
                })
        return results

    def append(self, code: str, section: str, record: dict, name: str = "") -> dict:
        code = _code(code)
        if section not in SECTIONS:
            raise MemoryError(f"未知模块：{section}")
        if section != "facts":
            raise MemoryError("该模块尚未实现")
        fact = _validate_fact(record)
        dedupe_key = _dedupe_key(fact)

        def operation(connection: sqlite3.Connection) -> tuple[dict, bool]:
            company = self._ensure_company(connection, code, name or fact["subject"])
            existing = connection.execute(
                "SELECT * FROM memory_fact WHERE code = ? AND dedupe_key = ?",
                (code, dedupe_key),
            ).fetchone()
            if existing is not None:
                return _fact_row(existing), False

            row = connection.execute(
                "SELECT fact_id FROM memory_fact WHERE code = ? "
                "ORDER BY CAST(SUBSTR(fact_id, 2) AS INTEGER) DESC LIMIT 1",
                (code,),
            ).fetchone()
            next_id = 1 if row is None else int(str(row["fact_id"])[1:]) + 1
            fact_id = f"F{next_id:03d}"
            now = _now()
            subject = fact["subject"] or company["name"] or code
            try:
                connection.execute(
                    "INSERT INTO memory_fact ("
                    "code, fact_id, subject, fact_type, content, metrics_json, report_period, "
                    "published_at, source_id, source_label, source_locator, source_url, "
                    "verification_status, dedupe_key, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        code, fact_id, subject, fact["fact_type"], fact["content"],
                        json.dumps(fact["metrics"], ensure_ascii=False),
                        fact["report_period"], fact["published_at"], fact["source_id"],
                        fact["source_label"], fact["source_locator"], fact["source_url"],
                        fact["verification_status"], dedupe_key, now, now,
                    ),
                )
            except sqlite3.IntegrityError:
                # 并发重复：另一个线程已插入同一 dedupe_key。
                existing = connection.execute(
                    "SELECT * FROM memory_fact WHERE code = ? AND dedupe_key = ?",
                    (code, dedupe_key),
                ).fetchone()
                if existing is not None:
                    return _fact_row(existing), False
                raise
            connection.execute(
                "UPDATE company_memory SET updated_at = ? WHERE code = ?",
                (now, code),
            )
            row = connection.execute(
                "SELECT * FROM memory_fact WHERE code = ? AND fact_id = ?",
                (code, fact_id),
            ).fetchone()
            return _fact_row(row), True

        fact_record, created = self._write(operation)
        with self._session() as connection:
            counts = self._counts(connection, code)
        return {"record": fact_record, "created": created, "counts": counts}

    def read(
        self,
        code: str,
        section: str,
        record_id: str = "",
        status: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        code = _code(code)
        if section not in SECTIONS:
            raise MemoryError(f"未知模块：{section}")
        if section != "facts":
            raise MemoryError("该模块尚未实现")
        with self._session() as connection:
            company_row = connection.execute(
                "SELECT * FROM company_memory WHERE code = ?", (code,)
            ).fetchone()
            if company_row is None:
                raise MemoryError("尚未建档")
            clauses = ["code = ?"]
            params: list[Any] = [code]
            if record_id:
                clauses.append("fact_id = ?")
                params.append(str(record_id))
            if status:
                clauses.append("verification_status = ?")
                params.append(str(status))
            where = " AND ".join(clauses)
            total = int(connection.execute(
                f"SELECT COUNT(*) AS n FROM memory_fact WHERE {where}", params
            ).fetchone()["n"])
            rows = connection.execute(
                f"SELECT * FROM memory_fact WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
            counts = self._counts(connection, code)
            company = {
                "code": company_row["code"],
                "name": company_row["name"],
                "created_at": company_row["created_at"],
                "updated_at": company_row["updated_at"],
                "last_reviewed_at": company_row["last_reviewed_at"],
                "counts": counts,
                "conclusion": "",
            }
        return {
            "company": company,
            "section": section,
            "total": total,
            "records": [_fact_row(row) for row in rows],
        }


_STORE: ResearchMemoryStore | None = None


def default_store() -> ResearchMemoryStore:
    global _STORE
    if _STORE is None:
        _STORE = ResearchMemoryStore()
    return _STORE
