#!/usr/bin/env python3
"""
vc-signal-scanner — Thesis-driven startup signal monitor for European VC.

Scans multiple data sources, scores signals against a configurable
investment thesis using an LLM, and delivers a daily digest.

Usage:
    python main.py                          # Run with defaults
    python main.py --thesis theses/my.yaml  # Custom thesis
    python main.py --slack                  # Also send to Slack
    python main.py --threshold 5.0          # Lower relevance bar
    python main.py --dry-run                # Fetch only, no scoring
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.models import InvestmentThesis, Signal
from src.sources import HackerNewsSource, ProductHuntSource, GitHubTrendingSource, RSSFeedSource
from src.scoring import ThesisScorer
try:
    from src.output import MarkdownReportGenerator, EmailNotifier
    _output_available = True
except ImportError:
    _output_available = False

load_dotenv()
console = Console()


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    path = Path(config_path)
    if not path.exists():
        console.print(f"[yellow]Config not found at {config_path}, using defaults[/yellow]")
        return {}
    with open(path) as f:
        return yaml.safe_load(f)


def load_thesis(thesis_path: str) -> InvestmentThesis:
    """Load investment thesis from YAML file."""
    with open(thesis_path) as f:
        data = yaml.safe_load(f)
    return InvestmentThesis.from_yaml(data)


async def fetch_all_signals(config: dict) -> list[Signal]:
    """Fetch signals from all enabled sources."""
    sources_config = config.get("sources", {})
    all_signals = []
    tasks = []

    # Initialize enabled sources
    if sources_config.get("hackernews", {}).get("enabled", True):
        hn = HackerNewsSource(sources_config.get("hackernews", {}))
        tasks.append(("Hacker News", hn.fetch()))

    if sources_config.get("producthunt", {}).get("enabled", True):
        ph_config = sources_config.get("producthunt", {})
        ph_config["api_token"] = os.getenv("PH_API_TOKEN")
        ph = ProductHuntSource(ph_config)
        tasks.append(("Product Hunt", ph.fetch()))

    if sources_config.get("github", {}).get("enabled", True):
        gh = GitHubTrendingSource(sources_config.get("github", {}))
        tasks.append(("GitHub Trending", gh.fetch()))

    if sources_config.get("rss", {}).get("enabled", True):
        rss = RSSFeedSource(sources_config.get("rss", {}))
        tasks.append(("RSS Feeds", rss.fetch()))

    # Fetch all sources concurrently
    with console.status("[bold blue]Fetching signals from all sources..."):
        results = await asyncio.gather(
            *[task for _, task in tasks],
            return_exceptions=True,
        )

    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            console.print(f"  [red]\u2717[/red] {name}: {result}")
        else:
            console.print(f"  [green]\u2713[/green] {name}: {len(result)} signals")
            all_signals.extend(result)

    return all_signals


def print_summary_table(scored_signals, threshold: float):
    """Print a rich table summarizing the scored signals."""
    table = Table(title="Signal Scores", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", justify="center", width=8)
    table.add_column("Signal", min_width=40)
    table.add_column("Source", width=14)
    table.add_column("Key Insight", min_width=30)

    for i, scored in enumerate(scored_signals[:20], 1):
        score = scored.relevance_score
        if score >= 8:
            score_style = "bold green"
        elif score >= threshold:
            score_style = "yellow"
        else:
            score_style = "dim"

        # Truncate reasoning
        insight = scored.reasoning[:80] + "..." if len(scored.reasoning) > 80 else scored.reasoning

        table.add_row(
            str(i),
            f"[{score_style}]{score:.1f}/10[/{score_style}]",
            scored.signal.title[:60],
            scored.signal.source.value,
            insight,
        )

    console.print()
    console.print(table)


@click.command()
@click.option("--thesis", default=None, help="Path to thesis YAML file")
@click.option("--config", "config_path", default="config.yaml", help="Path to config file")
@click.option("--threshold", default=None, type=float, help="Relevance score threshold (0-10)")
@click.option("--email", is_flag=True, help="Also send digest by email")
@click.option("--dry-run", is_flag=True, help="Fetch signals only, skip scoring")
@click.option("--output", "output_path", default=None, help="Custom output file path")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(thesis, config_path, threshold, email, dry_run, output_path, verbose):
    """vc-signal-scanner — Find startup signals that match your thesis."""

    # Setup logging
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s | %(name)s | %(message)s")

    # Load config
    config = load_config(config_path)
    scoring_config = config.get("scoring", {})

    if threshold is None:
        threshold = scoring_config.get("relevance_threshold", 6.0)

    # Load thesis
    thesis_path = thesis or config.get("default_thesis", "theses/3vc_default.yaml")
    try:
        investment_thesis = load_thesis(thesis_path)
    except FileNotFoundError:
        console.print(f"[red]Thesis file not found: {thesis_path}[/red]")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]{investment_thesis.fund_name}[/bold] Signal Scanner\n"
            f"Thesis: {thesis_path}\n"
            f"Threshold: {threshold}/10",
            title="vc-signal-scanner",
            border_style="blue",
        )
    )

    # Run async pipeline
    asyncio.run(_run_pipeline(
        config=config,
        thesis=investment_thesis,
        threshold=threshold,
        send_email=email,
        dry_run=dry_run,
        output_path=output_path,
    ))


async def _run_pipeline(
    config: dict,
    thesis: InvestmentThesis,
    threshold: float,
    send_email: bool,
    dry_run: bool,
    output_path: str | None,
):
    """Main async pipeline: fetch → score → output."""

    # 1. Fetch signals
    signals = await fetch_all_signals(config)
    console.print(f"\n[bold]Total signals fetched: {len(signals)}[/bold]")

    if not signals:
        console.print("[yellow]No signals found. Check your source configuration.[/yellow]")
        return

    if dry_run:
        console.print("[dim]Dry run — skipping scoring.[/dim]")
        for s in signals[:20]:
            console.print(f"  {s}")
        return

    # 2. Score signals
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY not set. Add it to your .env file.[/red]")
        sys.exit(1)

    scoring_config = config.get("scoring", {})
    scorer = ThesisScorer(
        thesis=thesis,
        api_key=api_key,
        model=scoring_config.get("model", "claude-sonnet-4-6"),
        requests_per_minute=scoring_config.get("requests_per_minute", 4),
    )

    with console.status("[bold blue]Scoring signals with LLM..."):
        scored_signals = await scorer.score_signals(signals)

    # 3. Display results
    relevant = scorer.filter_relevant(scored_signals, threshold)
    console.print(f"\n[bold green]{len(relevant)} signals above threshold ({threshold}/10)[/bold green]")
    print_summary_table(scored_signals, threshold)

    # 4. Generate markdown report
    if _output_available:
        output_config = config.get("output", {}).get("markdown", {})
        reporter = MarkdownReportGenerator(fund_name=thesis.fund_name)
        report = reporter.generate(
            scored_signals,
            threshold=threshold,
            max_items=output_config.get("max_items", 15),
        )

        if not output_path:
            output_dir = Path(output_config.get("output_dir", "output"))
            output_dir.mkdir(exist_ok=True)
            date_str = datetime.utcnow().strftime("%Y_%m_%d")
            output_path = str(output_dir / f"digest_{date_str}.md")

        reporter.save(report, output_path)
        console.print(f"\n[green]Report saved to {output_path}[/green]")

        # 5. Email digest (optional)
        if send_email:
            missing = [v for v in ("EMAIL_SMTP_HOST", "EMAIL_USER", "EMAIL_PASSWORD", "EMAIL_FROM", "EMAIL_TO") if not os.getenv(v)]
            if missing:
                console.print(f"[red]Email not configured. Missing .env vars: {', '.join(missing)}[/red]")
            else:
                to_addrs = [a.strip() for a in os.getenv("EMAIL_TO", "").split(",") if a.strip()]
                email_config = config.get("output", {}).get("email", {})
                notifier = EmailNotifier(
                    smtp_host=os.getenv("EMAIL_SMTP_HOST"),
                    smtp_port=int(os.getenv("EMAIL_SMTP_PORT", "587")),
                    user=os.getenv("EMAIL_USER"),
                    password=os.getenv("EMAIL_PASSWORD"),
                    from_addr=os.getenv("EMAIL_FROM"),
                    to_addrs=to_addrs,
                    fund_name=thesis.fund_name,
                )
                notifier.send_digest(
                    scored_signals,
                    threshold=threshold,
                    max_items=email_config.get("max_items", 15),
                )
                console.print(f"[green]Email digest sent to {', '.join(to_addrs)}[/green]")
    else:
        console.print("[dim]Output module not available — skipping report generation.[/dim]")


if __name__ == "__main__":
    main()
