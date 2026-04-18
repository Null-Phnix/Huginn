"""
Huginn Extractor — LLM-powered structured data extraction.

The /v1/extract endpoint engine. Uses LLM providers to extract
structured data from pages based on a JSON schema or prompt.

Huginn extension: Mental model-assisted extraction.
Instead of throwing LLM at raw text, we build beliefs about page
structure and use them to guide extraction retries.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from .browser import BrowserManager
from .models import ScrapeData
from .scraper import Scraper
from .models import OutputFormat

logger = logging.getLogger(__name__)


class ExtractionResult:
    """Result of a structured extraction attempt."""

    def __init__(self, data: Dict[str, Any], confidence: float, attempts: int):
        self.data = data
        self.confidence = confidence
        self.attempts = attempts


class Extractor:
    """Extract structured data from web pages using LLM + mental model."""

    def __init__(
        self,
        browser: BrowserManager,
        llm_provider: str = "openai",
        llm_model: Optional[str] = None,
        max_retries: int = 3,
        mental_model: bool = True,
    ):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.max_retries = max_retries
        self.mental_model = mental_model

    async def extract(
        self,
        urls: List[str],
        prompt: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured data from one or more URLs.

        For single URLs: returns extracted data.
        For multiple URLs: merges findings from all pages.
        """
        all_texts = []
        page_metadata = []

        # Scrape all URLs concurrently
        tasks = [self.scraper.scrape(url=url, formats=[OutputFormat.MARKDOWN]) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Failed to scrape {urls[i]}: {result}")
                continue
            if result.markdown:
                all_texts.append(result.markdown)
                page_metadata.append({
                    "url": urls[i],
                    "title": result.metadata.get("title", "") if result.metadata else "",
                    "length": len(result.markdown),
                })

        if not all_texts:
            return {"error": "No content could be extracted from any URL", "success": False}

        # Concatenate text with page markers
        combined_text = ""
        for i, (text, meta) in enumerate(zip(all_texts, page_metadata)):
            combined_text += f"\n--- Page {i+1}: {meta['title']} ({meta['url']}) ---\n"
            combined_text += text[:50_000]  # Truncate per page
            combined_text += "\n"

        # Truncate total to fit LLM context
        if len(combined_text) > 100_000:
            combined_text = combined_text[:100_000]

        # Build extraction prompt
        extraction_prompt = self._build_prompt(combined_text, prompt, schema, page_metadata)

        # Extract using LLM with retries + mental model
        result = await self._extract_with_llm(extraction_prompt, schema, system_prompt, combined_text)

        return {
            "data": result.data,
            "confidence": result.confidence,
            "attempts": result.attempts,
            "sources": page_metadata,
            "success": True,
        }

    async def _extract_with_llm(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        system_prompt: Optional[str],
        raw_text: str,
    ) -> ExtractionResult:
        """Call LLM for extraction with retry logic and mental model beliefs."""

        beliefs = {}
        best_result = None
        best_confidence = 0.0

        for attempt in range(1, self.max_retries + 1):
            try:
                # Build beliefs from previous attempts (mental model)
                belief_context = ""
                if self.mental_model and beliefs:
                    belief_context = f"\n\nPrevious extraction attempts revealed:\n"
                    for key, value in beliefs.items():
                        belief_context += f"- {key}: {value}\n"
                    belief_context += "\nUse these observations to improve the extraction.\n"

                full_prompt = prompt + belief_context

                result_data = await self._call_llm(full_prompt, schema, system_prompt)

                # Validate against schema if provided
                if schema:
                    validation = self._validate_schema(result_data, schema)
                    confidence = validation["confidence"]
                    result_data = validation["data"]
                else:
                    confidence = 0.8  # Default confidence without schema validation

                # Update beliefs based on result
                if self.mental_model:
                    self._update_beliefs(beliefs, result_data, attempt, confidence)

                if confidence > best_confidence:
                    best_result = result_data
                    best_confidence = confidence

                # Accept result if confidence is high enough
                if confidence >= 0.7:
                    return ExtractionResult(result_data, confidence, attempt)

            except Exception as e:
                logger.warning(f"Extraction attempt {attempt} failed: {e}")
                if self.mental_model:
                    beliefs[f"attempt_{attempt}_error"] = str(e)[:200]

        # Return best result even if below threshold
        if best_result:
            return ExtractionResult(best_result, best_confidence, self.max_retries)

        return ExtractionResult(
            {"error": "All extraction attempts failed"},
            0.0,
            self.max_retries
        )

    def _build_prompt(
        self,
        text: str,
        prompt: Optional[str],
        schema: Optional[Dict[str, Any]],
        page_metadata: List[Dict],
    ) -> str:
        """Build the LLM extraction prompt."""
        parts = ["Extract the requested information from the following web page content.\n"]

        if prompt:
            parts.append(f"Task: {prompt}\n")
        else:
            parts.append("Task: Extract all relevant structured information.\n")

        if schema:
            parts.append(f"\nExpected output schema:\n```json\n{json.dumps(schema, indent=2)}\n```\n")
            parts.append("Return your extraction as valid JSON matching this schema.\n")
        else:
            parts.append("Return your extraction as a JSON object.\n")

        parts.append(f"\nSource pages: {len(page_metadata)}")
        for meta in page_metadata[:5]:
            parts.append(f"\n- {meta['title']} ({meta['url']})")

        parts.append(f"\n\n--- Content ---\n{text}")

        return "".join(parts)

    async def _call_llm(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        system_prompt: Optional[str],
    ) -> Dict[str, Any]:
        """Call the configured LLM provider."""

        sys_msg = system_prompt or "You are a precise data extraction engine. Extract exactly what is asked, return valid JSON only."

        # Build messages
        messages = [{"role": "system", "content": sys_msg}]
        messages.append({"role": "user", "content": prompt})

        # Try OpenAI-compatible API first
        if self.llm_provider in ("openai", "xai", "ollama"):
            return await self._call_openai_compatible(messages, schema)
        elif self.llm_provider == "anthropic":
            return await self._call_anthropic(messages, schema)
        elif self.llm_provider == "google":
            return await self._call_google(messages, schema)
        else:
            # Default to OpenAI-compatible
            return await self._call_openai_compatible(messages, schema)

    async def _call_openai_compatible(
        self, messages: List[Dict], schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Call OpenAI-compatible API (OpenAI, xAI, Ollama)."""

        provider_config = {
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
                "base_url": "http://localhost:11434/v1",
                "key_env": "OLLAMA_API_KEY",
                "default_model": "llama3",
            },
        }

        config = provider_config.get(self.llm_provider, provider_config["openai"])
        api_key = os.environ.get(config["key_env"], "")
        if self.llm_provider not in ("ollama",) and not api_key:
            raise RuntimeError(
                f"LLM provider '{self.llm_provider}' requires {config['key_env']} environment variable. "
                f"Set it or use --llm-provider ollama for local models."
            )
        model = self.llm_model or config["default_model"]

        async with httpx.AsyncClient(timeout=60) as client:
            body = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
            }

            # JSON output mode varies by provider
            if schema:
                if self.llm_provider == "ollama":
                    body["format"] = "json"
                else:
                    body["response_format"] = {"type": "json_object"}

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            # Ollama doesn't need auth and uses different base URL
            if self.llm_provider == "ollama":
                headers.pop("Authorization", None)
                # Ollama also needs timeout set on the client
                client_timeout = httpx.Timeout(60, connect=10)
                resp = await client.post(
                    f"{config['base_url']}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=client_timeout,
                )
            else:
                resp = await client.post(
                    f"{config['base_url']}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=60,
                )
            resp.raise_for_status()
            data = resp.json()

            content = data["choices"][0]["message"]["content"]
            return self._parse_json_response(content)

    async def _call_anthropic(
        self, messages: List[Dict], schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Call Anthropic Claude API."""

        try:
            import anthropic
            client = anthropic.AsyncAnthropic()
            model = self.llm_model or "claude-3-5-sonnet-20241022"

            # Convert messages to Anthropic format
            system_msg = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    user_messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })

            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_msg,
                messages=user_messages,
            )

            content = response.content[0].text
            return self._parse_json_response(content)
        except ImportError:
            raise RuntimeError("anthropic package not installed. pip install anthropic")

    async def _call_google(
        self, messages: List[Dict], schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Call Google Gemini API."""

        model = self.llm_model or "gemini-2.0-flash"
        api_key = os.environ.get("GOOGLE_API_KEY", "")

        if not api_key:
            raise RuntimeError(
                f"LLM provider 'google' requires GOOGLE_API_KEY environment variable. "
                f"Set it or use --llm-provider ollama for local models."
            )

        async with httpx.AsyncClient(timeout=60) as client:
            # Convert to Gemini format
            system_instruction = None
            contents = []
            for msg in messages:
                if msg["role"] == "system":
                    system_instruction = {"parts": [{"text": msg["content"]}]}
                else:
                    role = "model" if msg["role"] == "assistant" else "user"
                    contents.append({"role": role, "parts": [{"text": msg["content"]}]})

            body = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json" if schema else "text/plain",
                },
            }
            if system_instruction:
                body["systemInstruction"] = system_instruction

            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return self._parse_json_response(content)

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""
        # Strip markdown code blocks
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Brute-force find the outermost JSON object via bracket matching
            start = content.find("{")
            if start != -1:
                depth = 0
                for i in range(start, len(content)):
                    if content[i] == "{":
                        depth += 1
                    elif content[i] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(content[start:i + 1])
                            except json.JSONDecodeError:
                                break  # outermost object failed, give up

            return {"raw_response": content, "parse_error": True}

    def _validate_schema(self, data: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        """Validate extracted data against schema. Returns confidence score."""
        if not isinstance(data, dict):
            return {"data": data, "confidence": 0.3}

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        filled_required = sum(1 for r in required if r in data and data[r] is not None)
        filled_optional = sum(1 for k in properties if k in data and data[k] is not None and k not in required)

        total_fields = len(properties)
        required_ratio = filled_required / len(required) if required else 1.0
        optional_ratio = filled_optional / max(total_fields - len(required), 1)

        # Confidence based on how many fields were filled
        confidence = required_ratio * 0.8 + optional_ratio * 0.2

        return {"data": data, "confidence": min(confidence, 1.0)}

    def _update_beliefs(self, beliefs: Dict, result: Dict, attempt: int, confidence: float):
        """Update mental model beliefs after an extraction attempt."""
        if isinstance(result, dict):
            # Track which fields were successfully extracted
            for key, value in result.items():
                if value is not None and value != "" and value != []:
                    beliefs[f"field_{key}_present"] = True
                else:
                    beliefs[f"field_{key}_present"] = False

            beliefs["last_confidence"] = confidence
            beliefs["attempt"] = attempt