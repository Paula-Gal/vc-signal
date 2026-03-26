"""Data models for vc-signal-scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SourceType(Enum):
    HACKER_NEWS = "hackernews"
    PRODUCT_HUNT = "producthunt"
    GITHUB = "github"
    RSS = "rss"
    REDDIT = "reddit"
    LAUNCHES = "launches"


@dataclass
class Signal:
    """A raw signal from a data source — a potential startup or product worth evaluating."""

    title: str
    description: str
    source: SourceType
    url: str
    discovered_at: datetime = field(default_factory=datetime.utcnow)

    # Optional metadata depending on source
    score: int | None = None  # HN points, PH votes, GitHub stars
    author: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __str__(self) -> str:
        source_label = self.source.value
        score_str = f" ({self.score} pts)" if self.score else ""
        return f"[{source_label}] {self.title}{score_str}"


@dataclass
class ScoredSignal:
    """A signal that has been evaluated against an investment thesis."""

    signal: Signal
    relevance_score: float  # 0.0 to 10.0
    reasoning: str
    thesis_alignment: list[str]
    red_flags: list[str]

    # Extracted startup details (best-effort from signal data)
    location: str = ""
    website: str = ""
    founders: list[str] = field(default_factory=list)
    previous_rounds: str = ""
    stage: str = ""
    risk: str = ""

    @property
    def is_relevant(self) -> bool:
        return self.relevance_score >= 6.0

    def __str__(self) -> str:
        emoji = "\U0001f7e2" if self.relevance_score >= 7 else "\U0001f7e1" if self.relevance_score >= 5 else "\U0001f534"
        return f"{emoji} {self.relevance_score:.1f}/10 — {self.signal.title}"


@dataclass
class InvestmentThesis:
    """A fund's investment thesis — used by the LLM to score signals."""

    fund_name: str
    stage: str
    geography: str
    sectors: list[str]
    positive_signals: list[str]
    negative_signals: list[str]
    additional_context: str = ""

    def to_prompt(self) -> str:
        """Convert the thesis into a prompt fragment for the LLM."""
        sectors_str = "\n".join(f"  - {s}" for s in self.sectors)
        pos_str = "\n".join(f"  - {s}" for s in self.positive_signals)
        neg_str = "\n".join(f"  - {s}" for s in self.negative_signals)

        return f"""Investment Thesis for {self.fund_name}:

Stage: {self.stage}
Geography: {self.geography}

Target Sectors:
{sectors_str}

Positive Signals (increase relevance):
{pos_str}

Negative Signals (decrease relevance):
{neg_str}

{f"Additional Context: {self.additional_context}" if self.additional_context else ""}"""

    @classmethod
    def from_yaml(cls, data: dict) -> "InvestmentThesis":
        """Load thesis from a parsed YAML dict."""
        thesis = data.get("thesis", {})
        return cls(
            fund_name=data.get("fund_name", "Unknown Fund"),
            stage=thesis.get("stage", ""),
            geography=thesis.get("geography", ""),
            sectors=thesis.get("sectors", []),
            positive_signals=thesis.get("signals", {}).get("positive", []),
            negative_signals=thesis.get("signals", {}).get("negative", []),
            additional_context=thesis.get("additional_context", ""),
        )
