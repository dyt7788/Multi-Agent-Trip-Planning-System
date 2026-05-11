"""Search adapters for public travel content."""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from app.config import Settings, get_settings
from app.models.schemas import WebSource
from TravelCore.text import normalize_space


@dataclass(frozen=True)
class TravelSearchStrategy:
    """A small, agent-friendly search mode for real travel planning."""

    name: str
    query_suffix: str
    search_depth: str = "basic"
    time_range: Optional[str] = None
    include_domains: tuple[str, ...] = ()


GUIDE_STRATEGY = TravelSearchStrategy(
    name="guide",
    query_suffix="旅行攻略 行程 路线 景点 美食 交通 住宿 避坑",
)
CURRENT_STRATEGY = TravelSearchStrategy(
    name="current",
    query_suffix="最新 开放时间 门票预约 交通 官方公告",
    time_range="m",
)
COMMUNITY_STRATEGY = TravelSearchStrategy(
    name="community",
    query_suffix="游记 攻略 避坑 本地人推荐 美食 路线",
    include_domains=(
        "mafengwo.cn",
        "qyer.com",
        "xiaohongshu.com",
        "tripadvisor.cn",
        "tripadvisor.com",
    ),
)


class SearchTool:
    """Searches destination guides with a compact set of travel search modes."""

    current_info_hints = (
        "最新",
        "现在",
        "实时",
        "开放",
        "闭馆",
        "关闭",
        "营业",
        "门票",
        "预约",
        "限流",
        "运营时间",
        "停运",
        "管制",
        "时刻表",
        "票价",
        "签证",
        "天气",
        "台风",
        "雨季",
        "旺季",
        "淡季",
        "花期",
        "樱花",
        "雪季",
        "节假日",
        "春节",
        "国庆",
        "current",
        "latest",
        "opening hours",
        "ticket",
        "closed",
        "transport",
        "weather",
        "visa",
    )
    community_hints = (
        "小红书",
        "马蜂窝",
        "穷游",
        "论坛",
        "游记",
        "笔记",
        "避坑",
        "本地人",
        "亲测",
        "打卡",
        "tripadvisor",
        "forum",
        "blog",
        "review",
        "local tips",
    )
    blog_domains = ("blog", "medium.com", "lofter.com")
    forum_domains = ("bbs.", "forum", "qyer.com", "tripadvisor.")

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.tavily_api_key = getattr(self.settings, "tavily_api_key", None) or os.getenv("TAVILY_API_KEY")
        self.tavily_api_url = (
            getattr(self.settings, "tavily_api_url", None)
            or os.getenv("TAVILY_API_URL")
            or "https://api.tavily.com/search"
        )

    async def search(self, query: str, limit: Optional[int] = None) -> List[WebSource]:
        """Run the best travel-search strategy for a QueryAgent query."""

        limit = limit or self.settings.max_search_results
        strategy = self._choose_strategy(query)

        if not self.settings.enable_live_web:
            return self._fallback_sources(query, limit, strategy)

        results = await self._tavily(query, limit, strategy)
        if results:
            return results

        if strategy.include_domains:
            broader_strategy = replace(strategy, include_domains=())
            results = await self._tavily(query, limit, broader_strategy)
            if results:
                return results

        if self.settings.search_api_url:
            results = await self._search_api(query, limit, strategy)
            if results:
                return results

        results = await self._duckduckgo(query, limit, strategy)
        return results or self._fallback_sources(query, limit, strategy)

    def _choose_strategy(self, query: str) -> TravelSearchStrategy:
        text = query.lower()
        if self._contains_hint(text, self.current_info_hints) or self._mentions_year(text):
            return CURRENT_STRATEGY
        if self._contains_hint(text, self.community_hints):
            return COMMUNITY_STRATEGY
        return GUIDE_STRATEGY

    async def _tavily(self, query: str, limit: int, strategy: TravelSearchStrategy) -> List[WebSource]:
        if not self.tavily_api_key:
            return []

        payload: dict[str, Any] = {
            "api_key": self.tavily_api_key,
            "query": self._strategy_query(query, strategy, include_domain_filters=False),
            "topic": "general",
            "search_depth": strategy.search_depth,
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
        }
        if strategy.time_range:
            payload["time_range"] = strategy.time_range
        if strategy.include_domains:
            payload["include_domains"] = list(strategy.include_domains)

        headers = {"User-Agent": self.settings.request_user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout, headers=headers) as client:
                response = await client.post(self.tavily_api_url, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception:
            return []

        rows = data.get("results", []) if isinstance(data, dict) else []
        return self._rows_to_sources(rows, limit)

    async def _search_api(self, query: str, limit: int, strategy: TravelSearchStrategy) -> List[WebSource]:
        headers = {"User-Agent": self.settings.request_user_agent}
        if self.settings.search_api_key:
            headers["Authorization"] = f"Bearer {self.settings.search_api_key}"

        params: dict[str, Any] = {
            "q": self._strategy_query(query, strategy, include_domain_filters=False),
            "query": self._strategy_query(query, strategy, include_domain_filters=False),
            "limit": limit,
        }
        if strategy.time_range:
            params["time_range"] = strategy.time_range
        if strategy.include_domains:
            params["include_domains"] = ",".join(strategy.include_domains)

        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout, headers=headers) as client:
                response = await client.get(self.settings.search_api_url, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []

        rows = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        return self._rows_to_sources(rows, limit)

    async def _duckduckgo(self, query: str, limit: int, strategy: TravelSearchStrategy) -> List[WebSource]:
        search_query = self._strategy_query(query, strategy, include_domain_filters=True)
        url = f"https://duckduckgo.com/html/?q={quote_plus(search_query)}"
        headers = {"User-Agent": self.settings.request_user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout, headers=headers, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                body = response.text
        except Exception:
            return []

        blocks = re.split(r'<div class="result', body)
        sources: List[WebSource] = []
        for block in blocks:
            link_match = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not link_match:
                continue
            href = html.unescape(link_match.group(1))
            title = normalize_space(re.sub(r"<[^>]+>", " ", html.unescape(link_match.group(2))))
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>',
                block,
                re.S,
            )
            snippet_raw = next((group for group in (snippet_match.groups() if snippet_match else []) if group), "")
            snippet = normalize_space(re.sub(r"<[^>]+>", " ", html.unescape(snippet_raw)))
            parsed = urlparse(href)
            if "duckduckgo.com" in parsed.netloc:
                href = unquote(parse_qs(parsed.query).get("uddg", [href])[0])
            sources.append(
                WebSource(
                    title=title,
                    url=href,
                    snippet=snippet,
                    source_type=self._infer_source_type(href, title, snippet),
                )
            )
            if len(sources) >= limit:
                break
        return sources

    def _rows_to_sources(self, rows: list[Any], limit: int) -> List[WebSource]:
        sources: List[WebSource] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url") or row.get("link")
            if not url or url in seen:
                continue
            seen.add(url)
            title = normalize_space(str(row.get("title") or ""))
            snippet = normalize_space(
                str(row.get("snippet") or row.get("content") or row.get("description") or "")
            )
            published_date = row.get("published_date")
            if published_date:
                snippet = normalize_space(f"{snippet} 更新/发布日期: {published_date}")
            sources.append(
                WebSource(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source_type=self._infer_source_type(url, title, snippet),
                    score=self._coerce_score(row.get("score")),
                )
            )
            if len(sources) >= limit:
                break
        return sources

    def _strategy_query(
        self,
        query: str,
        strategy: TravelSearchStrategy,
        *,
        include_domain_filters: bool,
    ) -> str:
        parts = [normalize_space(query), strategy.query_suffix]
        if strategy.name == "current" and not self._mentions_year(query):
            parts.append(str(date.today().year))
        if include_domain_filters and strategy.include_domains:
            domains = " OR ".join(f"site:{domain}" for domain in strategy.include_domains)
            parts.append(f"({domains})")
        return normalize_space(" ".join(part for part in parts if part))

    def _fallback_sources(self, query: str, limit: int, strategy: TravelSearchStrategy) -> List[WebSource]:
        search_query = self._strategy_query(query, strategy, include_domain_filters=False)
        encoded = quote_plus(search_query)
        templates = [
            ("Bing travel guide search", f"https://www.bing.com/search?q={encoded}"),
            ("Mafengwo travel guide search", f"https://www.mafengwo.cn/search/q.php?q={encoded}"),
            ("Qyer travel guide search", f"https://search.qyer.com/index?wd={encoded}"),
            ("Xiaohongshu travel notes search", f"https://www.xiaohongshu.com/search_result?keyword={encoded}"),
            ("Tripadvisor travel search", f"https://www.tripadvisor.com/Search?q={encoded}"),
        ]
        return [
            WebSource(
                title=title,
                url=url,
                snippet=f"Fallback {strategy.name} search entry for {search_query}. Configure TAVILY_API_KEY or SEARCH_API_URL for live results.",
                source_type="fallback",
            )
            for title, url in templates[:limit]
        ]

    def _infer_source_type(self, url: str, title: str, snippet: str) -> str:
        haystack = f"{url} {title} {snippet}".lower()
        if "xiaohongshu.com" in haystack:
            return "xiaohongshu"
        if any(domain in haystack for domain in self.forum_domains):
            return "forum"
        if any(domain in haystack for domain in self.blog_domains) or "blog" in haystack:
            return "blog"
        return "search"

    @staticmethod
    def _contains_hint(text: str, hints: tuple[str, ...]) -> bool:
        return any(hint.lower() in text for hint in hints)

    @staticmethod
    def _mentions_year(text: str) -> bool:
        return bool(re.search(r"\b20\d{2}\b", text))

    @staticmethod
    def _coerce_score(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0


async def search_web(query: str, limit: Optional[int] = None) -> List[WebSource]:
    """Convenience entry point for tool-style imports."""

    return await SearchTool().search(query, limit)
