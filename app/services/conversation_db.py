"""Structured SQLite persistence for planner conversations and agent traces."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConversationDatabase:
    """Persist conversations, chat messages, and raw agent steps in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create_conversation(self, user_text: str, title: Optional[str] = None) -> str:
        conversation_id = uuid.uuid4().hex
        now = self._now()
        final_title = self.make_title(title or user_text)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (
                    id, title, status, user_text, assistant_text,
                    user_preview, assistant_preview, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    final_title,
                    "running",
                    user_text,
                    "",
                    self.preview(user_text, 240),
                    "",
                    now,
                    now,
                ),
            )
            self.append_message(conversation_id, "user", user_text, conn=conn)
        return conversation_id

    def append_message(
        self,
        conversation_id: Optional[str],
        role: str,
        content: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        if not conversation_id or not content:
            return

        def write(active_conn: sqlite3.Connection) -> None:
            sequence = self._next_sequence(active_conn, "messages", conversation_id)
            active_conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, sequence, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, sequence, self._now()),
            )

        if conn is not None:
            write(conn)
        else:
            with self._connect() as active_conn:
                write(active_conn)

    def save_step(self, conversation_id: Optional[str], step: object) -> None:
        if not conversation_id or step is None:
            return

        data = self._step_to_dict(step)
        with self._connect() as conn:
            sequence = self._next_sequence(conn, "agent_steps", conversation_id)
            conn.execute(
                """
                INSERT INTO agent_steps (
                    conversation_id, step_index, event_type, title, assistant_text,
                    file_path, code, output, error, success, agent_name, sequence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    data.get("step_index"),
                    data.get("event_type") or "",
                    data.get("title") or "",
                    data.get("assistant_text") or "",
                    data.get("file_path") or "",
                    data.get("code") or "",
                    data.get("output") or "",
                    data.get("error") or "",
                    1 if data.get("success", True) else 0,
                    data.get("agent_name") or "",
                    sequence,
                    self._now(),
                ),
            )

    def finish_conversation(
        self,
        conversation_id: Optional[str],
        user_text: str,
        assistant_text: str,
        status: str,
    ) -> None:
        if not conversation_id:
            return

        now = self._now()
        with self._connect() as conn:
            self.append_message(conversation_id, "assistant", assistant_text, conn=conn)
            conn.execute(
                """
                UPDATE conversations
                SET status = ?, user_text = ?, assistant_text = ?,
                    user_preview = ?, assistant_preview = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    user_text,
                    assistant_text,
                    self.preview(user_text, 240),
                    self.preview(assistant_text, 240),
                    now,
                    conversation_id,
                ),
            )

    def update_conversation_title(self, conversation_id: str, title: str) -> bool:
        clean_title = self.make_title(title)
        if not conversation_id or not clean_title:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (clean_title, self._now(), conversation_id),
            )
            return cursor.rowcount > 0

    def list_conversations(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, status, user_preview, assistant_preview, created_at, updated_at
                FROM conversations
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_conversations(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        value = (query or "").strip()
        if not value:
            return self.list_conversations(limit)
        like = f"%{value}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c.id, c.title, c.status, c.user_preview, c.assistant_preview,
                       c.created_at, c.updated_at
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                LEFT JOIN agent_steps s ON s.conversation_id = c.id
                WHERE c.title LIKE ?
                   OR c.user_text LIKE ?
                   OR c.assistant_text LIKE ?
                   OR m.content LIKE ?
                   OR s.assistant_text LIKE ?
                   OR s.output LIKE ?
                   OR s.error LIKE ?
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (like, like, like, like, like, like, like, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            conversation = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            messages = conn.execute(
                """
                SELECT role, content, sequence, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY sequence ASC, id ASC
                """,
                (conversation_id,),
            ).fetchall()
            steps = conn.execute(
                """
                SELECT step_index, event_type, title, assistant_text, file_path, code,
                       output, error, success, agent_name, sequence, created_at
                FROM agent_steps
                WHERE conversation_id = ?
                ORDER BY sequence ASC, id ASC
                """,
                (conversation_id,),
            ).fetchall()

        return {
            "conversation": dict(conversation) if conversation else None,
            "messages": [dict(row) for row in messages],
            "steps": [dict(row) for row in steps],
        }

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_text TEXT NOT NULL DEFAULT '',
                    assistant_text TEXT NOT NULL DEFAULT '',
                    user_preview TEXT NOT NULL DEFAULT '',
                    assistant_preview TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    step_index INTEGER,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    assistant_text TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL DEFAULT '',
                    output TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    success INTEGER NOT NULL DEFAULT 1,
                    agent_name TEXT NOT NULL DEFAULT '',
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, sequence)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_steps_conversation ON agent_steps(conversation_id, sequence)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _next_sequence(conn: sqlite3.Connection, table: str, conversation_id: str) -> int:
        row = conn.execute(
            f"SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM {table} WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return int(row["next_sequence"])

    @staticmethod
    def _step_to_dict(step: object) -> Dict[str, Any]:
        if is_dataclass(step):
            return asdict(step)
        if isinstance(step, dict):
            return step
        return {
            "step_index": getattr(step, "step_index", None),
            "event_type": getattr(step, "event_type", ""),
            "title": getattr(step, "title", ""),
            "assistant_text": getattr(step, "assistant_text", ""),
            "file_path": getattr(step, "file_path", ""),
            "code": getattr(step, "code", ""),
            "output": getattr(step, "output", ""),
            "error": getattr(step, "error", ""),
            "success": getattr(step, "success", True),
            "agent_name": getattr(step, "agent_name", ""),
        }

    @staticmethod
    def make_title(text: str, max_length: int = 28) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        cleaned = re.sub(r"^(请|帮我|麻烦|能不能|可以)?\s*", "", cleaned)
        cleaned = re.sub(r"[，。！？,.!?；;：:]+", " ", cleaned).strip()
        return cleaned[:max_length] if cleaned else "未命名对话"

    @staticmethod
    def preview(text: str, max_length: int = 160) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[: max_length - 3] + "..."

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
