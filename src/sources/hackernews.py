"""Hacker News data source.

Pulls top stories and Show HN posts from the HN API.
Useful for catching new product launches, technical discussions,
and startup announcements before they hit mainstream press.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from src.models import Signal, SourceType

logger = logging.getLogger(__name__)

HN_API_BASE = "https://hacker-news.firebaseio.com/v0"


class HackerNewsSource:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.min_score = self.config.get("min_score", 50)
        self.categories = self.config.get("categories", ["show", "top"])
        self.max_items = self.config.get("max_items", 60)

    async def fetch(self) -> list[Signal]:
        """Fetch recent HN stories that meet the minimum score threshold."""
        signals = []
        async with httpx.AsyncClient(timeout=30) as client:
            story_ids = await self._get_story_ids(client)
            # Fetch stories concurrently in batches
            tasks = [self._fetch_story(client, sid) for sid in story_ids[: self.max_items]]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Signal):
                    signals.append(result)
                elif isinstance(result, Exception):
                    logger.debug(f"Failed to fetch story: {result}")

        logger.info(f"HackerNews: fetched {len(signals)} signals (min_score={self.min_score})")
        return signals

    async def _get_story_ids(self, client: httpx.AsyncClient) -> list[int]:
        """Get story IDs from configured categories."""
        all_ids = []
        category_endpoints = {
            "top": f"{HN_API_BASE}/topstories.json",
            "new": f"{HN_API_BASE}/newstories.json",
            "show": f"{HN_API_BASE}/showstories.json",
            "ask": f"{HN_API_BASE}/askstories.json",
        }
        for cat in self.categories:
            if cat in category_endpoints:
                try:
                    resp = await client.get(category_endpoints[cat])
                    resp.raise_for_status()
                    ids = resp.json()
                    all_ids.extend(ids[:30])  # top 30 per category
                except httpx.HTTPError as e:
                    logger.warning(f"Failed to fetch {cat} stories: {e}")
        return list(dict.fromkeys(all_ids))  # dedupe preserving order

    async def _fetch_story(self, client: httpx.AsyncClient, story_id: int) -> Signal | None:
        """Fetch a single story and convert to Signal if it meets criteria."""
        try:
            resp = await client.get(f"{HN_API_BASE}/item/{story_id}.json")
            resp.raise_for_status()
            item = resp.json()

            if not item or item.get("type") != "story":
                return None

            score = item.get("score", 0)
            if score < self.min_score:
                return None

            title = item.get("title", "")
            url = item.get("url", f"https://news.ycombinator.com/item?id={story_id}")
            hn_url = f"https://news.ycombinator.com/item?id={story_id}"

            # Determine tags
            tags = []
            if title.startswith("Show HN:"):
                tags.append("show-hn")
            if title.startswith("Ask HN:"):
                tags.append("ask-hn")
            if title.startswith("Launch HN:"):
                tags.append("launch-hn")

            return Signal(
                title=title,
                description=f"HN discussion with {score} points and {item.get('descendants', 0)} comments.",
                source=SourceType.HACKER_NEWS,
                url=url,
                score=score,
                author=item.get("by"),
                tags=tags,
                discovered_at=datetime.utcfromtimestamp(item.get("time", 0)),
                extra={
                    "hn_url": hn_url,
                    "comments": item.get("descendants", 0),
                    "story_id": story_id,
                },
            )
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug(f"Error fetching story {story_id}: {e}")
            return None
