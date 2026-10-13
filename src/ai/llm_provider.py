"""LLM provider abstraction.

Supported providers
* openai    — OpenAI API through the official `openai` SDK. Setting
              LLM_BASE_URL makes it work with any OpenAI-compatible server
              (e.g. LM Studio, vLLM, llama.cpp server) — including local ones.
* anthropic — Anthropic Claude API through the official `anthropic` SDK.
* ollama    — a local Ollama server (default http://localhost:11434).
* none      — no model; the app runs in retrieval-only mode.

The application never falls back from one provider to another. Failures are
raised as LLMProviderError with an actionable hint and shown to the user.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlparse

from config.settings import LLM_PROVIDERS, Settings
from src.utils.errors import LLMNotConfiguredError, LLMProviderError
from src.utils.logging_config import get_logger, redact

log = get_logger("llm")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model

    @property
    @abstractmethod
    def is_external(self) -> bool:
        """True if prompts (including document excerpts) leave this machine."""

    @property
    def destination(self) -> str:
        return LLM_PROVIDERS.get(self.name, self.name)

    @abstractmethod
    def _complete(self, system: str, user: str, max_tokens: int, temperature: float) -> str: ...

    def generate(self, system: str, user: str, max_tokens: int | None = None,
                 temperature: float | None = None) -> LLMResult:
        start = time.perf_counter()
        try:
            text = self._complete(system, user, max_tokens or self.settings.llm_max_tokens,
                                  self.settings.llm_temperature if temperature is None else temperature)
        except LLMProviderError:
            raise
        except Exception as exc:  # map SDK-specific errors to a uniform, safe message
            raise self._map_error(exc) from exc
        latency = int((time.perf_counter() - start) * 1000)
        if not text or not text.strip():
            raise LLMProviderError("The model returned an empty response.", "Try again or choose a different model.")
        return LLMResult(text=text.strip(), provider=self.name, model=self.model, latency_ms=latency)

    def _map_error(self, exc: Exception) -> LLMProviderError:
        name = type(exc).__name__
        message = redact(str(exc))[:300]
        lowered = (name + " " + message).lower()
        if "authentication" in lowered or "401" in lowered or "permission" in lowered or "api key" in lowered:
            return LLMProviderError(f"The {self.name} provider rejected the credentials.",
                                    "Check the API key in your .env file and restart the app.")
        if "ratelimit" in lowered or "rate limit" in lowered or "429" in lowered:
            return LLMProviderError(f"The {self.name} provider is rate-limiting requests.",
                                    "Wait a minute and try again, or reduce RETRIEVAL_TOP_K.")
        if "timeout" in lowered:
            return LLMProviderError(f"The {self.name} provider timed out after {self.settings.llm_timeout_seconds}s.",
                                    "Increase LLM_TIMEOUT_SECONDS or try a smaller model.")
        if "notfound" in lowered or "404" in lowered or "model" in lowered and "not" in lowered:
            return LLMProviderError(f"The model '{self.model}' was not found by the {self.name} provider.",
                                    "Check LLM_MODEL (for Ollama, run `ollama pull <model>` first).")
        if "connect" in lowered or "network" in lowered:
            return LLMProviderError(f"Could not connect to the {self.name} provider.",
                                    "Check your network connection or that the local server is running.")
        return LLMProviderError(f"The {self.name} provider returned an error ({name}).", message)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.base_url = settings.llm_base_url or None
        api_key = settings.api_key_for("openai")
        if not api_key and self.base_url and self.is_external:
            raise LLMNotConfiguredError("OPENAI_API_KEY is not set.", "Add it to your .env file and restart.")
        if not api_key and not self.base_url:
            raise LLMNotConfiguredError("OPENAI_API_KEY is not set.", "Add it to your .env file and restart.")
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key or "not-needed-for-local-server", base_url=self.base_url,
                             timeout=settings.llm_timeout_seconds, max_retries=2)

    @property
    def is_external(self) -> bool:
        if not self.base_url:
            return True
        return (urlparse(self.base_url).hostname or "") not in LOCAL_HOSTS

    @property
    def destination(self) -> str:
        return urlparse(self.base_url).netloc if self.base_url else "api.openai.com"

    def _complete(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        api_key = settings.api_key_for("anthropic")
        if not api_key:
            raise LLMNotConfiguredError("ANTHROPIC_API_KEY is not set.", "Add it to your .env file and restart.")
        from anthropic import Anthropic

        kwargs = {"api_key": api_key, "timeout": settings.llm_timeout_seconds, "max_retries": 2}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = Anthropic(**kwargs)

    @property
    def is_external(self) -> bool:
        return True

    @property
    def destination(self) -> str:
        return urlparse(self.settings.llm_base_url).netloc if self.settings.llm_base_url else "api.anthropic.com"

    def _complete(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        response = self.client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return "".join(getattr(block, "text", "") for block in response.content)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.host = (settings.llm_base_url or settings.ollama_host).rstrip("/")

    @property
    def is_external(self) -> bool:
        return (urlparse(self.host).hostname or "") not in LOCAL_HOSTS

    @property
    def destination(self) -> str:
        return urlparse(self.host).netloc

    def _complete(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        import httpx

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        response = httpx.post(f"{self.host}/api/chat", json=payload, timeout=self.settings.llm_timeout_seconds)
        if response.status_code == 404:
            raise LLMProviderError(f"Ollama does not have the model '{self.model}'.",
                                   f"Run `ollama pull {self.model}` and try again.")
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")

    def health(self) -> dict:
        import httpx

        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=3)
            r.raise_for_status()
            models = [m.get("name") for m in r.json().get("models", [])]
            return {"reachable": True, "models": models}
        except Exception as exc:
            return {"reachable": False, "error": type(exc).__name__}


_PROVIDERS = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "ollama": OllamaProvider}


def get_llm_provider(settings: Settings) -> LLMProvider:
    """Return the configured provider or raise LLMNotConfiguredError with setup guidance."""
    if settings.llm_provider in ("", "none"):
        raise LLMNotConfiguredError(
            "No language model is configured.",
            "Set LLM_PROVIDER and LLM_MODEL in .env (see README → 'Configure the LLM'). "
            "Retrieval, extraction, comparison and checks still work without one.",
        )
    if settings.llm_provider not in _PROVIDERS:
        raise LLMNotConfiguredError(f"Unknown LLM provider '{settings.llm_provider}'.")
    if not settings.llm_model:
        raise LLMNotConfiguredError("LLM_MODEL is not set.", "Choose a model name on the Settings page or in .env.")
    try:
        return _PROVIDERS[settings.llm_provider](settings)
    except ImportError as exc:
        raise LLMNotConfiguredError(f"The SDK for '{settings.llm_provider}' is not installed.",
                                    "Run `pip install -r requirements.txt`.") from exc


def llm_status(settings: Settings) -> dict:
    """Configuration summary safe for display (never includes a key)."""
    status = {"provider": settings.llm_provider, "model": settings.llm_model or "—", "configured": False,
              "external": None, "destination": "", "message": "", "key_env": settings.api_key_env_name(settings.llm_provider),
              "key_present": bool(settings.api_key_for(settings.llm_provider))}
    try:
        provider = get_llm_provider(settings)
        status.update(configured=True, external=provider.is_external, destination=provider.destination)
    except LLMNotConfiguredError as exc:
        status["message"] = exc.user_message()
    return status
