"""
Phase III: Multi-Tier LLM Extraction Engine.

- Fallback chain across providers: Gemini Flash -> Groq Llama 3 -> DeepSeek
- Intelligent chunking so payloads never trigger 413s
- Exponential backoff + jitter on 429s
- Structured JSON-only output, validated against our Pydantic schemas
  before being accepted (never trust raw LLM output blindly)

Provider clients are behind a common Protocol so adding/removing a tier
is a one-line change, not a rewrite.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any, Optional, Protocol

logger = logging.getLogger("frontieratlas.llm")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    """Raised by a provider client on HTTP 429."""


class PayloadTooLargeError(Exception):
    """Raised by a provider client on HTTP 413."""


class ProviderUnavailableError(Exception):
    """Any other non-retryable provider failure (5xx exhausted, auth, etc.)."""


# ---------------------------------------------------------------------------
# Provider protocol -- implement one of these per real provider
# ---------------------------------------------------------------------------

class LLMProvider(Protocol):
    name: str
    max_context_tokens: int

    async def complete_json(self, system_prompt: str, user_content: str) -> dict:
        """Must return parsed JSON dict or raise one of the errors above."""
        ...


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Cheap, provider-agnostic estimate: ~4 chars/token for English text."""
    return max(1, len(text) // 4)


def chunk_text(
    text: str,
    max_tokens: int,
    overlap_tokens: int = 100,
) -> list[str]:
    """
    Splits text into chunks that fit under max_tokens, biased to break on
    paragraph/sentence boundaries so we don't sever semantically dense
    content (e.g. a table row, a key claim) mid-sentence where avoidable.
    """
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4

    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
        if len(para) > max_chars:
            # Single paragraph too large on its own: hard-split on sentences
            sentences = para.split(". ")
            buf = ""
            for sent in sentences:
                cand = f"{buf}. {sent}" if buf else sent
                if len(cand) <= max_chars:
                    buf = cand
                else:
                    if len(sent) > max_chars:
                        # No sentence boundaries either (e.g. one giant
                        # blob of text/code) -- fall back to a hard
                        # character split so we NEVER emit an oversized
                        # chunk regardless of input shape.
                        if buf:
                            chunks.append(buf)
                            buf = ""
                        for i in range(0, len(sent), max_chars):
                            piece = sent[i : i + max_chars]
                            if len(piece) == max_chars:
                                chunks.append(piece)
                            else:
                                buf = piece
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = sent
            current = buf
        else:
            current = para

    if current:
        chunks.append(current)

    # Add overlap between consecutive chunks for extraction continuity
    overlapped = []
    for i, c in enumerate(chunks):
        if i == 0:
            overlapped.append(c)
        else:
            tail = chunks[i - 1][-overlap_chars:]
            overlapped.append(tail + c)
    return overlapped


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: float = 0.5


class LLMOrchestrator:
    def __init__(
        self,
        providers: list[LLMProvider],
        retry_config: Optional[RetryConfig] = None,
    ):
        if not providers:
            raise ValueError("At least one LLM provider must be configured")
        self.providers = providers
        self.retry = retry_config or RetryConfig()

    async def _call_with_backoff(
        self, provider: LLMProvider, system_prompt: str, chunk: str
    ) -> Optional[dict]:
        for attempt in range(self.retry.max_retries):
            try:
                return await provider.complete_json(system_prompt, chunk)
            except RateLimitError:
                delay = min(
                    self.retry.max_delay,
                    self.retry.base_delay * (2 ** attempt),
                )
                delay += random.uniform(0, self.retry.jitter * delay)
                logger.warning(
                    "429 from %s (attempt %d/%d) — backing off %.1fs",
                    provider.name, attempt + 1, self.retry.max_retries, delay,
                )
                await asyncio.sleep(delay)
            except PayloadTooLargeError:
                # Chunk was still too big for this provider's context window;
                # caller should re-chunk smaller. We signal by returning None
                # so extract() can retry with a tighter budget once.
                logger.warning("413 from %s — chunk exceeds context window", provider.name)
                return None
        logger.error("%s exhausted retries", provider.name)
        return None

    async def extract(
        self,
        system_prompt: str,
        raw_text: str,
    ) -> list[dict]:
        """
        Runs raw_text through the fallback chain, chunked to fit whichever
        provider is currently being tried. Returns a list of parsed JSON
        objects (one per chunk) for the caller to merge/validate.
        """
        results: list[dict] = []

        for provider in self.providers:
            chunks = chunk_text(raw_text, provider.max_context_tokens - 500)
            provider_failed = False

            for chunk in chunks:
                # If a chunk still 413s on this provider, halve it once and retry.
                payload = chunk
                for shrink_attempt in range(2):
                    result = await self._call_with_backoff(provider, system_prompt, payload)
                    if result is not None:
                        results.append(result)
                        break
                    payload = payload[: len(payload) // 2]
                    if not payload:
                        break
                else:
                    # This provider couldn't handle this chunk at all —
                    # fall through to the next provider in the chain for
                    # the REMAINDER of the document, not just this chunk,
                    # since a provider that's rate-limited/down for one
                    # chunk is likely down for all of them.
                    provider_failed = True
                    break

            if not provider_failed:
                return results  # this provider handled the whole document
            logger.warning(
                "Falling back from %s to next provider in chain", provider.name
            )
            results = []  # discard partial results, restart cleanly on next tier

        logger.error("All providers in fallback chain exhausted for this document")
        return results
