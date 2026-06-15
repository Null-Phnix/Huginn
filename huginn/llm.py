"""
LLM helpers — provider config + light-weight text operations.

Centralizes LLM provider config and helper functions so they're not
embedded in api.py. Used by:
  - huginn.api: _summarize_text for the /v1/scrape `summary` field
  - huginn.doctor: check_llm_credentials verifies the configured provider
  - huginn.extractor: the structured extraction pipeline

The provider config maps a provider name to:
  - base_url: API endpoint
  - key_env: environment variable holding the API key
  - default_model: model to use if user didn't specify one
"""

import os
from typing import Optional

import httpx


# ─── Provider config ─────────────────────────────────────────────────────────

LLM_PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "key_env": "XAI_API_KEY",
        "default_model": "grok-3-mini",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_BASE_URL", "https://ollama.com/api"),
        "key_env": "OLLAMA_API_KEY",
        "default_model": "llama3.3",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-haiku-20250620",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "key_env": "GOOGLE_API_KEY",
        "default_model": "gemini-2.0-flash",
    },
}


# Backwards-compatible alias — older callers (e.g. tests) may import this name.
_LLM_PROVIDER_CONFIG = LLM_PROVIDER_CONFIG


# ─── Summarization ───────────────────────────────────────────────────────────


async def summarize_text(
    text: str,
    llm_provider: str = "openai",
    llm_model: Optional[str] = None,
) -> Optional[str]:
    """Generate a 1-2 sentence summary of text using the configured LLM.

    Best-effort: returns None on failure (no API key, network error, etc.)
    Used by the /v1/scrape `summary` field when the request sets summary=True.

    Args:
        text: The content to summarize. Short texts (<20 chars) return None.
        llm_provider: One of the keys in LLM_PROVIDER_CONFIG.
        llm_model: Optional override for the default model.

    Returns:
        The summary text, or None on any failure.
    """
    if not text or len(text.strip()) < 20:
        return None

    config = LLM_PROVIDER_CONFIG.get(llm_provider, LLM_PROVIDER_CONFIG["openai"])
    api_key = os.environ.get(config["key_env"], "")
    model = llm_model or config["default_model"]

    if llm_provider not in ("ollama",) and not api_key:
        return None  # No key — skip silently (best-effort)

    prompt = (
        "Provide a very brief summary (1-2 sentences maximum) of the following page content.\n"
        "Be concise and informative.\n\nContent:\n" + text[:8000]
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if llm_provider == "anthropic":
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                body = {
                    "model": model,
                    "max_tokens": 128,
                    "messages": [{"role": "user", "content": prompt}],
                }
                resp = await client.post(
                    f"{config['base_url']}/messages", headers=headers, json=body
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("content", [{}])[0].get("text", "").strip()
            else:
                # OpenAI / xAI / Ollama — chat completions endpoint
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                }
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 128,
                    "temperature": 0.3,
                }
                resp = await client.post(
                    f"{config['base_url']}/chat/completions", headers=headers, json=body
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
    except Exception:
        pass
    return None


# Backwards-compatible alias — older callers may import this name.
_summarize_text = summarize_text
