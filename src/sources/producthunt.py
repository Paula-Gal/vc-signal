"""Product Hunt data source — daily product launches via GraphQL or RSS fallback."""

from __future__ import annotations

import logging

import httpx

from src.models import Signal, SourceType

logger = logging.getLogger(__name__)

PH_API_URL = "https://api.producthunt.com/v2/api/graphql"


class ProductHuntSource:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.min_votes = self.config.get("min_votes", 100)
        self.api_token = self.config.get("api_token")
        self.max_items = self.config.get("max_items", 30)

    async def fetch(self) -> list[Signal]:
        """Fetch today's top Product Hunt launches."""
        if not self.api_token:
            logger.info("ProductHunt: no API token — falling back to RSS. Set PH_API_TOKEN in .env")
            return await self._fetch_via_rss()
        return await self._fetch_via_graphql()

    async def _fetch_via_graphql(self) -> list[Signal]:
        """Fetch via PH GraphQL API (requires token)."""
        query = """
        query {
            posts(order: VOTES, first: %d) {
                edges {
                    node {
                        id name tagline description url website votesCount createdAt
                        topics { edges { node { name } } }
                        makers { name username }
                    }
                }
            }
        }
        """ % self.max_items

        signals = []
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    PH_API_URL,
                    json={"query": query},
                    headers={
                        "Authorization": f"Bearer {self.api_token}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                for edge in data.get("data", {}).get("posts", {}).get("edges", []):
                    node = edge.get("node", {})
                    votes = node.get("votesCount", 0)
                    if votes < self.min_votes:
                        continue

                    topics = [t["node"]["name"] for t in node.get("topics", {}).get("edges", [])]
                    makers = [m.get("name", "") for m in node.get("makers", [])]

                    signals.append(
                        Signal(
                            title=node.get("name", ""),
                            description=node.get("tagline", ""),
                            source=SourceType.PRODUCT_HUNT,
                            url=node.get("website") or node.get("url", ""),
                            score=votes,
                            tags=topics,
                            extra={
                                "ph_url": node.get("url", ""),
                                "makers": makers,
                                "full_description": node.get("description", ""),
                            },
                        )
                    )

            except httpx.HTTPError as e:
                logger.warning(f"ProductHunt API error: {e}")

        logger.info(f"ProductHunt: fetched {len(signals)} signals (min_votes={self.min_votes})")
        return signals

    async def _fetch_via_rss(self) -> list[Signal]:
        """Fallback: parse PH RSS feed when no API token is available."""
        import feedparser

        signals = []
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get("https://www.producthunt.com/feed")
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)

                for entry in feed.entries[: self.max_items]:
                    signals.append(
                        Signal(
                            title=entry.get("title", ""),
                            description=entry.get("summary", ""),
                            source=SourceType.PRODUCT_HUNT,
                            url=entry.get("link", ""),
                            tags=["rss-fallback"],
                        )
                    )
        except Exception as e:
            logger.warning(f"ProductHunt RSS fallback failed: {e}")

        logger.info(f"ProductHunt (RSS fallback): fetched {len(signals)} signals")
        return signals
