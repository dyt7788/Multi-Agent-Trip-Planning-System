"""Short-term and long-term memory facade for Total Agent workflows."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.models.schemas import PreferenceMemory, PreferenceUpdateRequest, TripPlanRequest
from TravelCore.text import dedupe


class ConversationStore:
    """SQLite-backed conversation persistence."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = Path(self.settings.memory_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_tables(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    destination TEXT DEFAULT '',
                    days INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    trace TEXT,
                    plan TEXT,
                    reports TEXT,
                    timestamp TEXT NOT NULL
                )"""
            )
            self._ensure_message_column(conn, "reports", "TEXT")

    def _ensure_message_column(self, conn: sqlite3.Connection, column: str, column_type: str) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {column_type}")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save(
        self,
        conv_id: str,
        user_id: str,
        title: str,
        messages: List[Dict[str, Any]],
        destination: str = "",
        days: int = 0,
    ) -> None:
        """保存对话及其消息（先删旧消息再插入新消息）。"""
        now = self._now()
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            conn.execute(
                """INSERT INTO conversations(id, user_id, title, destination, days, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       title = excluded.title,
                       destination = excluded.destination,
                       days = excluded.days,
                       updated_at = excluded.updated_at""",
                (conv_id, user_id, title, destination, days, now, now),
            )
            for msg in messages:
                conn.execute(
                    """INSERT INTO messages(conversation_id, role, content, trace, plan, reports, timestamp)
                       VALUES(?, ?, ?, ?, ?, ?, ?)""",
                    (
                        conv_id,
                        msg.get("role", "user"),
                        msg.get("content", ""),
                        json.dumps(msg["trace"], ensure_ascii=False) if msg.get("trace") else None,
                        json.dumps(msg.get("plan", {}), ensure_ascii=False) if msg.get("plan") else None,
                        json.dumps(msg.get("reports", []), ensure_ascii=False) if msg.get("reports") else None,
                        msg.get("timestamp", now),
                    ),
                )

    def list_by_user(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """列出用户的对话列表。"""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, title, destination, days, created_at, updated_at
                   FROM conversations WHERE user_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """获取单个对话详情（含所有消息）。"""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, title, destination, days, created_at, updated_at
                   FROM conversations WHERE id = ?""",
                (conv_id,),
            ).fetchone()
            if not row:
                return None
            msgs = conn.execute(
                """SELECT id, role, content, trace, plan, reports, timestamp
                   FROM messages WHERE conversation_id = ?
                   ORDER BY timestamp ASC""",
                (conv_id,),
            ).fetchall()
        result = dict(row)
        result["messages"] = [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "trace": json.loads(m["trace"]) if m["trace"] else None,
                "plan": json.loads(m["plan"]) if m["plan"] else None,
                "reports": json.loads(m["reports"]) if m["reports"] else None,
                "timestamp": m["timestamp"],
            }
            for m in msgs
        ]
        return result

    def delete(self, conv_id: str) -> bool:
        """删除对话及其所有消息。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            return cursor.rowcount > 0


class PreferenceStore:
    """SQLite-backed user preference memory."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = Path(self.settings.memory_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def get(self, user_id: str) -> PreferenceMemory:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            history_count = conn.execute(
                "SELECT COUNT(*) AS count FROM user_interactions WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]

        if not row:
            return PreferenceMemory(user_id=user_id, history_count=history_count)
        payload = json.loads(row["payload"])
        payload["history_count"] = history_count
        payload["updated_at"] = row["updated_at"]
        return PreferenceMemory(**payload)

    def update(self, user_id: str, update: PreferenceUpdateRequest | TripPlanRequest) -> PreferenceMemory:
        current = self.get(user_id)
        preferences = dedupe(current.preferences + list(getattr(update, "preferences", []) or []), 50)
        disliked = dedupe(current.disliked + list(getattr(update, "disliked", []) or []), 50)
        budget_level = getattr(update, "budget_level", None) or current.budget_level
        pace = getattr(update, "pace", None) or getattr(update, "travel_style", None) or current.pace
        notes = getattr(update, "notes", "") or current.notes
        if isinstance(update, TripPlanRequest) and update.free_text:
            notes = dedupe([notes, update.free_text], 5)
            notes = " | ".join(notes)

        memory = PreferenceMemory(
            user_id=user_id,
            preferences=preferences,
            disliked=disliked,
            budget_level=budget_level,
            pace=pace,
            notes=notes,
            history_count=current.history_count,
            updated_at=datetime.now(timezone.utc),
        )
        self.save(memory)
        return memory

    def save(self, memory: PreferenceMemory) -> None:
        payload = memory.model_dump(mode="json")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences(user_id, payload, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    memory.user_id,
                    json.dumps(payload, ensure_ascii=False),
                    (memory.updated_at or datetime.now(timezone.utc)).isoformat(),
                ),
            )

    def append_event(
        self,
        user_id: str,
        event_type: str,
        payload: Dict[str, Any],
        conversation_id: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_interactions(user_id, conversation_id, event_type, payload, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    conversation_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


class ShortTermMemory:
    """In-memory short-term storage with optional Redis backend."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._memory: dict[str, dict[str, Any]] = {}
        self._redis = None
        self._redis_prefix = "trip:short:"

        if self.settings.redis_url:
            try:
                import redis
                self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
            except ImportError:
                pass

    def get(self, user_id: str) -> dict[str, Any]:
        """Get short-term memory for a user."""
        if self._redis:
            data = self._redis.get(f"{self._redis_prefix}{user_id}")
            if data:
                return json.loads(data)
        return dict(self._memory.get(user_id, {}))

    def set(self, user_id: str, data: dict[str, Any]) -> None:
        """Set short-term memory for a user."""
        if self._redis:
            self._redis.setex(
                f"{self._redis_prefix}{user_id}",
                3600,
                json.dumps(data, ensure_ascii=False),
            )
        self._memory[user_id] = dict(data)

    def delete(self, user_id: str) -> None:
        """Delete short-term memory for a user."""
        if self._redis:
            self._redis.delete(f"{self._redis_prefix}{user_id}")
        self._memory.pop(user_id, None)

    def clear_all(self) -> None:
        """Clear all short-term memory."""
        if self._redis:
            keys = self._redis.keys(f"{self._redis_prefix}*")
            if keys:
                self._redis.delete(*keys)
        self._memory.clear()


class LongTermMemory:
    """SQLite-backed long-term memory for user preferences and history."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.store = PreferenceStore(self.settings)

    def get(self, user_id: str) -> PreferenceMemory:
        """Get long-term preference memory for a user."""
        return self.store.get(user_id)

    def save(self, memory: PreferenceMemory) -> None:
        """Save long-term preference memory."""
        self.store.save(memory)

    def update(self, user_id: str, data: dict[str, Any] | TripPlanRequest | PreferenceUpdateRequest) -> PreferenceMemory:
        """Update long-term memory and return updated memory."""
        # Handle PreferenceUpdateRequest directly
        if isinstance(data, PreferenceUpdateRequest):
            return self.store.update(user_id, data)
        # Handle TripPlanRequest
        if isinstance(data, TripPlanRequest):
            prefs = data.preferences
            if isinstance(prefs, dict):
                prefs = list(prefs.values())
            elif not isinstance(prefs, list):
                prefs = []

            update = PreferenceUpdateRequest(
                preferences=prefs,
                budget_level=getattr(data, "budget", None),
                pace=getattr(data, "travel_style", None),
                notes=getattr(data, "free_text", ""),
            )
        else:
            update = PreferenceUpdateRequest(
                preferences=data.get("preferences", []),
                disliked=data.get("disliked", []),
                budget_level=data.get("budget_level"),
                pace=data.get("pace"),
                notes=data.get("notes", ""),
            )
        return self.store.update(user_id, update)

    def record_trip_history(
        self,
        user_id: str,
        conversation_id: Optional[str],
        trip_data: dict[str, Any],
    ) -> None:
        """Record a trip planning event to long-term history."""
        self.store.append_event(
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="itinerary_plan",
            payload=trip_data,
        )

    def get_trip_history(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get recent trip planning history for a user."""
        db_path = Path(self.settings.memory_db_path)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT payload, created_at FROM user_interactions
                WHERE user_id = ? AND event_type = 'itinerary_plan'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        return [
            {
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_user_spot_history(self, user_id: str) -> dict[str, list[str]]:
        """Get history of spots user kept or removed."""
        history = self.get_trip_history(user_id, limit=50)
        kept_spots = []
        removed_spots = []

        for entry in history:
            payload = entry.get("payload", {})
            kept_spots.extend(payload.get("kept_spots", []))
            removed_spots.extend(payload.get("removed_spots", []))

        return {
            "kept": list(set(kept_spots)),
            "removed": list(set(removed_spots)),
        }


class Memory:
    """Unified memory interface combining short-term and long-term storage."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.short_term = ShortTermMemory(self.settings)
        self.long_term = LongTermMemory(self.settings)

    def get_short_term(self, user_id: str) -> dict[str, Any]:
        """Get short-term memory for a user."""
        return self.short_term.get(user_id)

    def set_short_term(self, user_id: str, data: dict[str, Any]) -> None:
        """Set short-term memory for a user."""
        self.short_term.set(user_id, data)

    def get_long_term(self, user_id: str) -> PreferenceMemory:
        """Get long-term preference memory for a user."""
        return self.long_term.get(user_id)

    def set_long_term(self, user_id: str, data: dict[str, Any]) -> PreferenceMemory:
        """Update long-term memory and return updated memory."""
        return self.long_term.update(user_id, data)

    def record_trip(
        self,
        user_id: str,
        conversation_id: Optional[str],
        trip_data: dict[str, Any],
    ) -> None:
        """Record a trip to long-term history."""
        self.long_term.record_trip_history(user_id, conversation_id, trip_data)

    def get_trip_history(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get recent trip planning history."""
        return self.long_term.get_trip_history(user_id, limit)

    def get_spot_history(self, user_id: str) -> dict[str, list[str]]:
        """Get user's spot keep/remove history."""
        return self.long_term.get_user_spot_history(user_id)
