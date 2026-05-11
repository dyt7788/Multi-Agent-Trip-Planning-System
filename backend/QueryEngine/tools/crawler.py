"""Article crawler for public travel pages."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

import httpx

from app.config import Settings, get_settings
from app.models.schemas import ArticleContent
from TravelCore.text import clip_text, normalize_space, strip_html


class ArticleCrawler:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    async def fetch(self, url: str) -> ArticleContent:
        if not self.settings.enable_live_web:
            return ArticleContent(url=url)
        headers = {"User-Agent": self.settings.request_user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout, headers=headers, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    return ArticleContent(url=url)
                body = response.text
        except Exception:
            return ArticleContent(url=url)

        title = self._title(body)
        image_urls = self._images(body, response.url.__str__())
        text = strip_html(body)
        return ArticleContent(title=title, url=url, text=clip_text(text, 9000), image_urls=image_urls[:12])

    @staticmethod
    def _title(body: str) -> str:
        for pattern in (
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r"<title[^>]*>(.*?)</title>",
            r"<h1[^>]*>(.*?)</h1>",
        ):
            match = re.search(pattern, body, re.I | re.S)
            if match:
                return normalize_space(strip_html(match.group(1)))
        return ""

    @staticmethod
    def _images(body: str, base_url: str) -> list[str]:
        urls: list[str] = []
        for pattern in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
        ):
            for match in re.finditer(pattern, body, re.I):
                src = match.group(1).strip()
                if not src or src.startswith("data:"):
                    continue
                urls.append(urljoin(base_url, src))
        seen: set[str] = set()
        result: list[str] = []
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

