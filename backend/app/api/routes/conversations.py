"""Conversation CRUD API endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationSummary,
)
from Memory import ConversationStore


router = APIRouter()
conv_store = ConversationStore()


@router.post("/conversations", status_code=201)
async def save_conversation(request: ConversationCreateRequest) -> Dict[str, str]:
    """保存或更新对话及其消息列表。"""
    conv_store.save(
        conv_id=request.id,
        user_id="guest",
        title=request.title,
        messages=request.messages,
        destination=request.destination,
        days=request.days,
    )
    return {"status": "ok"}


@router.get("/users/{user_id}/conversations", response_model=List[ConversationSummary])
async def list_conversations(user_id: str) -> List[Dict[str, Any]]:
    """列出用户的所有对话。"""
    return conv_store.list_by_user(user_id)


@router.get("/conversations/{conv_id}", response_model=ConversationDetailResponse)
async def get_conversation(conv_id: str) -> Dict[str, Any]:
    """获取单个对话详情（含所有消息）。"""
    data = conv_store.get(conv_id)
    if not data:
        raise HTTPException(status_code=404, detail="对话不存在。")
    return data


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: str) -> None:
    """删除对话及其所有消息。"""
    if not conv_store.delete(conv_id):
        raise HTTPException(status_code=404, detail="对话不存在。")
