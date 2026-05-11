"""TotalAgent: receives user requests and orchestrates the travel agents."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.config import Settings, get_settings
from app.models.schemas import (
    AgentTrace,
    AnalysisAgentOutput,
    ImageAnalysisRequest,
    ImageAgentOutput,
    PreferenceMemory,
    PreferenceUpdateRequest,
    QueryAgentOutput,
    QueryResearchRequest,
    ReportAgentOutput,
    StrategyAgentOutput,
    TotalAgentOutput,
    TravelPlanResponse,
    TripPlanRequest,
)
from Memory import Memory
from StrategyAgent.agent import StrategyAgent
from QueryEngine.agent import QueryAgent
from ImageAgent.agent import ImageAgent
from AnalysisAgent.agent import AnalysisAgent
from ReportEngine.agent import ReportAgent
from TravelCore.llm import create_agent_llm
from TravelCore.text import utc_now


class TotalAgent:
    """
    Total Agent (总协调 Agent) - SiliconFlow Qwen2.5-7B-Instruct

    Responsibilities:
    - Receive user requests
    - Analyze mode (初次规划 vs 修改报告)
    - Read short-term & long-term memory
    - Dispatch downstream agents
    - Manage memory updates

    Model: Lightweight 7B model for routing decisions
    """

    name = "TotalAgent"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        memory: Optional[Memory] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.memory = memory or Memory(self.settings)
        self.llm = create_agent_llm(self.name, self.settings)

        # Initialize downstream agents
        self.strategy_agent = StrategyAgent(self.settings)
        self.query_agent = QueryAgent(self.settings)
        self.image_agent = ImageAgent(self.settings)
        self.analysis_agent = AnalysisAgent(self.settings)
        self.report_agent = ReportAgent(self.settings)

    async def plan_trip(self, request: TripPlanRequest) -> TravelPlanResponse:
        """
        Main entry point for trip planning.

        Flow:
        1. Analyze request mode (plan_trip vs modify_report)
        2. Read memory (short-term & long-term)
        3. Dispatch StrategyAgent → QueryAgent → AnalysisAgent → ReportAgent
        4. Update memory with results
        5. Return comprehensive response
        """
        started = utc_now()

        # Step 1: Analyze mode
        action = "modify_report" if request.mode in ("修改报告", "modify_report") else "plan_trip"

        # Step 2: Read memory
        short_term = self.memory.get_short_term(request.user_id)
        long_term = self.memory.get_long_term(request.user_id)

        # Step 3: Create TotalAgent output (routing decision)
        total_output = TotalAgentOutput(
            action=action,
            agent_input={
                "destination": request.destination,
                "days": request.days,
                "preferences": request.preference_details or request.preferences,
                "user_history": long_term.model_dump(mode="json"),
            },
        )

        # Step 4: Dispatch StrategyAgent
        strategy_output = await self.strategy_agent.run(request)

        # Step 5: Parallel dispatch QueryAgent and ImageAgent
        query_task = self.query_agent.run(strategy_output)
        image_task = self.image_agent.run(request)
        query_output, image_output = await asyncio.gather(query_task, image_task)

        # Step 6: AnalysisAgent enriches spots and builds itinerary
        analysis_output, plan, analysis_trace = await self.analysis_agent.run(
            request,
            strategy_output,
            query_output,
            image_output,
            strategy_output.memory,
        )

        # Step 7: ReportAgent generates HTML report
        report_output, report_trace = await self.report_agent.run(plan, request.export_pdf, analysis_output)

        # Step 8: Update long-term memory with trip history
        await self.strategy_agent.remember_plan(request, plan.model_dump(mode="json"))

        # Step 9: Update short-term memory with current state
        self.memory.set_short_term(
            request.user_id,
            {
                "action": action,
                "destination": request.destination,
                "days": request.days,
                "spots": [spot.model_dump(mode="json") for spot in analysis_output.spots],
                "trip_id": plan.trip_id,
            },
        )

        # Step 10: Record to long-term history
        self.memory.record_trip(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            trip_data={
                "trip_id": plan.trip_id,
                "destination": request.destination,
                "days": request.days,
                "kept_spots": [spot.name for spot in analysis_output.spots if spot.status in ("推荐", "用户确认")],
            },
        )

        # Build trace
        total_trace = [
            AgentTrace(
                agent=self.name,
                status="completed",
                message="Dispatched Strategy, Query, Analysis, and Report agents.",
                started_at=started,
                finished_at=utc_now(),
                metadata={
                    "action": action,
                    "model": self.llm.effective_model,
                },
            )
        ]
        trace = (
            total_trace
            + strategy_output.trace
            + query_output.trace
            + image_output.trace
            + analysis_trace
            + report_trace
        )

        return TravelPlanResponse(
            success=True,
            message="Trip plan generated." if action == "plan_trip" else "Trip plan modified.",
            total=total_output,
            strategy=strategy_output,
            analysis=analysis_output,
            report=report_output,
            plan=plan,
            query=query_output,
            images=image_output,
            preference=strategy_output.memory,
            reports=report_output.artifacts,
            trace=trace,
        )

    async def research(self, request: QueryResearchRequest) -> QueryAgentOutput:
        """Standalone research endpoint."""
        return await self.query_agent.run(request)

    async def analyze_images(self, request: ImageAnalysisRequest) -> ImageAgentOutput:
        """Standalone image analysis endpoint."""
        return await self.image_agent.run(request)

    async def get_preferences(self, user_id: str) -> PreferenceMemory:
        """Get user preferences from long-term memory."""
        result = await self.strategy_agent.load(user_id)
        return result.memory

    async def update_preferences(
        self,
        user_id: str,
        request: PreferenceUpdateRequest,
    ) -> PreferenceMemory:
        """Update user preferences in long-term memory."""
        return await self.strategy_agent.update(user_id, request)


# Backward compatibility alias
CoordinatorAgent = TotalAgent
