"""StrategyAgent: turns user intent and memory into Query Agent input."""

from __future__ import annotations

from typing import Any, Optional

from app.config import Settings, get_settings
from app.models.schemas import (
    AgentTrace,
    PreferenceAgentOutput,
    PreferenceMemory,
    PreferenceUpdateRequest,
    StrategyAgentOutput,
    StrategyQueryInput,
    TripPlanRequest,
)
from Memory import LongTermMemory
from TravelCore.llm import create_agent_llm
from TravelCore.text import dedupe, utc_now


class StrategyAgent:
    """
    Strategy Agent (策略 Agent) - SiliconFlow Qwen2.5-7B-Instruct

    Responsibilities:
    - Analyze user input and historical preferences
    - Generate structured query JSON for QueryAgent
    - Manage long-term memory updates

    Input: TripPlanRequest or TotalAgent's agent_input
    Output: StrategyAgentOutput with query JSON

    Model: 7B for rule-based transformation and structured output
    """

    name = "StrategyAgent"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm: Optional[Any] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or create_agent_llm(self.name, self.settings)
        self.long_term_memory = LongTermMemory(self.settings)

    async def run(self, request: TripPlanRequest) -> StrategyAgentOutput:
        """
        Main strategy execution.

        Flow:
        1. Update/load long-term memory
        2. Record interaction event
        3. Extract scenic types from preferences
        4. Determine required info types
        5. Build query JSON
        """
        started = utc_now()

        # Step 1: Update memory with request preferences
        memory = self.long_term_memory.update(request.user_id, request)

        # Step 2: Record event
        event_type = (
            "trip_request"
            if request.mode in ("初次规划", "plan_trip")
            else "modify_report"
        )
        self.long_term_memory.store.append_event(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            event_type=event_type,
            payload=request.model_dump(mode="json"),
        )

        # Step 3: Extract scenic types
        scenic_types = self._extract_scenic_types(request, memory)

        # Step 4: Determine required info
        required_info = self._extract_required_info(request)

        # Step 5: Build deduped keywords
        keywords = dedupe(scenic_types + required_info + request.preferences, 10)

        # Step 6: Create query input
        query = StrategyQueryInput(
            destination=request.destination,
            days=request.days,
            scenic_types=scenic_types,
            required_info=required_info,
            keywords=keywords,
            limit=self.settings.max_search_results,
        )

        preferences = self._build_preference_payload(request)
        user_history = memory.model_dump(mode="json")

        return StrategyAgentOutput(
            destination=request.destination,
            days=request.days,
            scenic_types=scenic_types,
            required_info=required_info,
            preferences=preferences,
            user_history=user_history,
            query=query,
            memory=memory,
            trace=[
                AgentTrace(
                    agent=self.name,
                    status="completed",
                    message="Generated query JSON from request preferences and memory.",
                    started_at=started,
                    finished_at=utc_now(),
                    metadata={
                        "required_info": required_info,
                        "scenic_types": scenic_types,
                        "model": self.llm.effective_model,
                    },
                )
            ],
        )

    async def load(self, user_id: str) -> PreferenceAgentOutput:
        """Load user preferences from long-term memory."""
        started = utc_now()
        memory = self.long_term_memory.get(user_id)

        return PreferenceAgentOutput(
            memory=memory,
            trace=[
                AgentTrace(
                    agent="Memory",
                    status="completed",
                    message="Loaded long-term preference memory.",
                    started_at=started,
                    finished_at=utc_now(),
                )
            ],
        )

    async def update(
        self,
        user_id: str,
        update: PreferenceUpdateRequest,
    ) -> PreferenceMemory:
        """Update user preferences."""
        return self.long_term_memory.update(user_id, update)

    async def remember_plan(self, request: TripPlanRequest, plan_payload: dict) -> None:
        """Record a generated plan to long-term history."""
        self.long_term_memory.store.append_event(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            event_type="itinerary_plan",
            payload=plan_payload,
        )

    def _extract_scenic_types(
        self,
        request: TripPlanRequest,
        memory: PreferenceMemory,
    ) -> list[str]:
        """
        Extract scenic/attraction types from request and memory.

        Priority:
        1. Explicit 景点类型 from preference_details
        2. Known type keywords from preferences
        3. Memory preferences
        """
        details = self._build_preference_payload(request)

        # Try explicit field first
        raw_types = (
            details.get("景点类型")
            or details.get("scenic_types")
            or details.get("attraction_types")
        )

        if isinstance(raw_types, str):
            scenic_types = [
                item.strip()
                for item in raw_types.replace(",", ",").split(",")
                if item.strip()
            ]
        elif isinstance(raw_types, list):
            scenic_types = [
                str(item).strip() for item in raw_types if str(item).strip()
            ]
        else:
            scenic_types = []

        # Fallback to known type keywords
        known_types = {
            "历史",
            "自然",
            "亲子",
            "美食",
            "博物馆",
            "艺术",
            "购物",
            "夜景",
            "户外",
        }

        # Handle request.preferences being either list or dict
        request_prefs = []
        if isinstance(request.preferences, list):
            request_prefs = request.preferences
        elif isinstance(request.preferences, dict):
            request_prefs = list(request.preferences.values())

        fallback_types = [
            item
            for item in request_prefs + memory.preferences
            if item in known_types
        ]

        return dedupe(scenic_types + fallback_types, 8)

    @staticmethod
    def _extract_required_info(request: TripPlanRequest) -> list[str]:
        """
        Determine what types of information are required.

        Always includes "攻略", plus conditional additions:
        - "最新信息" if start_date is set
        - "酒店" if hotel level specified
        - "预算" if budget specified
        - "局部更新" if modify mode
        """
        required = ["攻略"]
        details = request.preference_details or {}

        if request.start_date:
            required.append("最新信息")
        if details.get("酒店级别") or details.get("hotel_level"):
            required.append("酒店")
        if request.budget or details.get("预算"):
            required.append("预算")
        if request.mode in ("修改报告", "modify_report"):
            required.append("局部更新")

        return dedupe(required, 8)

    @staticmethod
    def _build_preference_payload(request: TripPlanRequest) -> dict[str, Any]:
        """Extract preference details as a flat dictionary."""
        if request.preference_details:
            return dict(request.preference_details)
        return {"偏好": request.preferences}


# Backward compatibility alias
UserPreferenceAgent = StrategyAgent
