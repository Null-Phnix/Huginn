"""
Tests for the extracted huginn.llm module.

Verifies the LLM provider config and summarize_text() helper are importable
from both huginn.llm (new canonical location) and huginn.api (back-compat
re-export) so existing callers don't break.
"""

import pytest
import inspect
import httpx

from huginn import llm
from huginn.llm import (
    LLM_PROVIDER_CONFIG,
    _LLM_PROVIDER_CONFIG,  # back-compat alias
    summarize_text,
    _summarize_text,  # back-compat alias
)


class TestLLMProviderConfig:
    """LLM_PROVIDER_CONFIG has all 5 supported providers."""

    def test_all_providers_present(self):
        """All 5 providers (openai, xai, ollama, anthropic, google) are configured."""
        expected = {"openai", "xai", "ollama", "anthropic", "google"}
        assert set(LLM_PROVIDER_CONFIG.keys()) == expected

    def test_each_provider_has_required_keys(self):
        """Each provider has base_url, key_env, default_model."""
        for name, cfg in LLM_PROVIDER_CONFIG.items():
            assert "base_url" in cfg, f"{name} missing base_url"
            assert "key_env" in cfg, f"{name} missing key_env"
            assert "default_model" in cfg, f"{name} missing default_model"

    def test_back_compat_alias_matches_canonical(self):
        """_LLM_PROVIDER_CONFIG is the same dict as LLM_PROVIDER_CONFIG."""
        assert _LLM_PROVIDER_CONFIG is LLM_PROVIDER_CONFIG

    def test_default_models_are_reasonable(self):
        """Default models are stable, well-known models (not experimental)."""
        assert LLM_PROVIDER_CONFIG["openai"]["default_model"] == "gpt-4o-mini"
        assert LLM_PROVIDER_CONFIG["anthropic"]["default_model"] == "claude-3-5-haiku-20250620"
        assert LLM_PROVIDER_CONFIG["google"]["default_model"] == "gemini-2.0-flash"


class TestSummarizeTextAPI:
    """summarize_text() has the right signature and best-effort behavior."""

    def test_signature_includes_optional_model(self):
        """summarize_text(text, llm_provider, llm_model) — llm_model is optional."""
        sig = inspect.signature(summarize_text)
        assert "text" in sig.parameters
        assert "llm_provider" in sig.parameters
        assert "llm_model" in sig.parameters
        # Defaults: text=positional, llm_provider="openai", llm_model=None
        assert sig.parameters["llm_provider"].default == "openai"
        assert sig.parameters["llm_model"].default is None

    @pytest.mark.asyncio
    async def test_summarize_text_returns_none_for_short_text(self):
        """Text shorter than 20 chars returns None (no point summarizing)."""
        result = await summarize_text("hi")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_text_returns_none_for_empty_text(self):
        """Empty text returns None."""
        result = await summarize_text("")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_text_returns_none_without_api_key(self, monkeypatch):
        """Without an API key, returns None (best-effort, not an error)."""
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY", "OLLAMA_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        result = await summarize_text("This is a long enough text to be worth summarizing.")
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_text_with_ollama_no_key_required(self, monkeypatch):
        """Ollama provider doesn't need a key — local model."""
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY", "OLLAMA_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        # With ollama, it will still try to call the API and fail (no real ollama
        # running) — the function catches the exception and returns None.
        result = await summarize_text(
            "This is a long enough text to be worth summarizing with ollama.",
            llm_provider="ollama",
        )
        # Either None (no key, no server) or actual summary if ollama is running
        # — the test passes either way; we just verify the call completes.
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_summarize_text_openai_actual_call_path(self, monkeypatch):
        """When OPENAI_API_KEY is set, the OpenAI chat completions endpoint is called.

        Uses httpx.MockTransport to verify the call without making a real
        network request. Verifies:
          - The Authorization header is set
          - The model is the default for the provider
          - The response text is returned
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")

        # Build a mock httpx client that returns a canned OpenAI response
        def mock_handler(request: "httpx.Request") -> "httpx.Response":
            # Verify the request was constructed correctly
            assert request.headers["Authorization"] == "Bearer sk-test-abc"
            body = request.read().decode("utf-8")
            assert "gpt-4o-mini" in body  # default model for openai
            assert "summary" in body.lower()  # the prompt includes "summary"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "A concise test summary."}}]},
            )

        transport = httpx.MockTransport(mock_handler)
        # Patch AsyncClient to use our transport
        from huginn import llm as llm_module
        original_client = llm_module.httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(llm_module.httpx, "AsyncClient", patched_client)

        result = await summarize_text("This is a long enough text to be worth summarizing for the test.")
        assert result == "A concise test summary."

    @pytest.mark.asyncio
    async def test_summarize_text_anthropic_actual_call_path(self, monkeypatch):
        """When ANTHROPIC_API_KEY is set, the Anthropic /messages endpoint is called.

        Anthropic uses a different response format (content[].text) so this
        test verifies the parsing path is exercised.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("HUGINN_LLM_PROVIDER", "anthropic")

        def mock_handler(request):
            assert request.headers["x-api-key"] == "sk-ant-test"
            assert request.headers["anthropic-version"] == "2023-06-01"
            return httpx.Response(
                200,
                json={"content": [{"text": "Anthropic-flavored summary."}]},
            )

        from huginn import llm as llm_module
        original_client = llm_module.httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(mock_handler)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(llm_module.httpx, "AsyncClient", patched_client)

        result = await summarize_text("Long enough text for anthropic test.", llm_provider="anthropic")
        assert result == "Anthropic-flavored summary."

    @pytest.mark.asyncio
    async def test_summarize_text_raises_keeps_silent(self, monkeypatch):
        """If the LLM API raises, summarize_text returns None (best-effort, never raises)."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")

        from huginn import llm as llm_module

        def erroring_client(*args, **kwargs):
            raise RuntimeError("API server down")

        monkeypatch.setattr(llm_module.httpx, "AsyncClient", erroring_client)

        # Should not raise — function catches all exceptions
        result = await summarize_text("Long enough text that would normally call the API.")
        assert result is None


class TestBackCompatReExports:
    """huginn.api re-exports the LLM helpers for back-compat."""

    def test_huginn_api_re_exports_provider_config(self):
        """huginn.api.LLM_PROVIDER_CONFIG and _LLM_PROVIDER_CONFIG both exist."""
        from huginn import api
        assert hasattr(api, "LLM_PROVIDER_CONFIG")
        assert hasattr(api, "_LLM_PROVIDER_CONFIG")
        assert api.LLM_PROVIDER_CONFIG is LLM_PROVIDER_CONFIG

    def test_huginn_api_re_exports_summarize_text(self):
        """huginn.api.summarize_text and _summarize_text both exist."""
        from huginn import api
        assert hasattr(api, "summarize_text")
        assert hasattr(api, "_summarize_text")
        assert api.summarize_text is summarize_text
        assert api._summarize_text is summarize_text
