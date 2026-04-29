"""Chroma-backed conversation persistence for the Streamlit planner."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class HashEmbeddingFunction:
    """Small local embedding function so Chroma does not need a model download."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def __call__(self, input: List[str]) -> List[List[float]]:  # noqa: A002 - Chroma expects this name
        return [self._embed(text) for text in input]

    def name(self) -> str:
        return "hash-embeddings"

    def _embed(self, text: str) -> List[float]:
        tokens = self._tokenize(text)
        vector = [0.0] * self.dimensions
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        value = (text or "").lower()
        parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", value)
        bigrams = [f"{parts[i]}{parts[i + 1]}" for i in range(len(parts) - 1)]
        return parts + bigrams


class ChromaConversationStore:
    """Persist, name, and search conversations with Chroma."""

    def __init__(self, persist_dir: Path, collection_name: str = "travel_conversations"):
        self.persist_dir = Path(persist_dir)
        self.collection_name = collection_name
        self.enabled = False
        self.error: Optional[str] = None
        self.collection = None
        self._init_chroma()

    def _init_chroma(self) -> None:
        try:
            import chromadb
        except Exception as exc:
            self.error = f"Chroma 未启用: {exc}"
            return

        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.persist_dir))
            self.collection = client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=HashEmbeddingFunction(),
                metadata={"description": "Travel planner conversations"},
            )
            self.enabled = True
            self.error = None
        except Exception as exc:
            self.error = f"Chroma 初始化失败: {exc}"

    def create_conversation(self, user_text: str, title: Optional[str] = None) -> Optional[str]:
        if not self.enabled or self.collection is None:
            return None

        conversation_id = uuid.uuid4().hex
        now = self._now()
        final_title = title.strip() if isinstance(title, str) and title.strip() else self.make_title(user_text)
        title_source = "custom" if isinstance(title, str) and title.strip() else "auto"
        metadata = {
            "kind": "conversation",
            "conversation_id": conversation_id,
            "title": final_title,
            "title_source": title_source,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "user_preview": self.preview(user_text, 160),
        }
        self.collection.upsert(
            ids=[self._meta_id(conversation_id)],
            documents=[f"{final_title}\n{user_text}"],
            metadatas=[metadata],
        )
        self.append_message(conversation_id, "user", user_text, title=final_title)
        return conversation_id

    def append_message(
        self,
        conversation_id: Optional[str],
        role: str,
        content: str,
        title: Optional[str] = None,
    ) -> None:
        if not conversation_id or not self.enabled or self.collection is None:
            return
        if not content:
            return

        now = self._now()
        message_id = f"{conversation_id}:message:{now}:{uuid.uuid4().hex[:8]}"
        metadata = {
            "kind": "message",
            "conversation_id": conversation_id,
            "role": role,
            "title": title or "",
            "created_at": now,
            "updated_at": now,
            "preview": self.preview(content, 220),
        }
        self.collection.upsert(ids=[message_id], documents=[content], metadatas=[metadata])

    def finish_conversation(
        self,
        conversation_id: Optional[str],
        user_text: str,
        assistant_text: str,
        status: str,
    ) -> None:
        if not conversation_id or not self.enabled or self.collection is None:
            return

        existing_meta = self._get_conversation_meta(conversation_id)
        title_source = (existing_meta or {}).get("title_source") or "auto"
        final_title = (existing_meta or {}).get("title")
        if not final_title or title_source != "custom":
            final_title = self.make_title(user_text)
            title_source = "auto"

        self.append_message(conversation_id, "assistant", assistant_text, title=final_title)
        now = self._now()
        metadata = {
            "kind": "conversation",
            "conversation_id": conversation_id,
            "title": final_title,
            "title_source": title_source,
            "status": status,
            "created_at": (existing_meta or {}).get("created_at") or now,
            "updated_at": now,
            "user_preview": self.preview(user_text, 160),
            "assistant_preview": self.preview(assistant_text, 160),
        }
        self.collection.upsert(
            ids=[self._meta_id(conversation_id)],
            documents=[f"{final_title}\n用户: {user_text}\n助手: {assistant_text}"],
            metadatas=[metadata],
        )

    def update_conversation_title(self, conversation_id: str, title: str) -> bool:
        if not conversation_id or not self.enabled or self.collection is None:
            return False
        clean_title = (title or "").strip()
        if not clean_title:
            return False

        existing_meta = self._get_conversation_meta(conversation_id) or {}
        now = self._now()
        metadata = {
            "kind": "conversation",
            "conversation_id": conversation_id,
            "title": clean_title,
            "title_source": "custom",
            "status": existing_meta.get("status", "running"),
            "created_at": existing_meta.get("created_at", now),
            "updated_at": now,
            "user_preview": existing_meta.get("user_preview", ""),
            "assistant_preview": existing_meta.get("assistant_preview", ""),
        }
        doc = (
            f"{clean_title}\n"
            f"用户: {metadata.get('user_preview', '')}\n"
            f"助手: {metadata.get('assistant_preview', '')}"
        )
        self.collection.upsert(
            ids=[self._meta_id(conversation_id)],
            documents=[doc],
            metadatas=[metadata],
        )
        return True

    def list_conversations(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.enabled or self.collection is None:
            return []

        result = self.collection.get(
            where={"kind": "conversation"},
            include=["metadatas", "documents"],
        )
        rows = self._rows_from_get(result)
        rows.sort(key=lambda item: str(item["metadata"].get("updated_at", "")), reverse=True)
        return rows[:limit]

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        if not self.enabled or self.collection is None or not conversation_id:
            return {"conversation": None, "messages": []}

        result = self.collection.get(
            where={"conversation_id": conversation_id},
            include=["metadatas", "documents"],
        )
        rows = self._rows_from_get(result)
        conversation = None
        messages: List[Dict[str, Any]] = []
        for row in rows:
            if row["metadata"].get("kind") == "conversation":
                conversation = row
            elif row["metadata"].get("kind") == "message":
                messages.append(row)

        messages.sort(key=lambda item: str(item["metadata"].get("created_at", "")))
        return {"conversation": conversation, "messages": messages}

    def search(self, query: str, limit: int = 6) -> List[Dict[str, Any]]:
        if not self.enabled or self.collection is None or not query.strip():
            return []

        result = self.collection.query(
            query_texts=[query],
            n_results=limit,
            where={"kind": "message"},
            include=["metadatas", "documents", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        rows: List[Dict[str, Any]] = []
        for index, item_id in enumerate(ids):
            rows.append(
                {
                    "id": item_id,
                    "document": docs[index] if index < len(docs) else "",
                    "metadata": metas[index] if index < len(metas) else {},
                    "distance": distances[index] if index < len(distances) else None,
                }
            )
        return rows

    @staticmethod
    def make_title(text: str, max_length: int = 24) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        cleaned = re.sub(r"^(请|帮我|麻烦|能不能|可以)?\s*", "", cleaned)
        cleaned = re.sub(r"[，。！？,.!?；;：:]+", " ", cleaned).strip()
        if not cleaned:
            return "未命名对话"
        return cleaned[:max_length]

    @staticmethod
    def preview(text: str, max_length: int = 120) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[: max_length - 3] + "..."

    @staticmethod
    def _rows_from_get(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        rows: List[Dict[str, Any]] = []
        for index, item_id in enumerate(ids):
            rows.append(
                {
                    "id": item_id,
                    "document": docs[index] if index < len(docs) else "",
                    "metadata": metas[index] if index < len(metas) else {},
                }
            )
        return rows

    def _get_conversation_meta(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        if not conversation_id or not self.enabled or self.collection is None:
            return None
        result = self.collection.get(ids=[self._meta_id(conversation_id)], include=["metadatas"])
        metas = result.get("metadatas") or []
        if metas:
            return metas[0]
        return None

    @staticmethod
    def _meta_id(conversation_id: str) -> str:
        return f"{conversation_id}:meta"

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
