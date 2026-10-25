"""对话持久化：会话与消息存 SQLite，页面重启后还能接着聊。

单独一个 ``data/assistant.db``，不和数据层的 ``stock.db`` 混在一起；
数据更新脚本重刷行情库时不会误伤用户对话。每条消息原样保存模型
消息（``raw``），续聊时按顺序回放给 ``ResearchAgent``。

测试用 ``MARKET_ASSISTANT_DB`` 指到临时文件。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "assistant.db"
DB_PATH = Path(os.environ.get("MARKET_ASSISTANT_DB", DEFAULT_PATH))

TITLE_LIMIT = 30
RECENT_LIMIT = 20
BACKUP_KEEP = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    primary_code TEXT NOT NULL DEFAULT '',
    primary_name TEXT NOT NULL DEFAULT '',
    companies TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
    conversation_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
"""


@dataclass(frozen=True)
class Conversation:
    id: int
    title: str
    primary_code: str
    primary_name: str
    companies: tuple[str, ...]
    created_at: str
    updated_at: str
    message_count: int = 0


def build_title(name: str, question: str) -> str:
    """标题：公司简称 + 截断后的问题；名字为空时只留问题。"""
    text = (question or "").strip()
    if len(text) > TITLE_LIMIT:
        text = text[:TITLE_LIMIT] + "…"
    name = (name or "").strip()
    return f"{name} · {text}" if name else text


def _now() -> str:
    """带微秒的本地时间，最近列表的排序靠它区分先后。"""
    return datetime.now().isoformat(timespec="microseconds")


class ConversationStore:
    """单用户的会话存储；每次操作开一个短连接。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._backup()
        self._ensure_schema()

    def _backup(self) -> None:
        """每天第一次打开时留一份副本，并只保留最近几份。"""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        folder = self.path.parent / "backups"
        target = folder / f"{self.path.stem}-{datetime.now():%Y%m%d}.db"
        if target.exists():
            return
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, target)
        backups = sorted(folder.glob(f"{self.path.stem}-*.db"))
        for old in backups[:-BACKUP_KEEP]:
            old.unlink()

    @contextmanager
    def _session(self):
        """用完即关，避免页面反复 rerun 时攒下一堆连接。"""
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._session() as connection:
            connection.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # 会话
    # ------------------------------------------------------------------
    def create(
        self,
        title: str,
        primary_code: str,
        primary_name: str = "",
        companies: tuple[str, ...] = (),
    ) -> int:
        codes = list(dict.fromkeys([primary_code, *companies]))
        now = _now()
        with self._session() as connection:
            cursor = connection.execute(
                "INSERT INTO conversation"
                " (title, primary_code, primary_name, companies, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    title,
                    primary_code,
                    primary_name,
                    json.dumps(codes, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def append(self, conversation_id: int, role: str, payload: dict) -> int:
        now = _now()
        with self._session() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM message WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            seq = int(row[0]) + 1
            connection.execute(
                "INSERT INTO message"
                " (conversation_id, seq, role, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    seq,
                    role,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE conversation SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            return seq

    def messages(self, conversation_id: int) -> list[dict]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT role, payload_json FROM message"
                " WHERE conversation_id = ? ORDER BY seq",
                (conversation_id,),
            ).fetchall()
        messages: list[dict] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            messages.append({"role": row["role"], **payload})
        return messages

    def recent(
        self,
        primary_code: str | None = None,
        query: str = "",
        limit: int = RECENT_LIMIT,
    ) -> list[Conversation]:
        clauses: list[str] = []
        args: list[object] = []
        if primary_code:
            clauses.append("c.primary_code = ?")
            args.append(primary_code)
        query = (query or "").strip()
        if query:
            clauses.append("(c.title LIKE ? OR c.primary_code LIKE ?)")
            args.extend([f"%{query}%", f"%{query}%"])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        with self._session() as connection:
            rows = connection.execute(
                "SELECT c.*,"
                " (SELECT COUNT(*) FROM message m WHERE m.conversation_id = c.id)"
                " AS message_count"
                f" FROM conversation c{where}"
                " ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [
            Conversation(
                id=int(row["id"]),
                title=row["title"],
                primary_code=row["primary_code"],
                primary_name=row["primary_name"],
                companies=tuple(json.loads(row["companies"] or "[]")),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                message_count=int(row["message_count"]),
            )
            for row in rows
        ]

    def delete(self, conversation_id: int) -> None:
        with self._session() as connection:
            connection.execute(
                "DELETE FROM message WHERE conversation_id = ?", (conversation_id,)
            )
            connection.execute("DELETE FROM conversation WHERE id = ?", (conversation_id,))
