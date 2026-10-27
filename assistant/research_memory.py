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
HYPOTHESIS_STATUSES = ("待验证", "部分支持", "已证实", "已证伪")
CONFIDENCE_LEVELS = ("低", "中", "高")
CONCLUSION_STATUSES = ("有效", "已推翻")
IMPORTANCE_LEVELS = ("高", "中", "低")
QUESTION_STATUSES = ("待处理", "调查中", "已解决", "暂时无法解决")
OPEN_QUESTION_STATUSES = ("待处理", "调查中")
MAX_FIELD_BYTES = 20 * 1024
MAX_RECORD_BYTES = 50 * 1024

_UPDATE_ALLOWLISTS = {
    "facts": {"verification_status", "source_locator", "source_url"},
    "hypotheses": {"status", "confidence", "next_review_date"},
    "questions": {"status", "last_checked_at", "resolution_note"},
}

_TABLES = {
    "facts": ("memory_fact", "fact_id", "verification_status", "created_at DESC, id DESC"),
    "conclusions": ("memory_conclusion", "conclusion_id", "status", "updated_at DESC, id DESC"),
    "hypotheses": (
        "memory_hypothesis", "hypothesis_id", "status",
        "(next_review_date IS NULL), next_review_date ASC, updated_at DESC, id DESC",
    ),
    "questions": (
        "memory_question", "question_id", "status",
        "CASE importance WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END, "
        "last_checked_at ASC, id ASC",
    ),
}

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

CREATE TABLE IF NOT EXISTS memory_hypothesis (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  code                  TEXT NOT NULL,
  hypothesis_id         TEXT NOT NULL,
  statement             TEXT NOT NULL,
  supporting_fact_ids_json TEXT NOT NULL DEFAULT '[]',
  assumptions           TEXT NOT NULL DEFAULT '',
  confidence            TEXT NOT NULL,
  verification_method   TEXT NOT NULL,
  falsification_condition TEXT NOT NULL,
  status                TEXT NOT NULL,
  next_review_date      TEXT,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  UNIQUE (code, hypothesis_id)
);

CREATE INDEX IF NOT EXISTS idx_hypothesis ON memory_hypothesis(code, status, next_review_date);

CREATE TABLE IF NOT EXISTS memory_conclusion (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  code                  TEXT NOT NULL,
  conclusion_id         TEXT NOT NULL,
  title                 TEXT NOT NULL,
  statement             TEXT NOT NULL,
  status                TEXT NOT NULL,
  confidence            TEXT NOT NULL DEFAULT '',
  based_on_fact_ids_json TEXT NOT NULL DEFAULT '[]',
  based_on_hypothesis_ids_json TEXT NOT NULL DEFAULT '[]',
  last_change_id        TEXT,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  UNIQUE (code, conclusion_id)
);

CREATE INDEX IF NOT EXISTS idx_conclusion ON memory_conclusion(code, status);

CREATE TABLE IF NOT EXISTS memory_question (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  code                  TEXT NOT NULL,
  question_id           TEXT NOT NULL,
  question              TEXT NOT NULL,
  importance            TEXT NOT NULL,
  known_facts_json      TEXT NOT NULL DEFAULT '[]',
  missing_evidence      TEXT NOT NULL,
  next_action           TEXT NOT NULL,
  resolution_criteria   TEXT NOT NULL,
  related_hypothesis_ids_json TEXT NOT NULL DEFAULT '[]',
  status                TEXT NOT NULL,
  last_checked_at       TEXT,
  resolution_note       TEXT NOT NULL DEFAULT '',
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  UNIQUE (code, question_id)
);

CREATE INDEX IF NOT EXISTS idx_question ON memory_question(code, status, importance);
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


def _load_list(raw: str | None) -> list:
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _hypothesis_row(row: sqlite3.Row) -> dict:
    return {
        "hypothesis_id": row["hypothesis_id"],
        "statement": row["statement"],
        "supporting_fact_ids": _load_list(row["supporting_fact_ids_json"]),
        "assumptions": row["assumptions"],
        "confidence": row["confidence"],
        "verification_method": row["verification_method"],
        "falsification_condition": row["falsification_condition"],
        "status": row["status"],
        "next_review_date": row["next_review_date"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _conclusion_row(row: sqlite3.Row) -> dict:
    return {
        "conclusion_id": row["conclusion_id"],
        "title": row["title"],
        "statement": row["statement"],
        "status": row["status"],
        "confidence": row["confidence"],
        "based_on_fact_ids": _load_list(row["based_on_fact_ids_json"]),
        "based_on_hypothesis_ids": _load_list(row["based_on_hypothesis_ids_json"]),
        "last_change_id": row["last_change_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _question_row(row: sqlite3.Row) -> dict:
    return {
        "question_id": row["question_id"],
        "question": row["question"],
        "importance": row["importance"],
        "known_facts": _load_list(row["known_facts_json"]),
        "missing_evidence": row["missing_evidence"],
        "next_action": row["next_action"],
        "resolution_criteria": row["resolution_criteria"],
        "related_hypothesis_ids": _load_list(row["related_hypothesis_ids_json"]),
        "status": row["status"],
        "last_checked_at": row["last_checked_at"],
        "resolution_note": row["resolution_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_hypothesis(record: dict) -> dict:
    if not isinstance(record, dict):
        raise MemoryError("record 必须是对象")
    hypothesis = {
        "statement": _text(record.get("statement"), "statement", required=True),
        "supporting_fact_ids": _ref_ids(
            record.get("supporting_fact_ids"), "supporting_fact_ids", r"F\d{3,}"
        ),
        "assumptions": _text(record.get("assumptions"), "assumptions"),
        "confidence": _enum(record.get("confidence"), "confidence", CONFIDENCE_LEVELS),
        "verification_method": _text(
            record.get("verification_method"), "verification_method", required=True
        ),
        "falsification_condition": _text(
            record.get("falsification_condition"), "falsification_condition", required=True
        ),
        "status": _enum(record.get("status"), "status", HYPOTHESIS_STATUSES),
        "next_review_date": _text(record.get("next_review_date"), "next_review_date") or None,
    }
    if not hypothesis["supporting_fact_ids"]:
        raise MemoryError("假设必须引用至少一条事实")
    _record_size(hypothesis)
    return hypothesis


def _validate_question(record: dict) -> dict:
    if not isinstance(record, dict):
        raise MemoryError("record 必须是对象")
    question = {
        "question": _text(record.get("question"), "question", required=True),
        "importance": _enum(record.get("importance"), "importance", IMPORTANCE_LEVELS),
        "known_facts": _ref_ids(record.get("known_facts"), "known_facts", r"F\d{3,}"),
        "missing_evidence": _text(
            record.get("missing_evidence"), "missing_evidence", required=True
        ),
        "next_action": _text(record.get("next_action"), "next_action", required=True),
        "resolution_criteria": _text(
            record.get("resolution_criteria"), "resolution_criteria", required=True
        ),
        "related_hypothesis_ids": _ref_ids(
            record.get("related_hypothesis_ids"), "related_hypothesis_ids", r"H\d{3,}"
        ),
        "status": _enum(record.get("status"), "status", QUESTION_STATUSES),
        "resolution_note": _text(record.get("resolution_note"), "resolution_note"),
    }
    _record_size(question)
    return question


def _validate_conclusion(record: dict) -> dict:
    if not isinstance(record, dict):
        raise MemoryError("record 必须是对象")
    conclusion = {
        "title": _text(record.get("title"), "title", required=True),
        "statement": _text(record.get("statement"), "statement", required=True),
        "status": _enum(record.get("status"), "status", CONCLUSION_STATUSES, default="有效"),
        "confidence": _enum(record.get("confidence"), "confidence", CONFIDENCE_LEVELS, default=""),
        "based_on_fact_ids": _ref_ids(
            record.get("based_on_fact_ids"), "based_on_fact_ids", r"F\d{3,}"
        ),
        "based_on_hypothesis_ids": _ref_ids(
            record.get("based_on_hypothesis_ids"), "based_on_hypothesis_ids", r"H\d{3,}"
        ),
    }
    _record_size(conclusion)
    return conclusion


def _row_mapper(section: str) -> Callable[[sqlite3.Row], dict]:
    return {
        "facts": _fact_row,
        "hypotheses": _hypothesis_row,
        "conclusions": _conclusion_row,
        "questions": _question_row,
    }[section]


def _enum(value: Any, field: str, allowed: tuple[str, ...], default: str | None = None) -> str:
    text = _text(value, field)
    if not text:
        if default is None:
            raise MemoryError(f"缺少 {field}")
        return default
    if text not in allowed:
        raise MemoryError(f"{field} 只允许 {'/'.join(allowed)}")
    return text


def _ref_ids(value: Any, field: str, pattern: str) -> list[str]:
    items = _json_array(value, field)
    result = []
    for item in items:
        text = str(item if item is not None else "").strip()
        if not re.fullmatch(pattern, text):
            raise MemoryError(f"{field} 含非法编号：{text}")
        result.append(text)
    return result


def _next_id(
    connection: sqlite3.Connection, code: str, table: str, column: str, prefix: str
) -> str:
    row = connection.execute(
        f"SELECT MAX(CAST(SUBSTR({column}, 2) AS INTEGER)) AS n FROM {table} "
        "WHERE code = ?",
        (code,),
    ).fetchone()
    number = int(row["n"]) + 1 if row and row["n"] is not None else 1
    return f"{prefix}{number:03d}"


def _missing_ids(
    connection: sqlite3.Connection, code: str, table: str, column: str, ids: list[str]
) -> list[str]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = connection.execute(
        f"SELECT {column} AS record_id FROM {table} "
        f"WHERE code = ? AND {column} IN ({placeholders})",
        (code, *ids),
    ).fetchall()
    found = {row["record_id"] for row in rows}
    return [item for item in ids if item not in found]


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
        def count(table: str, extra: str = "", params: tuple = ()) -> int:
            row = connection.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE code = ?{extra}",
                (code, *params),
            ).fetchone()
            return int(row["n"])

        return {
            "facts": count("memory_fact"),
            "hypotheses": count("memory_hypothesis"),
            "conclusions": count("memory_conclusion"),
            "questions": count("memory_question"),
            "open_questions": count(
                "memory_question",
                " AND status IN (?, ?)",
                OPEN_QUESTION_STATUSES,
            ),
        }

    def _company_summary(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        active = connection.execute(
            "SELECT conclusion_id, status, statement FROM memory_conclusion "
            "WHERE code = ? AND status = '有效' ORDER BY updated_at DESC, id DESC LIMIT 1",
            (row["code"],),
        ).fetchone()
        conclusion = (
            f"{active['conclusion_id']} [{active['status']}] {active['statement'][:60]}"
            if active else ""
        )
        return {
            "code": row["code"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_reviewed_at": row["last_reviewed_at"],
            "counts": self._counts(connection, row["code"]),
            "conclusion": conclusion,
        }

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
            return self._company_summary(connection, row)

    def companies(self, query: str = "", limit: int = 100) -> list[dict]:
        pattern = f"%{str(query or '').strip()}%"
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM company_memory "
                "WHERE code LIKE ? OR name LIKE ? "
                "ORDER BY updated_at DESC, code ASC LIMIT ?",
                (pattern, pattern, max(1, min(int(limit), 500))),
            ).fetchall()
            return [self._company_summary(connection, row) for row in rows]

    def append(self, code: str, section: str, record: dict, name: str = "") -> dict:
        code = _code(code)
        if section not in SECTIONS:
            raise MemoryError(f"未知模块：{section}")
        if section == "changes":
            raise MemoryError("changes 只能通过 record_conclusion_change 写入")
        if section == "facts":
            return self._append_fact(code, record, name)
        return self._append_section(code, section, record, name)

    def _append_fact(self, code: str, record: dict, name: str = "") -> dict:
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

            fact_id = _next_id(connection, code, "memory_fact", "fact_id", "F")
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

    def _append_section(self, code: str, section: str, record: dict, name: str = "") -> dict:
        if section == "hypotheses":
            data = _validate_hypothesis(record)
            table, column, prefix, mapper = (
                "memory_hypothesis", "hypothesis_id", "H", _hypothesis_row,
            )
            sql = (
                "INSERT INTO memory_hypothesis (code, hypothesis_id, statement, "
                "supporting_fact_ids_json, assumptions, confidence, verification_method, "
                "falsification_condition, status, next_review_date, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )

            def values(record_id: str, now: str) -> tuple:
                return (
                    code, record_id, data["statement"],
                    json.dumps(data["supporting_fact_ids"], ensure_ascii=False),
                    data["assumptions"], data["confidence"], data["verification_method"],
                    data["falsification_condition"], data["status"],
                    data["next_review_date"], now, now,
                )

            checks = (
                ("memory_fact", "fact_id", data["supporting_fact_ids"], "假设引用了不存在的事实："),
            )
        elif section == "questions":
            data = _validate_question(record)
            table, column, prefix, mapper = (
                "memory_question", "question_id", "Q", _question_row,
            )
            sql = (
                "INSERT INTO memory_question (code, question_id, question, importance, "
                "known_facts_json, missing_evidence, next_action, resolution_criteria, "
                "related_hypothesis_ids_json, status, last_checked_at, resolution_note, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )

            def values(record_id: str, now: str) -> tuple:
                return (
                    code, record_id, data["question"], data["importance"],
                    json.dumps(data["known_facts"], ensure_ascii=False),
                    data["missing_evidence"], data["next_action"], data["resolution_criteria"],
                    json.dumps(data["related_hypothesis_ids"], ensure_ascii=False),
                    data["status"], None, data["resolution_note"], now, now,
                )

            checks = (
                ("memory_fact", "fact_id", data["known_facts"], "问题引用了不存在的事实："),
                (
                    "memory_hypothesis", "hypothesis_id", data["related_hypothesis_ids"],
                    "问题引用了不存在的假设：",
                ),
            )
        else:
            data = _validate_conclusion(record)
            table, column, prefix, mapper = (
                "memory_conclusion", "conclusion_id", "G", _conclusion_row,
            )
            sql = (
                "INSERT INTO memory_conclusion (code, conclusion_id, title, statement, "
                "status, confidence, based_on_fact_ids_json, based_on_hypothesis_ids_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )

            def values(record_id: str, now: str) -> tuple:
                return (
                    code, record_id, data["title"], data["statement"], data["status"],
                    data["confidence"],
                    json.dumps(data["based_on_fact_ids"], ensure_ascii=False),
                    json.dumps(data["based_on_hypothesis_ids"], ensure_ascii=False),
                    now, now,
                )

            checks = (
                ("memory_fact", "fact_id", data["based_on_fact_ids"], "结论引用了不存在的事实："),
                (
                    "memory_hypothesis", "hypothesis_id", data["based_on_hypothesis_ids"],
                    "结论引用了不存在的假设：",
                ),
            )

        def operation(connection: sqlite3.Connection) -> dict:
            self._ensure_company(connection, code, name)
            for ref_table, ref_column, ids, message in checks:
                missing = _missing_ids(connection, code, ref_table, ref_column, ids)
                if missing:
                    raise MemoryError(message + "、".join(missing))
            record_id = _next_id(connection, code, table, column, prefix)
            now = _now()
            connection.execute(sql, values(record_id, now))
            connection.execute(
                "UPDATE company_memory SET updated_at = ? WHERE code = ?", (now, code)
            )
            row = connection.execute(
                f"SELECT * FROM {table} WHERE code = ? AND {column} = ?",
                (code, record_id),
            ).fetchone()
            return mapper(row)

        appended = self._write(operation)
        with self._session() as connection:
            counts = self._counts(connection, code)
        return {"record": appended, "created": True, "counts": counts}

    def update(self, code: str, section: str, record_id: str, patch: dict) -> dict:
        code = _code(code)
        if section not in SECTIONS:
            raise MemoryError(f"未知模块：{section}")
        allowed = _UPDATE_ALLOWLISTS.get(section)
        if not allowed:
            raise MemoryError(f"{section} 不允许直接更新")
        if not isinstance(patch, dict) or not patch:
            raise MemoryError("patch 不能为空")
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise MemoryError(f"不允许更新字段：{'、'.join(unknown)}")

        normalized: dict[str, Any] = {}
        for key, value in patch.items():
            if key == "verification_status":
                normalized[key] = _enum(value, key, VERIFICATION_STATUSES)
            elif key == "status":
                statuses = HYPOTHESIS_STATUSES if section == "hypotheses" else QUESTION_STATUSES
                normalized[key] = _enum(value, key, statuses)
            elif key == "confidence":
                normalized[key] = _enum(value, key, CONFIDENCE_LEVELS)
            else:
                normalized[key] = _text(value, key)
        _record_size(normalized)

        table, column, _status_column, _order = _TABLES[section]
        mapper = _row_mapper(section)

        def operation(connection: sqlite3.Connection) -> dict:
            company = connection.execute(
                "SELECT 1 FROM company_memory WHERE code = ?", (code,)
            ).fetchone()
            if company is None:
                raise MemoryError("尚未建档")
            exists = connection.execute(
                f"SELECT 1 FROM {table} WHERE code = ? AND {column} = ?",
                (code, record_id),
            ).fetchone()
            if exists is None:
                raise MemoryError(f"记录不存在：{record_id}")
            now = _now()
            assignments = ", ".join(f"{key} = ?" for key in normalized)
            connection.execute(
                f"UPDATE {table} SET {assignments}, updated_at = ? WHERE code = ? AND {column} = ?",
                (*normalized.values(), now, code, record_id),
            )
            connection.execute(
                "UPDATE company_memory SET updated_at = ? WHERE code = ?", (now, code)
            )
            row = connection.execute(
                f"SELECT * FROM {table} WHERE code = ? AND {column} = ?",
                (code, record_id),
            ).fetchone()
            return mapper(row)

        updated = self._write(operation)
        with self._session() as connection:
            counts = self._counts(connection, code)
        return {"record": updated, "counts": counts}

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
        if section == "changes":
            raise MemoryError("该模块尚未实现")
        table, column, status_column, order = _TABLES[section]
        mapper = _row_mapper(section)
        with self._session() as connection:
            company_row = connection.execute(
                "SELECT * FROM company_memory WHERE code = ?", (code,)
            ).fetchone()
            if company_row is None:
                raise MemoryError("尚未建档")
            clauses = ["code = ?"]
            params: list[Any] = [code]
            if record_id:
                clauses.append(f"{column} = ?")
                params.append(str(record_id))
            if status:
                clauses.append(f"{status_column} = ?")
                params.append(str(status))
            where = " AND ".join(clauses)
            total = int(connection.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params
            ).fetchone()["n"])
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE {where} "
                f"ORDER BY {order} LIMIT ? OFFSET ?",
                (*params, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
            company = self._company_summary(connection, company_row)
        return {
            "company": company,
            "section": section,
            "total": total,
            "records": [mapper(row) for row in rows],
        }


_STORE: ResearchMemoryStore | None = None


def default_store() -> ResearchMemoryStore:
    global _STORE
    if _STORE is None:
        _STORE = ResearchMemoryStore()
    return _STORE
