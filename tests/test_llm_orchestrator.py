"""
Unit tests for Phase III LLM Orchestrator fallback chain, rate limit backoff, and providers.
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llm.orchestrator import (  # noqa: E402
    LLMOrchestrator,
    PayloadTooLargeError,
    ProviderUnavailableError,
    RateLimitError,
    RetryConfig,
)
from src.llm.providers import GeminiFlashProvider, OpenAIProvider, build_default_chain  # noqa: E402


class MockFailingProvider:
    def __init__(self, name: str, fail_error: Exception, max_context_tokens: int = 1000):
        self.name = name
        self.fail_error = fail_error
        self.max_context_tokens = max_context_tokens
        self.calls = 0

    async def complete_json(self, system_prompt: str, user_content: str) -> dict:
        self.calls += 1
        raise self.fail_error


class MockSuccessProvider:
    def __init__(self, name: str, return_data: dict, max_context_tokens: int = 1000):
        self.name = name
        self.return_data = return_data
        self.max_context_tokens = max_context_tokens
        self.calls = 0

    async def complete_json(self, system_prompt: str, user_content: str) -> dict:
        self.calls += 1
        return self.return_data


@pytest.mark.asyncio
async def test_fallback_chain_on_provider_failure():
    failing_tier1 = MockFailingProvider("gemini-flash", PayloadTooLargeError("413 Payload Too Large"))
    failing_tier2 = MockFailingProvider("openai-chatgpt", RateLimitError("429 Rate Limit"))
    success_tier3 = MockSuccessProvider("tier3", {"extracted": "entity_data"})

    orchestrator = LLMOrchestrator(
        providers=[failing_tier1, failing_tier2, success_tier3],
        retry_config=RetryConfig(max_retries=1, base_delay=0.01, max_delay=0.02, jitter=0.0),
    )

    results = await orchestrator.extract(
        system_prompt="Extract schema",
        raw_text="Sample raw input text for extraction",
    )

    assert len(results) == 1
    assert results[0] == {"extracted": "entity_data"}
    assert failing_tier1.calls > 0
    assert failing_tier2.calls > 0
    assert success_tier3.calls == 1


@pytest.mark.asyncio
async def test_all_providers_failing_raises_error():
    failing_tier1 = MockFailingProvider("tier1", ProviderUnavailableError("500 Server Error"))
    failing_tier2 = MockFailingProvider("tier2", ProviderUnavailableError("503 Service Unavailable"))

    orchestrator = LLMOrchestrator(
        providers=[failing_tier1, failing_tier2],
        retry_config=RetryConfig(max_retries=1, base_delay=0.01, max_delay=0.02, jitter=0.0),
    )

    with pytest.raises(ProviderUnavailableError):
        await orchestrator.extract(
            system_prompt="Extract schema",
            raw_text="Sample text",
        )


def test_build_default_chain():
    chain = build_default_chain()
    assert len(chain) == 2
    assert isinstance(chain[0], GeminiFlashProvider)
    assert isinstance(chain[1], OpenAIProvider)
    assert chain[0].model == "gemini-3.6-flash"
    assert chain[1].model == "gpt-4o-mini"
