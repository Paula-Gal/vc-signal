"""LLM-based scoring engine — evaluates signals against an investment thesis."""

from __future__ import annotations

import json
import logging
import asyncio

import anthropic

from src.models import Signal, ScoredSignal, InvestmentThesis

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """You are an experienced venture capital analyst. Your job is to evaluate startup signals against a specific investment thesis and extract key company details.

You will receive:
1. An investment thesis describing a VC fund's focus
2. A signal (a startup, product launch, or news item)

You must respond with a JSON object containing:
- "relevance_score": float from 0.0 to 10.0 (10 = perfect fit for the fund)
- "reasoning": string explaining your score in 2-3 sentences — cover stage fit, geography fit, and why the traction is or isn't compelling
- "thesis_alignment": list of strings, each naming a specific thesis criterion this signal meets
- "red_flags": list of strings noting concrete concerns (e.g. "no revenue mentioned", "B2C model")
- "risk": string, one sentence summarising the single biggest investment risk
- "location": string, city and country if inferable from the signal, else empty string
- "website": string, company website URL if mentioned or inferable, else empty string
- "founders": list of strings, founder or key team member names if mentioned, else empty list
- "previous_rounds": string, any prior funding mentioned (e.g. "Seed €1.2M, 2023"), else empty string
- "stage": string, current funding stage (e.g. "Pre-Seed", "Seed", "Series A", "Series B", "Growth"), else empty string

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
        model: str = "claude-sonnet-4-6",
        requests_per_minute: int = 4,
    ):
        self.thesis = thesis
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        # Serialize requests and enforce minimum spacing to stay under RPM limit
        self.semaphore = asyncio.Semaphore(1)
        self._min_interval = 60.0 / requests_per_minute  # seconds between calls
        self._last_call: float = 0.0

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

        scored.sort(key=lambda s: s.relevance_score, reverse=True)
        logger.info(f"Scored {len(scored)} signals. Top score: {scored[0].relevance_score if scored else 'N/A'}")
        return scored

    async def _score_single(self, signal: Signal) -> ScoredSignal:
        """Score a single signal using the LLM, with rate limiting and retry."""
        async with self.semaphore:
            # Enforce minimum interval between API calls
            now = asyncio.get_event_loop().time()
            gap = now - self._last_call
            if gap < self._min_interval:
                await asyncio.sleep(self._min_interval - gap)
            self._last_call = asyncio.get_event_loop().time()

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

            for attempt in range(3):
                try:
                    response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.client.messages.create(
                            model=self.model,
                            max_tokens=800,
                            system=SCORING_SYSTEM_PROMPT,
                            messages=[{"role": "user", "content": user_prompt}],
                        ),
                    )

                    response_text = response.content[0].text.strip()

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
                        risk=result.get("risk", ""),
                        location=result.get("location", ""),
                        website=result.get("website", ""),
                        founders=result.get("founders", []),
                        previous_rounds=result.get("previous_rounds", ""),
                        stage=result.get("stage", ""),
                    )

                except anthropic.RateLimitError:
                    wait = 15 * (2 ** attempt)
                    logger.warning(f"Unexpected rate limit for '{signal.title}', waiting {wait}s (attempt {attempt+1}/3)")
                    if attempt == 2:
                        raise
                    await asyncio.sleep(wait)
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
        """Filter to signals above the relevance threshold."""
        return [s for s in scored_signals if s.relevance_score >= threshold]
