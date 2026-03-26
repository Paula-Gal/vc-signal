# VC Signal Scanner

Thesis-driven startup signal monitor. Scans Hacker News, GitHub Trending, Product Hunt, and European RSS feeds, then uses Claude to score each signal against your investment thesis.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your ANTHROPIC_API_KEY
```

## Run

```bash
uvicorn web:app --reload
# → http://localhost:8000
```

## Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Required for live scanning |
| `PH_API_TOKEN` | Optional — enables Product Hunt GraphQL (falls back to RSS without it) |

Scan limit is 10 per day (resets at midnight). Change `MAX_DAILY_SCANS` in `web.py`.

## Thesis customization

The investment thesis (stage, geography, sectors, signals) is configured in the `/api/scan` handler in `web.py`.

## Project structure

```
web.py              FastAPI app and API routes
src/
  models.py         Signal, ScoredSignal, InvestmentThesis dataclasses
  sources/          Data fetchers (HN, GitHub, Product Hunt, RSS, Reddit, Launches)
  scoring/          Claude-based thesis scorer
app/
  db.py             SQLite subscriber store
data/               Preloaded demo signals and scan count cache
```

## Adding a source

```python
from src.models import Signal

class MySource:
    async def fetch(self) -> list[Signal]:
        ...
```

## License

MIT
