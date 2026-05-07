"""
Huginn Extractor — Structured data extraction with guaranteed JSON output.

The /v1/extract endpoint engine. Uses LLM providers to extract structured data
from web pages based on:
- A JSON schema (user-defined)
- A predefined template (product, article, job, etc.)
- A free-form prompt

Key improvements over v0:
1. JSON repair pipeline: multiple parsing strategies with progressive fallback
2. Template system: 10 battle-tested schemas with field guides
3. Schema-guided retry: each retry gets better context about what failed
4. Field-level validation reporting: know exactly which fields failed
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .browser import BrowserManager
from .models import OutputFormat, ScrapeData
from .scraper import Scraper

logger = logging.getLogger(__name__)


class ExtractionResult:
    """Result of a structured extraction attempt."""

    def __init__(self, data: Dict[str, Any], confidence: float, attempts: int, validation_errors: Optional[List[str]] = None):
        self.data = data
        self.confidence = confidence
        self.attempts = attempts
        self.validation_errors = validation_errors or []


class Extractor:
    """Extract structured data from web pages using LLM with guaranteed JSON."""

    def __init__(
        self,
        browser: BrowserManager,
        llm_provider: str = "openai",
        llm_model: Optional[str] = None,
        max_retries: int = 3,
        mental_model: bool = True,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.browser = browser
        self.scraper = Scraper(browser)
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.max_retries = max_retries
        self.mental_model = mental_model
        self._http_client = http_client

    async def extract(
        self,
        urls: List[str],
        prompt: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        output_format: str = "json",
        template: Optional[Any] = None,
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        """
        Extract structured data from one or more URLs.

        Args:
            urls: URLs to scrape and extract from
            prompt: Free-form extraction instruction
            schema: JSON schema dict for structured output
            system_prompt: Override system prompt for LLM
            output_format: "json", "markdown", or "text"
            template: Optional ExtractTemplate for predefined schemas
            include_raw: Whether to include raw LLM response in output

        Returns:
            Dict with "data", "confidence", "attempts", "sources", "success",
            and optionally "validation_errors", "raw_response".
        """
        all_texts: List[str] = []
        page_metadata: List[Dict] = []

        # Scrape all URLs concurrently
        tasks = [self.scraper.scrape(url=url, formats=[OutputFormat.MARKDOWN]) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Failed to scrape %s: %s", urls[i], result)
                continue
            if isinstance(result, ScrapeData) and result.markdown:
                all_texts.append(result.markdown)
                page_metadata.append({
                    "url": urls[i],
                    "title": result.metadata.get("title", "") if result.metadata else "",
                    "length": len(result.markdown),
                })

        if not all_texts:
            return {
                "error": "No content could be extracted from any URL",
                "success": False,
                "sources": [{"url": u, "error": "failed"} for u in urls],
            }

        # Concatenate with page markers
        combined_text = self._concatenate_pages(all_texts, page_metadata, template)

        # Determine effective schema/system prompt from template if provided
        effective_schema, effective_system, fields_guide, merge_strategy = self._resolve_template(
            template, schema, system_prompt
        )

        # Build extraction prompt
        extraction_prompt = self._build_prompt(
            combined_text, prompt, effective_schema, page_metadata, output_format, fields_guide
        )

        # Extract with guaranteed JSON + retry
        result = await self._extract_with_llm(
            extraction_prompt,
            effective_schema,
            effective_system,
            combined_text,
            merge_strategy=merge_strategy,
        )

        response: Dict[str, Any] = {
            "data": result.data,
            "confidence": result.confidence,
            "attempts": result.attempts,
            "sources": page_metadata,
            "success": result.confidence >= 0.3,
        }
        if result.validation_errors:
            response["validation_errors"] = result.validation_errors
        if include_raw and hasattr(result, "_raw_response"):
            response["raw_response"] = getattr(result, "_raw_response")

        return response

    def _concatenate_pages(
        self,
        texts: List[str],
        metadata: List[Dict],
        template: Optional[Any],
    ) -> str:
        """Concatenate page texts with markers, respecting template limits."""
        max_chars = 50_000
        if template and hasattr(template, "max_page_chars"):
            max_chars = getattr(template, "max_page_chars")

        parts: List[str] = []
        total_chars = 0
        max_total = 120_000  # Hard cap across all pages

        for i, (text, meta) in enumerate(zip(texts, metadata)):
            page_text = text[:max_chars]
            marker = f"\n---\n📄 Page {i + 1}: {meta.get('title', '')}\n🔗 URL: {meta['url']}\n---\n"
            chunk = marker + page_text
            if total_chars + len(chunk) > max_total:
                # Truncate last page to fit
                remaining = max_total - total_chars - len(marker) - 100
                if remaining > 500:
                    page_text = page_text[:remaining]
                    chunk = marker + page_text
                else:
                    break
            parts.append(chunk)
            total_chars += len(chunk)

        return "".join(parts)

    def _resolve_template(
        self,
        template: Optional[Any],
        schema: Optional[Dict[str, Any]],
        system_prompt: Optional[str],
    ) -> Tuple[Optional[Dict], Optional[str], Optional[Dict[str, str]], str]:
        """Resolve template-provided schema/system prompt vs user overrides."""
        if template is None:
            return schema, system_prompt, None, "concat"

        # Template attributes ( ExtractTemplate or duck-typed)
        t_schema = getattr(template, "schema", None)
        t_system = getattr(template, "system_prompt", None)
        t_guide = getattr(template, "fields_guide", None)
        t_merge = getattr(template, "merge_strategy", "concat")

        # User overrides take priority
        effective_schema = schema or t_schema
        effective_system = system_prompt or t_system
        return effective_schema, effective_system, t_guide, t_merge

    def _build_prompt(
        self,
        text: str,
        prompt: Optional[str],
        schema: Optional[Dict[str, Any]],
        page_metadata: List[Dict],
        output_format: str,
        fields_guide: Optional[Dict[str, str]] = None,
    ) -> str:
        """Build the LLM extraction prompt with schema and field guides."""
        parts: List[str] = []

        # Task description
        if prompt:
            parts.append(f"Extract the following information from web page content:\n\nTask: {prompt}\n")
        else:
            parts.append("Extract all relevant structured information from the following web page content.\n")

        # Schema block
        if schema:
            parts.append(f"\n📋 OUTPUT SCHEMA (return valid JSON matching this exactly):\n```json\n{json.dumps(schema, indent=2)}\n```\n")

        # Field guides: LLM performance improves when given hints about where to find data
        if fields_guide:
            parts.append("\n🔍 FIELD GUIDES (where to find each piece of data on the page):\n")
            for field, guide in fields_guide.items():
                parts.append(f"  • {field}: {guide}\n")

        # Constraints based on output format
        parts.append("\n⚠️ RULES:\n")
        if output_format == "text":
            parts.append("  1. Return your extraction as plain text.\n")
            parts.append("  2. Do NOT use JSON formatting.\n")
            parts.append("  3. Do NOT invent data that is not on the page.\n")
        elif output_format == "json" or schema:
            parts.append("  1. Return ONLY a JSON object. No markdown code blocks, no commentary.\n")
            parts.append("  2. If a field is not found on the page, use null (not empty string).\n")
            parts.append("  3. For arrays, return [] if no items found.\n")
            parts.append("  4. Do NOT invent data that is not on the page.\n")
            if schema and "required" in schema:
                parts.append(f"  5. Required fields: {', '.join(schema['required'])}\n")
        else:
            parts.append("  1. Return your extraction as a JSON object.\n")
            parts.append("  2. Do NOT invent data that is not on the page.\n")

        # Source pages
        parts.append(f"\n📄 SOURCE PAGES ({len(page_metadata)}):\n")
        for meta in page_metadata[:5]:
            parts.append(f"  • {meta.get('title', 'Untitled')} — {meta['url']}\n")

        parts.append(f"\n{'='*60}\n{text}\n{'='*60}\n")

        return "".join(parts)

    async def _extract_with_llm(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        system_prompt: Optional[str],
        raw_text: str,
        merge_strategy: str = "concat",
    ) -> ExtractionResult:
        """Call LLM for extraction with guaranteed JSON + progressive repair."""

        beliefs: Dict[str, Any] = {}
        best_data: Dict[str, Any] = {}
        best_confidence = 0.0
        best_errors: List[str] = []
        last_raw_response = ""

        sys_msg = system_prompt or (
            "You are a precise data extraction engine. You extract structured data "
            "from web pages and return it as valid JSON. Never add markdown formatting, "
            "never wrap in ```json blocks. Raw JSON output only."
        )

        for attempt in range(1, self.max_retries + 1):
            try:
                # Build context from previous failures
                belief_context = ""
                if self.mental_model and beliefs:
                    belief_context = "\n\n📚 PREVIOUS ATTEMPTS:\n"
                    for key, value in beliefs.items():
                        if not key.startswith("attempt_"):
                            belief_context += f"  • {key}: {value}\n"
                    if best_errors:
                        belief_context += f"\n  Previous validation errors: {', '.join(best_errors[:3])}\n"
                    belief_context += "\nUse this context to improve your extraction.\n"

                full_prompt = prompt + belief_context
                if attempt > 1:
                    full_prompt += (
                        f"\n\n⚠️ ATTEMPT {attempt}/{self.max_retries}: "
                        f"The previous response had issues. Focus on fixing: "
                        f"{', '.join(best_errors[:3]) if best_errors else 'JSON validity'}\n"
                    )

                result_data, raw_response = await self._call_llm(
                    full_prompt, schema, sys_msg, force_json=True
                )
                last_raw_response = raw_response

                # Validate
                if schema:
                    validated = self._validate_schema(result_data, schema)
                    confidence = validated["confidence"]
                    errors = validated.get("validation_errors", [])
                    result_data = validated["data"]
                else:
                    confidence = 0.8 if isinstance(result_data, dict) else 0.3
                    errors = []

                # Update beliefs
                if self.mental_model:
                    self._update_beliefs(beliefs, result_data, attempt, confidence, errors)

                # Track best
                if confidence > best_confidence:
                    best_data = result_data
                    best_confidence = confidence
                    best_errors = errors

                # Success threshold
                if confidence >= 0.75 and not errors:
                    result = ExtractionResult(result_data, confidence, attempt, errors)
                    result._raw_response = raw_response  # type: ignore
                    return result

                # Early exit if we hit max confidence with minor errors
                if confidence >= 0.85:
                    result = ExtractionResult(result_data, confidence, attempt, errors)
                    result._raw_response = raw_response  # type: ignore
                    return result

            except Exception as e:
                logger.warning("Extraction attempt %d failed: %s", attempt, e)
                if self.mental_model:
                    beliefs[f"attempt_{attempt}_error"] = str(e)[:200]

        # Return best result even if below threshold
        result = ExtractionResult(
            best_data if best_data else {"error": "All extraction attempts failed"},
            best_confidence,
            self.max_retries,
            best_errors,
        )
        result._raw_response = last_raw_response  # type: ignore
        return result

    async def _call_llm(
        self,
        prompt: str,
        schema: Optional[Dict[str, Any]],
        system_prompt: Optional[str],
        force_json: bool = True,
    ) -> Tuple[Dict[str, Any], str]:
        """Call the configured LLM provider. Returns (parsed_data, raw_response)."""

        sys_msg = system_prompt or "You are a precise data extraction engine. Return valid JSON only."
        messages = [{"role": "system", "content": sys_msg}]
        messages.append({"role": "user", "content": prompt})

        if self.llm_provider in ("openai", "xai", "ollama"):
            raw = await self._call_openai_compatible(messages, schema, force_json)
        elif self.llm_provider == "anthropic":
            raw = await self._call_anthropic(messages, schema, force_json)
        elif self.llm_provider == "google":
            raw = await self._call_google(messages, schema, force_json)
        else:
            raw = await self._call_openai_compatible(messages, schema, force_json)

        parsed = self._parse_json_response(raw, schema)
        return parsed, raw

    async def _call_openai_compatible(
        self, messages: List[Dict], schema: Optional[Dict], force_json: bool
    ) -> str:
        """Call OpenAI-compatible API (OpenAI, xAI, Ollama). Returns raw text."""

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
                "base_url": os.getenv("OLLAMA_BASE_URL", "https://ollama.com/api"),
                "key_env": "OLLAMA_API_KEY",
                "default_model": "llama3.3",
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

        if self._http_client:
            client = self._http_client
            close_after = False
        else:
            client = httpx.AsyncClient(timeout=60)
            close_after = True
        try:
            body: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
            }

            if force_json:
                if self.llm_provider == "ollama":
                    body["format"] = "json"
                else:
                    body["response_format"] = {"type": "json_object"}

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            if self.llm_provider == "ollama":
                is_cloud = "ollama.com" in config["base_url"]
                if not is_cloud:
                    headers.pop("Authorization", None)
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
            return data["choices"][0]["message"]["content"]
        finally:
            if close_after:
                await client.aclose()

    async def _call_anthropic(
        self, messages: List[Dict], schema: Optional[Dict], force_json: bool
    ) -> str:
        """Call Anthropic Claude API. Returns raw text."""
        try:
            import anthropic
            client = anthropic.AsyncAnthropic()
            model = self.llm_model or "claude-3-5-sonnet-20241022"

            system_msg = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    user_messages.append({"role": msg["role"], "content": msg["content"]})

            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_msg,
                messages=user_messages,
            )
            return response.content[0].text
        except ImportError:
            raise RuntimeError("anthropic package not installed. pip install anthropic")

    async def _call_google(
        self, messages: List[Dict], schema: Optional[Dict], force_json: bool
    ) -> str:
        """Call Google Gemini API. Returns raw text."""
        model = self.llm_model or "gemini-2.0-flash"
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "LLM provider 'google' requires GOOGLE_API_KEY environment variable. "
                "Set it or use --llm-provider ollama for local models."
            )

        if self._http_client:
            client = self._http_client
            close_after = False
        else:
            client = httpx.AsyncClient(timeout=60)
            close_after = True
        try:
            system_instruction = None
            contents = []
            for msg in messages:
                if msg["role"] == "system":
                    system_instruction = {"parts": [{"text": msg["content"]}]}
                else:
                    role = "model" if msg["role"] == "assistant" else "user"
                    contents.append({"role": role, "parts": [{"text": msg["content"]}]})

            body: Dict[str, Any] = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json" if force_json else "text/plain",
                },
            }
            if system_instruction:
                body["systemInstruction"] = system_instruction

            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=***",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        finally:
            if close_after:
                await client.aclose()

    def _parse_json_response(
        self,
        content: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Parse JSON from LLM response with progressive repair.

        Strategy (fastest to slowest):
        1. Direct JSON parse after stripping markdown wrappers
        2. Find outermost JSON object via bracket matching
        3. Find any JSON object by regex
        4. Find JSON array if object not found
        5. Repair common LLM JSON errors (trailing commas, unclosed quotes, etc.)
        6. Last resort: wrap the whole thing in a {"raw_response": ...}
        """
        if not content or not content.strip():
            return {"raw_response": "", "parse_error": True, "error": "Empty response"}

        cleaned = self._strip_markdown(content)

        # Strategy 1: Direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Outermost object via bracket matching
        parsed = self._find_json_object(cleaned)
        if parsed is not None:
            return parsed

        # Strategy 3: Any JSON object by regex
        obj_match = re.search(r'\{[\s\S]*?\}', cleaned)
        if obj_match:
            try:
                return json.loads(obj_match.group(0))
            except json.JSONDecodeError:
                pass

        # Strategy 4: JSON array
        arr_match = re.search(r'\[[\s\S]*?\]', cleaned)
        if arr_match:
            try:
                arr = json.loads(arr_match.group(0))
                # Wrap array in object if schema expects object
                if schema and schema.get("type") == "object":
                    # Try to infer key from array items
                    return {"items": arr}
                return arr
            except json.JSONDecodeError:
                pass

        # Strategy 5: Repair common errors
        repaired = self._repair_json(cleaned)
        if repaired:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        # Strategy 6: Last resort
        return {"raw_response": content[:2000], "parse_error": True}

    @staticmethod
    def _strip_markdown(content: str) -> str:
        """Remove markdown code block wrappers."""
        content = content.strip()
        # ```json ... ```
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()

    @staticmethod
    def _find_json_object(content: str) -> Optional[Dict[str, Any]]:
        """Find the outermost JSON object via bracket matching."""
        start = content.find("{")
        if start == -1:
            return None
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
                        break
        return None

    @staticmethod
    def _repair_json(content: str) -> Optional[str]:
        """Repair common LLM-generated JSON errors."""
        # Remove trailing commas before ] or }
        content = re.sub(r',\s*([\}\]])', r'\1', content)
        # Remove comments (some LLMs add them)
        content = re.sub(r'//.*?\n', '\n', content)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        # Fix unescaped newlines in strings (common LLM bug)
        content = re.sub(r'(?<=")([^"]*)\n([^"]*)(?=")', lambda m: m.group(1) + '\\n' + m.group(2), content)
        return content if content.strip() else None

    def _validate_schema(
        self,
        data: Any,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Validate extracted data against JSON schema with detailed error reporting.

        Returns dict with "data", "confidence", and optional "validation_errors".
        """
        if not isinstance(data, dict):
            return {
                "data": data,
                "confidence": 0.2,
                "validation_errors": ["Expected top-level JSON object"],
            }

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        errors: List[str] = []

        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        # Required fields check
        for field in required:
            if field not in data or data[field] is None:
                errors.append(f"Missing required field: '{field}'")

        # Type and nested validation
        def validate_field(name: str, value: Any, field_schema: Dict, path: str = "") -> None:
            if value is None:
                return
            expected_type = field_schema.get("type")
            if expected_type and expected_type in type_map:
                expected = type_map[expected_type]
                if expected_type == "number" and isinstance(value, (int, float)):
                    pass
                elif expected_type == "integer" and isinstance(value, int):
                    pass
                elif not isinstance(value, expected):
                    errors.append(
                        f"Field '{path}{name}' has type {type(value).__name__}, expected {expected_type}"
                    )

            # Nested object
            if isinstance(value, dict) and expected_type == "object":
                nested_props = field_schema.get("properties", {})
                nested_required = field_schema.get("required", [])
                for req_field in nested_required:
                    if req_field not in value or value[req_field] is None:
                        errors.append(f"Missing required field: '{path}{name}.{req_field}'")
                for k, v in value.items():
                    if k in nested_props:
                        validate_field(k, v, nested_props[k], f"{path}{name}.")

            # Array items
            if isinstance(value, list) and expected_type == "array":
                item_schema = field_schema.get("items")
                if isinstance(item_schema, dict):
                    for idx, item in enumerate(value):
                        validate_field(f"[{idx}]", item, item_schema, f"{path}{name}")

        for field, field_schema in properties.items():
            if field in data:
                validate_field(field, data[field], field_schema)

        # Confidence scoring
        filled_required = sum(1 for r in required if r in data and data[r] is not None)
        filled_optional = sum(
            1 for k in properties if k in data and data[k] is not None and k not in required
        )

        total_fields = len(properties)
        req_ratio = filled_required / len(required) if required else 1.0
        opt_ratio = filled_optional / max(total_fields - len(required), 1)

        error_penalty = min(len(errors) * 0.12, 0.5)
        confidence = max(req_ratio * 0.75 + opt_ratio * 0.15 - error_penalty, 0.0)
        confidence = min(confidence, 1.0)

        result: Dict[str, Any] = {"data": data, "confidence": confidence}
        if errors:
            result["validation_errors"] = errors
        return result

    def _update_beliefs(
        self,
        beliefs: Dict,
        result: Any,
        attempt: int,
        confidence: float,
        errors: List[str],
    ) -> None:
        """Update mental model beliefs after an extraction attempt."""
        if isinstance(result, dict):
            for key, value in result.items():
                if value is not None and value != "" and value != []:
                    beliefs[f"field_{key}_present"] = True
                else:
                    beliefs[f"field_{key}_present"] = False
            beliefs["last_confidence"] = confidence
            beliefs["attempt"] = attempt
            if errors:
                beliefs["last_errors"] = errors[:5]
