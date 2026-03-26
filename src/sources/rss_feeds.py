"""RSS Feed data source — European tech and startup news."""

from __future__ import annotations

import logging
from datetime import datetime

import feedparser
import httpx

from src.models import Signal, SourceType

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    "https://tech.eu/feed/",
    "https://sifted.eu/feed",
    "https://www.eu-startups.com/feed/",
    "https://www.trendingtopics.eu/feed/",
    "https://brutkasten.com/feed/",
    "https://techcrunch.com/feed/",
    "https://the-decoder.com/feed/",
]


class RSSFeedSource:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.feeds = self.config.get("feeds", DEFAULT_FEEDS)
        self.max_items_per_feed = self.config.get("max_items_per_feed", 15)
        self.max_age_hours = self.config.get("max_age_hours", 48)

    async def fetch(self) -> list[Signal]:
        """Fetch recent articles from all configured RSS feeds."""
        signals = []

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for feed_url in self.feeds:
                try:
                    feed_signals = await self._fetch_feed(client, feed_url)
                    signals.extend(feed_signals)
                except Exception as e:
                    logger.warning(f"RSS feed failed ({feed_url}): {e}")

        logger.info(f"RSS: fetched {len(signals)} articles from {len(self.feeds)} feeds")
        return signals

    async def _fetch_feed(self, client: httpx.AsyncClient, feed_url: str) -> list[Signal]:
        """Fetch and parse a single RSS feed."""
        try:
            resp = await client.get(feed_url, headers={"User-Agent": "vc-signal-scanner/1.0"})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.debug(f"HTTP error for {feed_url}: {e}")
            return []

        feed = feedparser.parse(resp.text)
        feed_title = feed.feed.get("title", feed_url)
        signals = []

        for entry in feed.entries[: self.max_items_per_feed]:
            try:
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6])
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    published = datetime(*entry.updated_parsed[:6])

                if published:
                    age_hours = (datetime.utcnow() - published).total_seconds() / 3600
                    if age_hours > self.max_age_hours:
                        continue

                title = entry.get("title", "").strip()
                if not title:
                    continue

                description = ""
                if hasattr(entry, "summary"):
                    description = self._clean_html(entry.summary)
                elif hasattr(entry, "description"):
                    description = self._clean_html(entry.description)

                if len(description) > 500:
                    description = description[:497] + "..."

                tags = []
                if hasattr(entry, "tags"):
                    tags = [t.get("term", "") for t in entry.tags if t.get("term")]

                signals.append(
                    Signal(
                        title=title,
                        description=description,
                        source=SourceType.RSS,
                        url=entry.get("link", ""),
                        tags=tags,
                        author=entry.get("author"),
                        discovered_at=published or datetime.utcnow(),
                        extra={"feed_title": feed_title, "feed_url": feed_url},
                    )
                )
            except Exception as e:
                logger.debug(f"Error parsing RSS entry: {e}")

        return signals

    @staticmethod
    def _clean_html(html_text: str) -> str:
        """Strip HTML tags from RSS content."""
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html_text, "html.parser").get_text(separator=" ", strip=True)
        except ImportError:
            import re
            return re.sub(r"<[^>]+>", "", html_text).strip()
