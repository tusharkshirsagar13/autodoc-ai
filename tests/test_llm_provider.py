"""LLM configuration handling — no network access and no API keys required."""

from types import SimpleNamespace

import httpx
import pytest

from src.ai.llm_provider import AnthropicProvider, OllamaProvider, OpenAIProvider, get_llm_provider, llm_status
from src.utils.errors import LLMNotConfiguredError, LLMProviderError
from src.utils.logging_config import redact


def test_missing_configuration_gives_guidance(settings):
    settings.llm_provider = "none"
    with pytest.raises(LLMNotConfiguredError) as exc:
        get_llm_provider(settings)
    assert "LLM_PROVIDER" in exc.value.user_message()
    status = llm_status(settings)
    assert status["configured"] is False and "retrieval" in status["message"].lower()


def test_missing_model_or_key(settings):
    settings.llm_provider = "openai"
    settings.llm_model = ""
    with pytest.raises(LLMNotConfiguredError, match="LLM_MODEL"):
        get_llm_provider(settings)
    settings.llm_model = "some-model"
    with pytest.raises(LLMNotConfiguredError, match="OPENAI_API_KEY"):
        get_llm_provider(settings)
    settings.llm_provider = "anthropic"
    with pytest.raises(LLMNotConfiguredError, match="ANTHROPIC_API_KEY"):
        get_llm_provider(settings)


def test_local_openai_compatible_server_needs_no_key(settings):
    settings.llm_provider, settings.llm_model, settings.llm_base_url = "openai", "local-model", "http://localhost:1234/v1"
    provider = get_llm_provider(settings)
    assert isinstance(provider, OpenAIProvider) and provider.is_external is False
    status = llm_status(settings)
    assert status["configured"] and status["external"] is False


def test_hosted_providers_are_flagged_external(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0000000000")
    settings.llm_provider, settings.llm_model = "anthropic", "test-model"
    provider = get_llm_provider(settings)
    assert isinstance(provider, AnthropicProvider) and provider.is_external
    status = llm_status(settings)
    assert status["key_present"] and "sk-ant" not in str(status)  # the key itself is never exposed


def test_openai_errors_are_mapped(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890abcdef")
    settings.llm_provider, settings.llm_model, settings.llm_base_url = "openai", "m", ""
    provider = get_llm_provider(settings)

    class RateLimitError(Exception):
        pass

    def boom(**kwargs):
        raise RateLimitError("429 Too Many Requests for key sk-test-1234567890abcdef")

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    with pytest.raises(LLMProviderError) as exc:
        provider.generate("sys", "user")
    assert "rate-limiting" in exc.value.message
    assert "sk-test" not in exc.value.user_message()


def test_openai_success_path(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890abcdef")
    settings.llm_provider, settings.llm_model = "openai", "m"
    provider = get_llm_provider(settings)
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer [S1]"))])

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider.generate("system prompt", "user prompt")
    assert result.text == "Answer [S1]" and result.provider == "openai"
    assert captured["messages"][0] == {"role": "system", "content": "system prompt"}


def test_ollama_unreachable_is_graceful(settings, monkeypatch):
    settings.llm_provider, settings.llm_model = "ollama", "llama3.1:8b"
    provider = get_llm_provider(settings)
    assert isinstance(provider, OllamaProvider) and not provider.is_external

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    with pytest.raises(LLMProviderError, match="connect"):
        provider.generate("s", "u")
    assert provider.health()["reachable"] is False


def test_ollama_success(settings, monkeypatch):
    settings.llm_provider, settings.llm_model = "ollama", "llama3.1:8b"
    provider = get_llm_provider(settings)

    def fake_post(url, json, timeout):
        assert url.endswith("/api/chat") and json["stream"] is False
        return httpx.Response(200, json={"message": {"content": "Local answer [S2]"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert provider.generate("s", "u").text == "Local answer [S2]"


def test_redaction(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret-value-123")
    assert "secret" not in redact("failed with key sk-live-secret-value-123")
    assert redact("api_key=abcdef123") == "api_key=[REDACTED]"


def test_settings_overrides_never_store_secrets(settings):
    settings.save_overrides({"llm_model": "x", "OPENAI_API_KEY": "sk-should-not-be-saved", "chunk_size": 50})
    text = settings.user_settings_path.read_text()
    assert "sk-should" not in text
    assert settings.chunk_size == 200  # clamped to the safe minimum
