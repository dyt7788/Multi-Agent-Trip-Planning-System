"""SiliconFlow chat completion client with deterministic fallback behavior."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings


class LLMClient:
    """Async wrapper around SiliconFlow's OpenAI-compatible chat API."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    @property
    def configured(self) -> bool:
        return bool(self.effective_api_key and self.effective_model)

    @property
    def effective_api_key(self) -> Optional[str]:
        return self.api_key or self.settings.effective_llm_api_key

    @property
    def effective_base_url(self) -> str:
        return (self.base_url or self.settings.effective_llm_base_url).rstrip("/")

    @property
    def effective_model(self) -> Optional[str]:
        return self.model or self.settings.effective_llm_model

    @property
    def effective_timeout(self) -> float:
        return self.timeout or self.settings.llm_timeout

    @property
    def effective_max_tokens(self) -> Optional[int]:
        return self.max_tokens if self.max_tokens is not None else self.settings.llm_max_tokens

    async def complete_text(
        self,
        messages: List[Dict[str, Any]],
        fallback: str = "",
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.configured:
            return fallback

        payload: Dict[str, Any] = {
            "model": model or self.effective_model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.effective_max_tokens:
            payload["max_tokens"] = self.effective_max_tokens
        if response_format:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.effective_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.effective_timeout, headers=headers) as client:
                response = await client.post(f"{self.effective_base_url}/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
            return data["choices"][0]["message"].get("content") or fallback
        except Exception:
            return fallback

    async def complete_json(
        self,
        messages: List[Dict[str, Any]],
        fallback: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        text = await self.complete_text(
            messages,
            fallback=json.dumps(fallback, ensure_ascii=False),
            model=model,
            response_format={"type": "json_object"},
        )
        parsed = self._parse_json(text)
        return parsed if isinstance(parsed, dict) else fallback

    @staticmethod
    def _parse_json(value: str) -> Any:
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, flags=re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        match = re.search(r"(\{.*\})", value, flags=re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        return None


class SiliconFlowClient(LLMClient):
    """
    Dedicated SiliconFlow client with agent-specific model selection.

    Each agent uses a different model based on task complexity:
    - TotalAgent: 7B for lightweight routing
    - StrategyAgent: 7B for rule-based transformation
    - QueryAgent: 14B for medium complexity understanding
    - AnalysisAgent: 32B for deep analysis
    - ReportAgent: 7B for structured output
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        agent_name: str = "TotalAgent",
        temperature: float = 0.2,
    ) -> None:
        settings = settings or get_settings()
        config = settings.get_agent_model_config(agent_name)

        super().__init__(
            settings=settings,
            model=config["model"],
            timeout=config["timeout"],
            max_tokens=config["max_tokens"],
            temperature=temperature,
        )
        self.agent_name = agent_name

    @property
    def effective_model(self) -> Optional[str]:
        """Override to use agent-specific model."""
        return self.model or self.settings.get_agent_model_config(self.agent_name)["model"]

    @property
    def effective_timeout(self) -> float:
        """Override to use agent-specific timeout."""
        return self.timeout or self.settings.get_agent_model_config(self.agent_name)["timeout"]

    @property
    def effective_max_tokens(self) -> Optional[int]:
        """Override to use agent-specific max tokens."""
        config = self.settings.get_agent_model_config(self.agent_name)
        return self.max_tokens if self.max_tokens is not None else config["max_tokens"]


# Convenience factory function
def create_agent_llm(agent_name: str, settings: Optional[Settings] = None) -> LLMClient:
    """Create an LLM client configured for a specific agent."""
    return SiliconFlowClient(settings=settings, agent_name=agent_name)
