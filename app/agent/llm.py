"""Minimal chat client for any OpenAI-compatible endpoint (standard library only, no SDK).

The same class works with a local Ollama model, Groq, Google Gemini, Mistral or OpenAI:
only LLM_API_URL, LLM_API_KEY and LLM_MODEL in .env change. The agent depends on the small
``LLM`` protocol, so tests can swap in a fake model.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Protocol

from app.config import get_settings
from app.errors import ServiceNotReadyError

log = logging.getLogger(__name__)

RETRIES = 6  # free tiers limit tokens per minute: on HTTP 429, wait as long as the provider asks
MAX_WAIT_S = 60.0


class LLM(Protocol):
    """Anything that turns a system prompt and a user message into text."""

    def complete(self, system: str, user: str) -> str:
        """Return the model's answer."""


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before retrying: the provider's Retry-After header, else a growing delay."""
    try:
        wait = float(exc.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        wait = 5.0 * (attempt + 1)
    return min(wait + 0.5, MAX_WAIT_S)


class ChatLLM:
    """Chat completions at temperature 0, so the same question gives the same query."""

    def __init__(self, url: str, model: str, api_key: str = "", timeout_s: float = 120.0) -> None:
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s  # a local model on a laptop CPU can take a while

    def complete(self, system: str, user: str) -> str:
        """One chat completion; retries on rate limits, raises ServiceNotReadyError otherwise."""
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "wine-graph-agent/1.0",  # some providers' firewalls block the default Python-urllib
        }
        if self.api_key:  # a local Ollama server needs no key
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # nosec - URL from settings
                    body = json.load(response)
                return body["choices"][0]["message"]["content"].strip()
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < RETRIES - 1:
                    time.sleep(_retry_after(exc, attempt))
                    continue
                log.error(f"LLM HTTP {exc.code}: {exc.read()[:200]!r}")
                raise ServiceNotReadyError(f"LLM provider error (HTTP {exc.code})") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ServiceNotReadyError("LLM provider unreachable") from exc
        raise ServiceNotReadyError("LLM provider rate limit")


def make_llm() -> LLM:
    """The configured model; 503 if none is configured, so the rest of the API keeps working."""
    settings = get_settings()
    if not settings.llm_api_url or not settings.llm_model:
        raise ServiceNotReadyError("NL->Cypher needs LLM_API_URL and LLM_MODEL in .env")
    return ChatLLM(settings.llm_api_url, settings.llm_model, settings.llm_api_key)
