"""Travel relevance filter for search results."""

from __future__ import annotations

from typing import List

from app.models.schemas import WebSource


class TravelSourceFilter:
    travel_keywords = (
        "旅游",
        "旅行",
        "攻略",
        "自由行",
        "行程",
        "路线",
        "景点",
        "餐厅",
        "美食",
        "游记",
        "小红书",
        "马蜂窝",
        "穷游",
        "trip",
        "travel",
        "guide",
        "itinerary",
        "restaurant",
        "attraction",
    )

    def filter(self, destination: str, sources: List[WebSource], limit: int) -> List[WebSource]:
        scored: list[tuple[float, WebSource]] = []
        destination_key = destination.lower()
        for source in sources:
            haystack = f"{source.title} {source.snippet} {source.url}".lower()
            score = float(source.score or 0)
            if destination_key and destination_key in haystack:
                score += 3
            score += sum(1 for keyword in self.travel_keywords if keyword.lower() in haystack)
            if source.source_type == "fallback":
                score += 1
            if score > 0:
                source.score = score
                scored.append((score, source))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [source for _, source in scored[:limit]]
