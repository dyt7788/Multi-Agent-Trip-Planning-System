from dataclasses import dataclass, field
from typing import List

from app.models.schemas import ArticleContent, WebSource


@dataclass
class QueryState:
    destination: str
    queries: List[str] = field(default_factory=list)
    sources: List[WebSource] = field(default_factory=list)
    articles: List[ArticleContent] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

