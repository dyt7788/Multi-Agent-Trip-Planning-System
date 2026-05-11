"""QueryAgent-owned OpenAI-compatible LLM client."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings


class QueryLLMClient:
    """LLM adapter used only by QueryEngine.

    Each Agent should be free to choose a different model. QueryAgent uses
    query/extraction/synthesis model names instead of the shared default.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key(self) -> Optional[str]:
        return self.settings.query_llm_api_key or self.settings.effective_llm_api_key

    @property
    def base_url(self) -> str:
        return self.settings.query_llm_base_url or self.settings.effective_llm_base_url

    @property
    def default_model(self) -> str:
        return self.settings.query_llm_model or self.settings.effective_llm_model

    @property
    def query_model(self) -> str:
        return self.settings.query_query_model or self.default_model

    @property
    def extraction_model(self) -> str:
        return self.settings.query_extraction_model or self.default_model

    @property
    def synthesis_model(self) -> str:
        return self.settings.query_synthesis_model or self.extraction_model

    @property
    def timeout(self) -> float:
        return self.settings.query_llm_timeout or self.settings.llm_timeout

    @property
    def max_tokens(self) -> Optional[int]:
        return self.settings.query_llm_max_tokens or self.settings.llm_max_tokens

    async def complete_json(
        self,
        messages: List[Dict[str, Any]],
        fallback: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.configured:
            return fallback
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
            response = await client.chat.completions.create(
                model=model or self.default_model,
                messages=messages,
                temperature=0.1,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            parsed = self._parse_json(content)
            return parsed if isinstance(parsed, dict) else fallback
        except Exception:
            return fallback

    @staticmethod
    def _parse_json(value: str) -> Any:
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, flags=re.S)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                return None
        inline = re.search(r"(\{.*\})", value, flags=re.S)
        if inline:
            try:
                return json.loads(inline.group(1))
            except json.JSONDecodeError:
                return None
        return None
