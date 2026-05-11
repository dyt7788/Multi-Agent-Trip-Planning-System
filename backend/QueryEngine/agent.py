"""QueryAgent: searches travel websites and integrates destination guidance."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from app.config import Settings, get_settings
from app.models.schemas import (
    AgentTrace,
    ExtractedTravelInfo,
    QueryAgentOutput,
    QueryResearchRequest,
    QuerySpotSummary,
    StrategyAgentOutput,
    StrategyQueryInput,
    TripPlanRequest,
    WebSource,
)
from QueryEngine.llms.query_llm import QueryLLMClient
from QueryEngine.nodes.extract import TravelInfoExtractor
from QueryEngine.nodes.query_builder import TravelQueryBuilder
from QueryEngine.nodes.source_filter import TravelSourceFilter
from QueryEngine.tools.crawler import ArticleCrawler
from QueryEngine.tools.search import SearchTool
from TravelCore.text import utc_now


class QueryAgent:
    """Research Agent for destination-specific travel information.

    The module follows the BettaFish-style Engine boundary: the agent
    coordinates QueryEngine nodes/tools and owns its LLM model selection.
    """

    name = "QueryAgent"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm: Optional[QueryLLMClient] = None,
        search_tool: Optional[SearchTool] = None,
        crawler: Optional[ArticleCrawler] = None,
        query_builder: Optional[TravelQueryBuilder] = None,
        source_filter: Optional[TravelSourceFilter] = None,
        extractor: Optional[TravelInfoExtractor] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or QueryLLMClient(self.settings)
        self.search_tool = search_tool or SearchTool(self.settings)
        self.crawler = crawler or ArticleCrawler(self.settings)
        self.query_builder = query_builder or TravelQueryBuilder(self.llm)
        self.source_filter = source_filter or TravelSourceFilter()
        self.extractor = extractor or TravelInfoExtractor(self.llm)

    async def run(
        self,
        request: TripPlanRequest | QueryResearchRequest | StrategyAgentOutput | StrategyQueryInput,
    ) -> QueryAgentOutput:
        started = utc_now()
        destination = request.destination
        if isinstance(request, StrategyAgentOutput):
            search_request = request.query
            days = request.days
        else:
            search_request = request
            days = getattr(request, "days", None)
        queries = await self.query_builder.build(search_request)
        per_query_limit = getattr(search_request, "limit", self.settings.max_search_results)

        raw_sources = await self._search_all(queries, per_query_limit)
        filtered_sources = self.source_filter.filter(
            destination=destination,
            sources=raw_sources,
            limit=self.settings.max_crawled_articles * 2,
        )
        articles = await self._crawl_sources(filtered_sources)
        extracted = await self.extractor.extract(destination, filtered_sources, articles)

        return QueryAgentOutput(
            destination=destination,
            days=days,
            spots_summary=self._spot_summaries(extracted),
            raw_summary=extracted.summary,
            sources=filtered_sources,
            articles=articles,
            extracted=extracted,
            trace=[
                AgentTrace(
                    agent=self.name,
                    status="completed",
                    message=(
                        f"Searched {len(queries)} queries, kept {len(filtered_sources)} "
                        f"travel sources, crawled {len(articles)} articles."
                    ),
                    started_at=started,
                    finished_at=utc_now(),
                    metadata={
                        "queries": queries,
                        "raw_source_count": len(raw_sources),
                        "query_model": self.llm.query_model,
                        "synthesis_model": self.llm.synthesis_model,
                    },
                )
            ],
        )

    async def _search_all(self, queries: List[str], limit: int) -> List[WebSource]:
        tasks = [self.search_tool.search(query, limit) for query in queries]
        rows = await asyncio.gather(*tasks)
        seen: set[str] = set()
        sources: List[WebSource] = []
        for row in rows:
            for source in row:
                if source.url in seen:
                    continue
                seen.add(source.url)
                sources.append(source)
        return sources

    async def _crawl_sources(self, sources: List[WebSource]):
        targets = [source for source in sources if source.source_type != "fallback"][: self.settings.max_crawled_articles]
        if not targets:
            return []
        articles = await asyncio.gather(*(self.crawler.fetch(source.url) for source in targets))
        return [article for article in articles if article.text or article.title]

    @staticmethod
    def _spot_summaries(extracted: ExtractedTravelInfo) -> List[QuerySpotSummary]:
        spots: List[QuerySpotSummary] = []
        for name in extracted.attractions[:12]:
            keywords = [
                keyword
                for keyword in ("历史", "自然", "博物馆", "美食", "亲子", "夜景", "户外", "园林")
                if keyword in f"{name} {extracted.summary}"
            ]
            spots.append(
                QuerySpotSummary(
                    name=name,
                    keywords=keywords or ["景点"],
                    brief_desc=extracted.summary or f"{name} 是本次攻略检索中出现的候选景点。",
                )
            )
        return spots
