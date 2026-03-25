# vc-signal-scanner

A thesis-driven startup signal monitor built for European venture capital. It scans multiple data sources and scores each signal against configurable investment theses, delivering a daily digest of the most relevant opportunities.

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/vc-signal-scanner.git
cd vc-signal-scanner
pip install -r requirements.txt

cp .env.example .env
# Add your API_KEY to .env

python main.py
python main.py --thesis theses/my_thesis.yaml
python main.py --slack
```

## Configuration

### Investment Thesis

Define your fund's thesis in YAML (theses/my_thesis.yaml):

```yaml
fund_name: "My Fund"
thesis:
  stage: "Post product-market fit, typically Series A"
  geography: "Europe"
  sectors:
    - "AI and machine learning infrastructure"
    - "Developer tools and platforms"
  signals:
    positive:
      - "Strong revenue growth"
      - "Expanding engineering team"
      - "Clear product-market fit"
    negative:
      - "Pre-product stage"
      - "Unclear business model"
      - "No technical differentiation"
```

### Data Sources

Configure sources in config.yaml:

```yaml
sources:
  hackernews:
    enabled: true
    min_score: 50
  producthunt:
    enabled: true
    min_votes: 100
  github:
    enabled: true
    languages: ["python", "typescript", "rust"]
    min_stars_24h: 50
  rss:
    enabled: true
```

## Architecture

```
vc-signal-scanner/
├── main.py
├── config.yaml
├── requirements.txt
├── theses/
│   └── my_thesis.yaml
├── src/
│   ├── models.py
│   ├── sources/
│   │   ├── hackernews.py
│   │   ├── producthunt.py
│   │   ├── github_trending.py
│   │   └── rss_feeds.py
│   ├── scoring/
│   │   └── thesis_scorer.py
│   └── output/
│       ├── markdown_report.py
│       └── slack_webhook.py
└── sample_output/
    └── daily_digest_example.md
```

## Adding New Sources

Implement the following interface:

```python
from src.models import Signal

class MySource:
    async def fetch(self) -> list[Signal]:
        # Return a list of Signal objects
        pass
```

## License

MIT


