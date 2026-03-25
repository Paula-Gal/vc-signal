"""Thesis-driven LLM scoring engine.

This is the core differentiator of vc-signal-scanner.
It takes raw signals and evaluates them against a fund's investment thesis
using Claude, returning structured relevance scores and reasoning.
"""

from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import asdict

import anthropic

from src.models import Signal, ScoredSignal, InvestmentThesis

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """You are an experienced venture capital analyst. Your job is to evaluate startup signals against a specific investment thesis and determine their relevance.

You will receive:
1. An investment thesis describing a VC fund's focus
2. A signal (a startup, product launch, or news item)

You must respond with a JSON object containing:
- "relevance_score": float from 0.0 to 10.0 (10 = perfect fit for the fund)
- "reasoning": string explaining your assessment in 2-3 sentences
- "thesis_alignment": list of strings describing which thesis criteria this matches
- "red_flags": list of strings noting potential concerns

Scoring guidelines:
- 8-10: Strong match — fits stage, geography, and sector. Clear traction signals.
- 6-7: Interesting — partial match, worth monitoring. May fit one or two criteria well.
- 4-5: Tangential — loosely related but not a clear fit.
- 0-3: Not relevant — wrong stage, geography, sector, or business model.

Be rigorous. Most signals should score below 5. A score of 8+ should be rare and well-justified.
European companies with technical moats and clear revenue traction should score higher.
Be especially attentive to DACH and CEE region founders — these are underrepresented and high-value for this fund.

Respond ONLY with valid JSON. No markdown, no explanation outside the JSON."""


class ThesisScorer:
    def __init__(
        self,
        thesis: InvestmentThesis,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_concurrent: int = 5,
    ):
        self.thesis = thesis
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.semaphore = asyncio.Semaphore(max_concurrent)  # rate limiting

    async def score_signals(self, signals: list[Signal]) -> list[ScoredSignal]:
        """Score a batch of signals against the thesis."""
        logger.info(f"Scoring {len(signals)} signals against {self.thesis.fund_name} thesis...")

        tasks = [self._score_single(signal) for signal in signals]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored = []
        for result in results:
            if isinstance(result, ScoredSignal):
                scored.append(result)
            elif isinstance(result, Exception):
                logger.debug(f"Scoring failed: {result}")

        # Sort by relevance score descending
        scored.sort(key=lambda s: s.relevance_score, reverse=True)
        logger.info(f"Scored {len(scored)} signals. Top score: {scored[0].relevance_score if scored else 'N/A'}")
        return scored

    async def _score_single(self, signal: Signal) -> ScoredSignal:
        """Score a single signal using the LLM."""
        async with self.semaphore:
            signal_text = self._format_signal(signal)
            thesis_text = self.thesis.to_prompt()

            user_prompt = f"""{thesis_text}

---

Signal to evaluate:

Title: {signal.title}
Source: {signal.source.value}
Description: {signal.description}
URL: {signal.url}
Score/Traction: {signal.score or 'N/A'}
Tags: {', '.join(signal.tags) if signal.tags else 'None'}
Additional info: {json.dumps(signal.extra) if signal.extra else 'None'}

Evaluate this signal against the investment thesis above. Respond with JSON only."""

            try:
                # Using sync client in async context via executor
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.client.messages.create(
                        model=self.model,
                        max_tokens=500,
                        system=SCORING_SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": user_prompt}],
                    ),
                )

                response_text = response.content[0].text.strip()

                # Parse JSON response
                # Handle potential markdown code blocks
                if response_text.startswith("```"):
                    response_text = response_text.split("\n", 1)[1]
                    response_text = response_text.rsplit("```", 1)[0]

                result = json.loads(response_text)

                return ScoredSignal(
                    signal=signal,
                    relevance_score=float(result.get("relevance_score", 0)),
                    reasoning=result.get("reasoning", ""),
                    thesis_alignment=result.get("thesis_alignment", []),
                    red_flags=result.get("red_flags", []),
                )

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse LLM response for '{signal.title}': {e}")
                return ScoredSignal(
                    signal=signal,
                    relevance_score=0.0,
                    reasoning="[Scoring error: could not parse LLM response]",
                    thesis_alignment=[],
                    red_flags=["scoring_error"],
                )
            except anthropic.APIError as e:
                logger.warning(f"Anthropic API error for '{signal.title}': {e}")
                raise

    def _format_signal(self, signal: Signal) -> str:
        """Format a signal for human-readable display."""
        parts = [f"**{signal.title}**"]
        if signal.description:
            parts.append(signal.description)
        if signal.score:
            parts.append(f"Traction: {signal.score}")
        if signal.tags:
            parts.append(f"Tags: {', '.join(signal.tags)}")
        return "\n".join(parts)

    def filter_relevant(
        self, scored_signals: list[ScoredSignal], threshold: float = 6.0
    ) -> list[ScoredSignal]:
        """Filter scored signals to only include those above the relevance threshold."""
        return [s for s in scored_signals if s.relevance_score >= threshold]
