"""GitHub Trending data source — catches developer tools and open-source traction signals."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from src.models import Signal, SourceType

logger = logging.getLogger(__name__)

GITHUB_TRENDING_URL = "https://github.com/trending"


class GitHubTrendingSource:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.languages = self.config.get("languages", [])
        self.min_stars_today = self.config.get("min_stars_24h", 50)
        self.max_items = self.config.get("max_items", 30)

    async def fetch(self) -> list[Signal]:
        """Fetch trending GitHub repositories."""
        signals = []
        urls_to_fetch = [GITHUB_TRENDING_URL]

        for lang in self.languages:
            urls_to_fetch.append(f"{GITHUB_TRENDING_URL}/{lang}?since=daily")

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for url in urls_to_fetch:
                try:
                    resp = await client.get(url, headers={"User-Agent": "vc-signal-scanner/1.0"})
                    resp.raise_for_status()
                    signals.extend(self._parse_trending_page(resp.text))
                except httpx.HTTPError as e:
                    logger.warning(f"GitHub trending fetch failed for {url}: {e}")

        # Deduplicate by URL
        seen = set()
        unique_signals = []
        for s in signals:
            if s.url not in seen:
                seen.add(s.url)
                unique_signals.append(s)

        logger.info(f"GitHub: fetched {len(unique_signals)} trending repos")
        return unique_signals[: self.max_items]

    def _parse_trending_page(self, html: str) -> list[Signal]:
        """Parse the GitHub trending page HTML."""
        signals = []
        soup = BeautifulSoup(html, "html.parser")

        for article in soup.select("article.Box-row"):
            try:
                name_el = article.select_one("h2 a")
                if not name_el:
                    continue
                repo_path = name_el.get("href", "").strip("/")
                repo_url = f"https://github.com/{repo_path}"
                repo_name = repo_path.split("/")[-1] if "/" in repo_path else repo_path

                desc_el = article.select_one("p")
                description = desc_el.get_text(strip=True) if desc_el else ""

                lang_el = article.select_one("[itemprop='programmingLanguage']")
                language = lang_el.get_text(strip=True) if lang_el else "Unknown"

                stars_today = 0
                for el in article.select("span.d-inline-block"):
                    text = el.get_text(strip=True)
                    if "stars today" in text or "stars this week" in text:
                        try:
                            stars_today = int(text.split()[0].replace(",", ""))
                        except ValueError:
                            pass

                total_stars = 0
                for link in article.select("a.Link--muted"):
                    if "/stargazers" in link.get("href", ""):
                        try:
                            total_stars = int(link.get_text(strip=True).replace(",", ""))
                        except ValueError:
                            pass
                        break

                if stars_today < self.min_stars_today:
                    continue

                tags = [language.lower()] if language != "Unknown" else []
                tags.append("open-source")

                signals.append(
                    Signal(
                        title=f"{repo_path} — {repo_name}",
                        description=description,
                        source=SourceType.GITHUB,
                        url=repo_url,
                        score=stars_today,
                        tags=tags,
                        extra={
                            "language": language,
                            "total_stars": total_stars,
                            "stars_today": stars_today,
                            "owner": repo_path.split("/")[0] if "/" in repo_path else "",
                        },
                    )
                )
            except Exception as e:
                logger.debug(f"Error parsing GitHub trending article: {e}")

        return signals
