"""
Concrete LLMProvider implementations for the fallback chain:
Gemini Flash -> OpenAI (ChatGPT / GPT-4o-mini).

Each wraps its provider's HTTP API, maps 429 -> RateLimitError and
413/context-length errors -> PayloadTooLargeError, and forces JSON-only
output so the orchestrator can parse it directly.

Set API keys via environment variables:
  GEMINI_API_KEY, OPENAI_API_KEY
"""
from __future__ import annotations

import json
import os
import re

import aiohttp

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .orchestrator import PayloadTooLargeError, ProviderUnavailableError, RateLimitError


def _extract_json(text: str) -> dict:
    """Robustly extracts JSON from raw LLM output, handling markdown fences and pre/post commentary."""
    text = text.strip()
    # 1. Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extract from markdown code fences if present
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Extract between first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    return json.loads(text)


class GeminiFlashProvider:
    name = "gemini-flash"
    max_context_tokens = 900_000  # Gemini 1.5/2.x Flash: ~1M token window

    def __init__(self, api_key: str | None = None, model: str = "gemini-3.6-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self.url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

    async def complete_json(self, system_prompt: str, user_content: str) -> dict:
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt + "\nRespond with JSON only."}]},
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, json=payload) as resp:
                if resp.status == 429:
                    raise RateLimitError("Gemini rate limited")
                if resp.status == 413:
                    raise PayloadTooLargeError("Gemini payload too large")
                if resp.status >= 400:
                    raise ProviderUnavailableError(f"Gemini error {resp.status}: {await resp.text()}")
                data = await resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return _extract_json(text)


class OpenAIProvider:
    """ChatGPT / OpenAI (GPT-4o-mini / GPT-4o) Provider."""
    name = "openai-chatgpt"
    max_context_tokens = 128_000  # GPT-4o / GPT-4o-mini: 128k context

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.url = "https://api.openai.com/v1/chat/completions"

    async def complete_json(self, system_prompt: str, user_content: str) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt + "\nRespond with valid JSON only."},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, json=payload, headers=headers) as resp:
                if resp.status == 429:
                    raise RateLimitError("OpenAI rate limited")
                if resp.status == 413:
                    raise PayloadTooLargeError("OpenAI payload too large")
                if resp.status >= 400:
                    body = await resp.text()
                    if "context_length_exceeded" in body or "maximum context length" in body:
                        raise PayloadTooLargeError("OpenAI context length exceeded")
                    raise ProviderUnavailableError(f"OpenAI error {resp.status}: {body}")
                data = await resp.json()
                text = data["choices"][0]["message"]["content"]
                return _extract_json(text)


def build_default_chain() -> list:
    """Primary: Gemini Flash -> Fallback: ChatGPT (GPT-4o-mini)."""
    return [GeminiFlashProvider(), OpenAIProvider()]
