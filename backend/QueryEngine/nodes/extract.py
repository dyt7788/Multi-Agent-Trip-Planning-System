"""Structured extraction and synthesis node for travel research."""

from __future__ import annotations

import re
from typing import Iterable, List

from app.models.schemas import ArticleContent, ExtractedTravelInfo, WebSource
from QueryEngine.llms.query_llm import QueryLLMClient
from QueryEngine.prompts.research import STRUCTURE_RESEARCH_PROMPT
from TravelCore.text import dedupe, normalize_space


class TravelInfoExtractor:
    """Converts heterogeneous web evidence into structured travel facts."""

    def __init__(self, llm: QueryLLMClient | None = None) -> None:
        self.llm = llm or QueryLLMClient()

    async def extract(
        self,
        destination: str,
        sources: List[WebSource],
        articles: List[ArticleContent],
    ) -> ExtractedTravelInfo:
        fallback = self._heuristic_extract(destination, sources, articles)
        evidence = self._evidence_text(sources, articles)
        if not self.llm.configured or not evidence:
            return fallback

        payload = await self.llm.complete_json(
            [
                {"role": "system", "content": STRUCTURE_RESEARCH_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Destination: {destination}\n\n"
                        f"Evidence:\n{evidence}\n\n"
                        "Integrate the evidence and return JSON only."
                    ),
                },
            ],
            fallback=fallback.model_dump(),
            model=self.llm.synthesis_model,
        )
        return ExtractedTravelInfo(
            destination=destination,
            summary=normalize_space(payload.get("summary", fallback.summary)),
            attractions=dedupe(payload.get("attractions", fallback.attractions), 18),
            restaurants=dedupe(payload.get("restaurants", fallback.restaurants), 12),
            activities=dedupe(payload.get("activities", fallback.activities), 12),
            route_suggestions=dedupe(payload.get("route_suggestions", fallback.route_suggestions), 8),
            tips=dedupe(payload.get("tips", fallback.tips), 10),
            source_coverage=dedupe(payload.get("source_coverage", fallback.source_coverage), 8),
            source_urls=dedupe([source.url for source in sources] + [article.url for article in articles], 20),
        )

    @staticmethod
    def _evidence_text(sources: List[WebSource], articles: List[ArticleContent]) -> str:
        chunks: List[str] = []
        for index, source in enumerate(sources[:12], start=1):
            chunks.append(
                f"[Search {index}] title={source.title}\n"
                f"url={source.url}\n"
                f"snippet={source.snippet}\n"
                f"score={source.score}"
            )
        for index, article in enumerate(articles[:8], start=1):
            text = article.text[:2600] if article.text else ""
            chunks.append(
                f"[Article {index}] title={article.title}\n"
                f"url={article.url}\n"
                f"text={text}"
            )
        return "\n\n".join(chunk for chunk in chunks if chunk.strip())

    def _heuristic_extract(
        self,
        destination: str,
        sources: List[WebSource],
        articles: List[ArticleContent],
    ) -> ExtractedTravelInfo:
        corpus = "\n".join(
            [source.title + " " + source.snippet for source in sources]
            + [article.title + " " + article.text for article in articles]
        )
        attractions = self._candidates(corpus, ["景点", "公园", "博物馆", "古镇", "寺", "山", "湖", "海", "塔", "街"])
        restaurants = self._candidates(corpus, ["餐厅", "饭店", "小吃", "咖啡", "火锅", "面馆", "酒吧", "夜市"])
        activities = self._candidates(corpus, ["活动", "演出", "展览", "徒步", "骑行", "游船", "温泉", "市集"])
        route_suggestions = self._route_lines(corpus)
        tips = self._tips(corpus)

        if not attractions:
            attractions = [f"{destination}代表性景点", f"{destination}城市漫步区域", f"{destination}在地文化体验"]
        if not restaurants:
            restaurants = [f"{destination}本地风味餐厅", f"{destination}热门小吃街"]
        if not activities:
            activities = [f"{destination}夜游体验", f"{destination}城市文化活动"]
        if not route_suggestions:
            route_suggestions = [
                "上午安排核心景点，下午串联周边街区，晚上留给餐饮和夜景。",
                "优先把地理位置相近的景点放在同一天，减少跨城通勤。",
            ]
        if not tips:
            tips = [
                "热门景点建议提前预约或购买门票。",
                "保留每日一段弹性时间，应对排队、天气和交通变化。",
            ]

        source_coverage = self._source_coverage(sources, articles)
        return ExtractedTravelInfo(
            destination=destination,
            summary=f"已整合{len(sources)}条搜索结果和{len(articles)}篇可抓取文章，形成{destination}旅行结构化信息。",
            attractions=dedupe(attractions, 18),
            restaurants=dedupe(restaurants, 12),
            activities=dedupe(activities, 12),
            route_suggestions=dedupe(route_suggestions, 8),
            tips=dedupe(tips, 10),
            source_coverage=source_coverage,
            source_urls=dedupe([source.url for source in sources] + [article.url for article in articles], 20),
        )

    @staticmethod
    def _candidates(text: str, markers: Iterable[str]) -> List[str]:
        result: List[str] = []
        for marker in markers:
            pattern = rf"([\u4e00-\u9fa5A-Za-z0-9·（）()《》-]{{2,24}}{re.escape(marker)}[\u4e00-\u9fa5A-Za-z0-9·（）()《》-]{{0,16}})"
            result.extend(normalize_space(match.group(1)) for match in re.finditer(pattern, text))
        return result

    @staticmethod
    def _route_lines(text: str) -> List[str]:
        lines = re.split(r"[。.!?\n]", text)
        return [
            normalize_space(line)
            for line in lines
            if any(word in line for word in ("路线", "行程", "一日游", "两日游", "三日游", "Day", "day"))
        ][:12]

    @staticmethod
    def _tips(text: str) -> List[str]:
        lines = re.split(r"[。.!?\n]", text)
        return [
            normalize_space(line)
            for line in lines
            if any(word in line for word in ("预约", "门票", "避开", "交通", "开放时间", "注意", "排队"))
        ][:12]

    @staticmethod
    def _source_coverage(sources: List[WebSource], articles: List[ArticleContent]) -> List[str]:
        coverage: List[str] = []
        if sources:
            coverage.append(f"搜索结果 {len(sources)} 条")
        if articles:
            coverage.append(f"可抓取文章 {len(articles)} 篇")
        if any("xiaohongshu" in source.url or "小红书" in source.title for source in sources):
            coverage.append("小红书/社交平台线索")
        if any("mafengwo" in source.url or "马蜂窝" in source.title for source in sources):
            coverage.append("马蜂窝攻略线索")
        if any("qyer" in source.url or "穷游" in source.title for source in sources):
            coverage.append("穷游攻略线索")
        return dedupe(coverage, 8)
