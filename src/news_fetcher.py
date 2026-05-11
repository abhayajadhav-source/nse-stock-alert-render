"""Google News RSS fetcher — no API key, no quota."""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List

import feedparser

from config import HIGH_PRIORITY_KEYWORDS, NEWS_LOOKBACK_HOURS
from stock_list import get_search_terms

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    link: str
    published: datetime
    source: str
    is_high_priority: bool = False
    matched_keywords: List[str] = None

    def __post_init__(self):
        if self.matched_keywords is None:
            self.matched_keywords = []


def _build_query_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(f'"{query}"')
    return (
        f"https://news.google.com/rss/search?"
        f"q={encoded}+when:1d"
        f"&hl=en-IN&gl=IN&ceid=IN:en"
    )


def _parse_published(entry) -> datetime:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _check_keywords(title: str) -> List[str]:
    title_lower = title.lower()
    return [kw for kw in HIGH_PRIORITY_KEYWORDS if kw.lower() in title_lower]


def fetch_news_for_stock(symbol: str, max_items: int = 5) -> List[NewsItem]:
    search_terms = get_search_terms(symbol)
    if not search_terms:
        return []

    url = _build_query_url(search_terms[0])
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.warning("Feed parse failed for %s: %s", symbol, e)
        return []

    if not feed.entries:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_LOOKBACK_HOURS)
    results: List[NewsItem] = []

    for entry in feed.entries[:max_items * 2]:
        published = _parse_published(entry)
        if published < cutoff:
            continue
        title = getattr(entry, "title", "").strip()
        if not title:
            continue
        if not any(term.lower() in title.lower() for term in search_terms):
            continue
        matched = _check_keywords(title)
        source  = title.rsplit(" - ", 1)[-1] if " - " in title else "Unknown"
        results.append(NewsItem(
            title=title,
            link=getattr(entry, "link", ""),
            published=published,
            source=source,
            is_high_priority=bool(matched),
            matched_keywords=matched,
        ))
        if len(results) >= max_items:
            break
    return results
