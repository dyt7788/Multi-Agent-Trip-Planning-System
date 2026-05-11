"""Small deterministic text helpers used by agent fallbacks."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Iterable, List


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stable_id(*parts: object, size: int = 12) -> str:
    raw = "|".join(str(part) for part in parts if part is not None)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:size]


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return normalize_space(value)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def dedupe(items: Iterable[str], limit: int | None = None) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        normalized = normalize_space(str(item))
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if limit and len(result) >= limit:
            break
    return result


def clip_text(value: str, max_chars: int = 6000) -> str:
    value = normalize_space(value)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def html_escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)

