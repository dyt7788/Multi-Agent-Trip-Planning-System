"""Build destination-specific travel search queries."""

from __future__ import annotations

from typing import List

from app.models.schemas import QueryResearchRequest, StrategyAgentOutput, StrategyQueryInput, TripPlanRequest
from QueryEngine.llms.query_llm import QueryLLMClient
from TravelCore.text import dedupe


class TravelQueryBuilder:
    def __init__(self, llm: QueryLLMClient | None = None) -> None:
        self.llm = llm or QueryLLMClient()

    async def build(self, request: TripPlanRequest | QueryResearchRequest | StrategyAgentOutput | StrategyQueryInput) -> List[str]:
        fallback = self._fallback_queries(request)
        if not self.llm.configured:
            return fallback

        payload = await self.llm.complete_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You generate concise web search queries for travel research. "
                        "Return JSON: {\"queries\": [\"...\"]}. Queries must target "
                        "guides, blogs, forums, routes, food, attractions, and local tips."
                    ),
                },
                {"role": "user", "content": self._request_text(request)},
            ],
            fallback={"queries": fallback},
            model=self.llm.query_model,
        )
        return dedupe(payload.get("queries", fallback), 8) or fallback

    def _fallback_queries(self, request: TripPlanRequest | QueryResearchRequest | StrategyAgentOutput | StrategyQueryInput) -> List[str]:
        destination = request.destination.strip()
        if isinstance(request, StrategyAgentOutput):
            request = request.query
        if isinstance(request, StrategyQueryInput):
            scenic_types = " ".join(request.scenic_types)
            required_info = " ".join(request.required_info)
            days = f"{request.days}天" if request.days else ""
            return dedupe(
                [
                    f"{destination} {days} {scenic_types} 旅游攻略 景点 行程 路线 {required_info}",
                    f"{destination} {scenic_types} 必去景点 美食 交通 住宿 避坑",
                    f"{destination} {scenic_types} 游记 本地人推荐 小红书 马蜂窝 穷游",
                ]
                + [f"{destination} {keyword} 旅行攻略" for keyword in request.keywords],
                8,
            )
        if isinstance(request, QueryResearchRequest) and request.keywords:
            return dedupe([f"{destination} {keyword} 旅游 攻略" for keyword in request.keywords], 8)

        preferences = " ".join(getattr(request, "preferences", []) or [])
        days = getattr(request, "days", None)
        day_query = f"{days}天" if days else ""
        free_text = getattr(request, "free_text", "") or ""
        return dedupe(
            [
                f"{destination} {day_query} 旅游攻略 热门景点 路线 餐厅 活动 {preferences}",
                f"{destination} 自由行 行程 建议 避坑 {free_text}",
                f"{destination} 游记 博客 论坛 本地人 推荐",
                f"{destination} 小红书 旅行 笔记 打卡 美食",
                f"{destination} 马蜂窝 穷游 攻略",
            ],
            8,
        )

    @staticmethod
    def _request_text(request: TripPlanRequest | QueryResearchRequest | StrategyAgentOutput | StrategyQueryInput) -> str:
        return request.model_dump_json(indent=2)
