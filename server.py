"""Web interface for vc-signal-scanner.

Run with: uvicorn web:app --reload
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import add_subscriber
from src.models import InvestmentThesis
from src.sources import HackerNewsSource, GitHubTrendingSource, ProductHuntSource, RSSFeedSource, RedditSource, LaunchesSource
from src.scoring import ThesisScorer

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

PRELOADED_PATH = BASE_DIR / "data" / "preloaded.json"
SCAN_COUNT_PATH = Path("/tmp/scan_count.json")
MAX_DAILY_SCANS = 10

EU_RSS_FEEDS = [
    "https://tech.eu/feed/",
    "https://sifted.eu/feed",
    "https://www.eu-startups.com/feed/",
    "https://www.startupreporter.eu/feed/",
    "https://europeanstartupinsider.com/feed/",
    "https://www.seedtable.com/feed/",
    "https://www.vestbee.com/blog/feed/",
    "https://www.trendingtopics.eu/feed/",
]


def _load_preloaded() -> dict:
    if PRELOADED_PATH.exists():
        return json.loads(PRELOADED_PATH.read_text())
    return {"signals": [], "total_scanned": 0, "generated_at": ""}


def _get_scan_count() -> int:
    if not SCAN_COUNT_PATH.exists():
        return 0
    data = json.loads(SCAN_COUNT_PATH.read_text())
    if data.get("date") != str(date.today()):
        return 0
    return data.get("count", 0)


def _increment_scan_count() -> int:
    count = _get_scan_count()
    new_count = count + 1
    try:
        SCAN_COUNT_PATH.parent.mkdir(exist_ok=True)
        SCAN_COUNT_PATH.write_text(json.dumps({"date": str(date.today()), "count": new_count}))
    except (OSError, PermissionError):
        pass
    return new_count


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    scans_used = _get_scan_count()
    preloaded = _load_preloaded()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "signals": [],
            "preloaded": preloaded.get("signals", []),
            "total_scanned": 0,
            "generated_at": "",
            "has_api_key": bool(os.getenv("ANTHROPIC_API_KEY")),
            "scans_remaining": max(0, MAX_DAILY_SCANS - scans_used),
            "max_scans": MAX_DAILY_SCANS,
        },
    )


@app.get("/api/scan-status")
async def scan_status():
    used = _get_scan_count()
    return {"remaining": max(0, MAX_DAILY_SCANS - used), "max": MAX_DAILY_SCANS}


@app.post("/api/scan")
async def scan(request: Request):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not configured on server."}, status_code=503)

    used = _get_scan_count()
    if used >= MAX_DAILY_SCANS:
        return JSONResponse({"error": f"Daily scan limit reached ({MAX_DAILY_SCANS}/day). Check back tomorrow."}, status_code=429)

    body = await request.json()
    selected_sources = body.get("sources", ["hackernews", "github", "producthunt", "rss"])

    thesis = InvestmentThesis(
        fund_name="Custom",
        stage=body.get("stage", "Series A"),
        geography=body.get("geography", "Founders from DACH and CEE regions, companies with global ambition"),
        sectors=body.get("sectors", ["AI and machine learning infrastructure", "Developer tools and platforms"]),
        positive_signals=[
            "Strong revenue growth or clear monetization",
            "Expanding engineering team",
            "European founding team with global product",
        ],
        negative_signals=[
            "Pre-product, idea stage only",
            "Consumer social / ad-dependent model",
            "No clear technical differentiation",
        ],
    )

    fetch_tasks = []
    if "hackernews" in selected_sources:
        fetch_tasks.append(HackerNewsSource({"min_score": 50, "categories": ["show", "top"], "max_items": 25}).fetch())
    if "github" in selected_sources:
        fetch_tasks.append(GitHubTrendingSource({"languages": ["python", "typescript", "rust", "go"], "min_stars_24h": 50, "max_items": 20}).fetch())
    if "producthunt" in selected_sources:
        ph_config = {"min_votes": 50, "max_items": 20, "api_token": os.getenv("PH_API_TOKEN")}
        fetch_tasks.append(ProductHuntSource(ph_config).fetch())
    if "rss" in selected_sources:
        fetch_tasks.append(RSSFeedSource({"feeds": EU_RSS_FEEDS, "max_items_per_feed": 10, "max_age_hours": 48}).fetch())
    if "reddit" in selected_sources:
        fetch_tasks.append(RedditSource({"min_score": 20, "max_items_per_sub": 15}).fetch())
    if "launches" in selected_sources:
        fetch_tasks.append(LaunchesSource({"max_items_per_feed": 20, "max_age_hours": 72}).fetch())

    if not fetch_tasks:
        return JSONResponse({"error": "No sources selected."}, status_code=400)

    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    signals = []
    for r in results:
        if not isinstance(r, Exception):
            signals.extend(r)

    # Pre-filter to top 6 signals by source score to stay within API rate limits
    signals = sorted(signals, key=lambda s: s.score or 0, reverse=True)[:6]

    scorer = ThesisScorer(thesis=thesis, api_key=api_key)
    scored = await scorer.score_signals(signals)
    relevant = scorer.filter_relevant(scored, threshold=6.0)

    new_count = _increment_scan_count()

    return JSONResponse({
        "total_scanned": len(signals),
        "scans_remaining": max(0, MAX_DAILY_SCANS - new_count),
        "signals": [
            {
                "title": s.signal.title,
                "source": s.signal.source.value,
                "source_label": s.signal.source.value.replace("_", " ").title(),
                "url": s.signal.url,
                "score": s.signal.score,
                "relevance_score": s.relevance_score,
                "reasoning": s.reasoning,
                "thesis_alignment": s.thesis_alignment,
                "red_flags": s.red_flags,
                "risk": s.risk,
                "location": s.location,
                "website": s.website,
                "founders": s.founders,
                "previous_rounds": s.previous_rounds,
                "stage": s.stage,
            }
            for s in relevant
        ],
    })


@app.get("/api/demo")
async def demo():
    """Return preloaded curated signals instantly — no API call needed."""
    data = _load_preloaded()
    signals = data.get("signals", [])
    return JSONResponse({
        "total_scanned": len(signals),
        "scans_remaining": None,
        "demo": True,
        "signals": [
            {
                "title": s.get("title", ""),
                "source": s.get("source", "rss"),
                "source_label": s.get("source_label", "EU RSS"),
                "url": s.get("url", ""),
                "score": s.get("score"),
                "relevance_score": s.get("relevance_score", 0),
                "reasoning": s.get("reasoning", ""),
                "thesis_alignment": s.get("thesis_alignment", []),
                "red_flags": s.get("red_flags", []),
                "risk": s.get("risk", ""),
                "location": s.get("location", ""),
                "website": s.get("website", ""),
                "founders": s.get("founders", []),
                "previous_rounds": s.get("previous_rounds", ""),
                "stage": s.get("stage", ""),
            }
            for s in signals
        ],
    })


@app.post("/api/subscribe")
async def subscribe(email: str = Form(...)):
    is_new = add_subscriber(email.strip().lower())
    return JSONResponse({"ok": True, "new": is_new})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("web:app", host="0.0.0.0", port=port, reload=True)
