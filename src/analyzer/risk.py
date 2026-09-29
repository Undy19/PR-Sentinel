"""LLM-based pull request risk scoring.

Sends a PR's title, description, and diff to an OpenAI chat model and
returns a structured :class:`RiskAssessment`.

Guarantees:
- HTTP 429 rate limits are retried with exponential backoff
  (2 s base, 30 s cap, at most 3 retries).
- Every API call is bounded by a 30 s timeout.
- Malformed model output degrades to a ``HIGH`` assessment instead of
  raising, so the notification pipeline never stalls on the LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal, cast

import openai
from openai.types.chat import ChatCompletionMessageParam

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o"
REQUEST_TIMEOUT = 30.0
BASE_BACKOFF = 2.0
MAX_BACKOFF = 30.0
MAX_RETRIES = 3
MAX_DIFF_CHARS = 50_000

SYSTEM_PROMPT = (
    "You are a senior code reviewer. Analyze the PR diff and assess risk. "
    'Respond as JSON: {"level": "LOW|MED|HIGH|CRITICAL", '
    '"reasons": ["reason1", "reason2"], "confidence": 0.0-1.0}'
)

LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "ru": "Respond in Russian. The 'reasons' array must contain Russian text.",
    "en": "Respond in English. The 'reasons' array must contain English text.",
}

RiskLevel = Literal["LOW", "MED", "HIGH", "CRITICAL"]
_VALID_LEVELS = frozenset({"LOW", "MED", "HIGH", "CRITICAL"})


@dataclass
class RiskAssessment:
    """Structured result of a PR risk analysis."""

    level: RiskLevel
    reasons: list[str]
    confidence: float


def _build_user_prompt(diff: str, pr_title: str, pr_body: str) -> str:
    """Assemble the user message from PR metadata and the diff."""
    shown_diff = diff
    if len(diff) > MAX_DIFF_CHARS:
        shown_diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"
    parts: list[str] = [f"PR title: {pr_title}"]
    if pr_body:
        parts.append(f"PR description:\n{pr_body}")
    parts.append(f"Diff:\n{shown_diff}")
    return "\n\n".join(parts)


def _parse_assessment(raw: str) -> RiskAssessment:
    """Parse the model's JSON reply into a :class:`RiskAssessment`.

    Any malformed shape (non-JSON text, non-object JSON, unknown level,
    bad confidence) yields a ``HIGH`` assessment with a parse-error
    reason instead of raising.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Tolerate a markdown code fence around the JSON payload.
        text = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(text)
    except ValueError:
        logger.warning("Malformed JSON in LLM response: %.200s", raw)
        return RiskAssessment(level="HIGH", reasons=["LLM response parse error"], confidence=0.0)

    if not isinstance(data, dict):
        logger.warning("LLM response is not a JSON object: %.200s", raw)
        return RiskAssessment(level="HIGH", reasons=["LLM response parse error"], confidence=0.0)

    level = data.get("level")
    if not isinstance(level, str) or level.strip().upper() not in _VALID_LEVELS:
        logger.warning("Invalid risk level in LLM response: %r", level)
        return RiskAssessment(level="HIGH", reasons=["LLM response parse error"], confidence=0.0)
    level = cast(RiskLevel, level.strip().upper())

    raw_reasons = data.get("reasons")
    if isinstance(raw_reasons, list):
        reasons = [str(r).strip() for r in raw_reasons if str(r).strip()][:2]
    else:
        reasons = []

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0

    return RiskAssessment(level=level, reasons=reasons, confidence=confidence)


async def analyze_pr(
    diff: str,
    pr_title: str,
    pr_body: str,
    *,
    language: str = "ru",
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    base_url: str | None = None,
    client: openai.AsyncOpenAI | None = None,
) -> RiskAssessment:
    """Analyze a pull request and return its risk assessment.
        diff: Unified diff of the PR.
        pr_title: PR title.
        pr_body: PR description/body.
        language: Language for the ``reasons`` text (``"ru"`` or ``"en"``);
            appended to the system prompt as an instruction to the model.
        model: OpenAI chat model to use (default ``"gpt-4o"``).
        api_key: OpenAI API key; falls back to ``OPENAI_API_KEY`` env var
            when ``None``.
        base_url: Custom API base URL (e.g. an OpenAI-compatible proxy);
            falls back to the OpenAI default when ``None``.
        client: Optional pre-configured ``openai.AsyncOpenAI`` client
            (dependency injection / testing); created when omitted.

    Returns:
        A :class:`RiskAssessment`. Transient API failures (429 rate
        limits, timeouts) are retried with exponential backoff; after
        the retries are exhausted a ``HIGH`` assessment is returned
        rather than raising, so callers can always notify.
    """
    if client is None:
        client = openai.AsyncOpenAI(timeout=REQUEST_TIMEOUT, api_key=api_key, base_url=base_url)

    prompt = _build_user_prompt(diff, pr_title, pr_body)
    system_prompt = (
        SYSTEM_PROMPT
        + " "
        + LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["en"])
    )
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    backoff = BASE_BACKOFF

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(model=model, messages=messages),
                timeout=REQUEST_TIMEOUT,
            )
            return _parse_assessment(response.choices[0].message.content or "")
        except openai.RateLimitError:
            if attempt >= MAX_RETRIES:
                logger.warning(
                    "OpenAI rate limit persisted after %d retries", MAX_RETRIES
                )
                return RiskAssessment(
                    level="HIGH", reasons=["LLM rate limit exceeded"], confidence=0.0
                )
            logger.warning(
                "OpenAI 429 rate limit; retry %d/%d in %.0fs",
                attempt + 1,
                MAX_RETRIES,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
        except (TimeoutError, openai.APITimeoutError) as exc:
            if attempt >= MAX_RETRIES:
                logger.warning("OpenAI request timed out after %d retries", MAX_RETRIES)
                return RiskAssessment(
                    level="HIGH", reasons=["LLM request timed out"], confidence=0.0
                )
            logger.warning(
                "OpenAI timeout (%s); retry %d/%d in %.0fs",
                exc.__class__.__name__,
                attempt + 1,
                MAX_RETRIES,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
        except openai.OpenAIError as exc:
            logger.warning("OpenAI API error (not retried): %s", exc)
            return RiskAssessment(
                level="HIGH", reasons=["LLM API error"], confidence=0.0
            )

    # Unreachable: every path above returns, but keeps static analysis happy.
    return RiskAssessment(level="HIGH", reasons=["LLM analysis failed"], confidence=0.0)
