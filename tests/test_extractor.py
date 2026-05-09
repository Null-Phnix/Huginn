"""
Tests for Huginn Extractor — LLM prompt building, JSON parsing, schema validation.
"""

import json
import pytest

from huginn.extractor import Extractor, ExtractionResult


class TestPromptBuilding:
    """Test LLM extraction prompt construction."""

    def setup_method(self):
        self.extractor = Extractor(browser=None, llm_provider="openai")

    def test_basic_prompt(self):
        prompt = self.extractor._build_prompt(
            text="Hello World",
            prompt="Extract the greeting",
            schema=None,
            page_metadata=[{"url": "https://example.com", "title": "Test", "length": 11}],
            output_format="markdown",
        )
        assert "Extract the greeting" in prompt
        assert "Hello World" in prompt
        assert "Test" in prompt

    def test_prompt_with_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
        }
        prompt = self.extractor._build_prompt(
            text="Some text",
            prompt=None,
            schema=schema,
            page_metadata=[{"url": "https://example.com", "title": "Test", "length": 9}],
            output_format="json",
        )
        assert "schema" in prompt.lower() or "properties" in prompt
        assert "title" in prompt
        assert "content" in prompt


class TestJSONParsing:
    """Test JSON response parsing from LLM."""

    def setup_method(self):
        self.extractor = Extractor(browser=None)

    def test_parse_clean_json(self):
        content = '{"title": "Hello", "content": "World"}'
        result = self.extractor._parse_json_response(content)
        assert result["title"] == "Hello"
        assert result["content"] == "World"

    def test_parse_json_with_code_block(self):
        content = '```json\n{"title": "Hello"}\n```'
        result = self.extractor._parse_json_response(content)
        assert result["title"] == "Hello"

    def test_parse_json_with_generic_code_block(self):
        content = '```\n{"title": "Hello"}\n```'
        result = self.extractor._parse_json_response(content)
        assert result["title"] == "Hello"

    def test_parse_json_with_surrounding_text(self):
        content = 'Here is the result:\n{"title": "Hello"}\nThat is all.'
        result = self.extractor._parse_json_response(content)
        assert result["title"] == "Hello"

    def test_parse_invalid_json(self):
        content = "This is not JSON at all"
        result = self.extractor._parse_json_response(content)
        assert "raw_response" in result
        assert result["parse_error"] is True

    def test_parse_nested_json(self):
        content = '{"data": {"nested": true}, "count": 5}'
        result = self.extractor._parse_json_response(content)
        assert result["data"]["nested"] is True
        assert result["count"] == 5


class TestSchemaValidation:
    """Test schema validation and confidence scoring."""

    def setup_method(self):
        self.extractor = Extractor(browser=None)

    def test_validate_all_required_present(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
            "required": ["title", "content"],
        }
        data = {"title": "Test", "content": "Hello"}
        result = self.extractor._validate_schema(data, schema)
        assert result["confidence"] >= 0.7

    def test_validate_missing_required(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
            "required": ["title", "content"],
        }
        data = {"title": "Test"}  # Missing content
        result = self.extractor._validate_schema(data, schema)
        assert result["confidence"] < 0.7

    def test_validate_no_required_fields(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
        }
        data = {"title": "Test"}
        result = self.extractor._validate_schema(data, schema)
        assert result["confidence"] >= 0.7

    def test_validate_non_dict_data(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        result = self.extractor._validate_schema("not a dict", schema)
        assert result["confidence"] < 0.5

    def test_validate_empty_data(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        result = self.extractor._validate_schema({}, schema)
        assert result["confidence"] < 0.3

    # ── Type checking tests ──

    def test_validate_type_mismatch(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}, "title": {"type": "string"}},
            "required": [],
        }
        data = {"count": "not_a_number", "title": "valid string"}
        result = self.extractor._validate_schema(data, schema)
        assert "validation_errors" in result
        assert any("count" in e and "type" in e.lower() for e in result["validation_errors"])

    def test_validate_type_correct(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}, "active": {"type": "boolean"}},
            "required": [],
        }
        data = {"count": 42, "active": True}
        result = self.extractor._validate_schema(data, schema)
        assert result["confidence"] > 0.5
        assert "validation_errors" not in result

    def test_validate_number_accepts_int_and_float(self):
        schema = {
            "type": "object",
            "properties": {"price": {"type": "number"}},
            "required": [],
        }
        result_int = self.extractor._validate_schema({"price": 10}, schema)
        result_float = self.extractor._validate_schema({"price": 10.5}, schema)
        assert result_int["confidence"] > 0.5
        assert result_float["confidence"] > 0.5

    def test_validate_array_type(self):
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array"}},
            "required": [],
        }
        result = self.extractor._validate_schema({"tags": ["a", "b"]}, schema)
        assert result["confidence"] > 0.5

        result_bad = self.extractor._validate_schema({"tags": "not array"}, schema)
        assert "validation_errors" in result_bad

    # ── Required field error messages ──

    def test_validate_reports_missing_required(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
            "required": ["name", "email"],
        }
        result = self.extractor._validate_schema({}, schema)
        assert "validation_errors" in result
        missing_fields = [e for e in result["validation_errors"] if "Missing required" in e]
        assert len(missing_fields) == 2

    def test_validate_non_dict_reports_error(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = self.extractor._validate_schema([1, 2, 3], schema)
        assert result["confidence"] <= 0.3
        assert "validation_errors" in result

    # ── Nested schema validation ──

    def test_validate_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "address": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "zip": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
            "required": ["name"],
        }
        data = {
            "name": "Test",
            "address": {"city": "Berlin", "zip": "10115"},
        }
        result = self.extractor._validate_schema(data, schema)
        assert result["confidence"] > 0.5
        assert "validation_errors" not in result

    def test_validate_nested_object_missing_required(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "address": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
            "required": ["name"],
        }
        data = {"name": "Test", "address": {}}
        result = self.extractor._validate_schema(data, schema)
        assert "validation_errors" in result
        assert any("address" in e and "Missing required" in e for e in result["validation_errors"])

    def test_validate_nested_object_type_error(self):
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
            },
        }
        data = {"meta": {"count": "not_int"}}
        result = self.extractor._validate_schema(data, schema)
        assert "validation_errors" in result
        # Nested errors are prefixed with parent field: "meta.Field 'count' ..."
        assert any("meta" in e and "count" in e for e in result["validation_errors"])

    # ── Type penalty reduces confidence ──

    def test_validate_type_errors_reduce_confidence(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
                "c": {"type": "boolean"},
            },
            "required": ["a", "b", "c"],
        }
        # All present but wrong types
        data = {"a": 1, "b": "x", "c": "yes"}
        result = self.extractor._validate_schema(data, schema)
        assert result["confidence"] < 0.5


class TestOutputFormat:
    """Test the output_format parameter and _build_prompt format instructions."""

    def setup_method(self):
        self.extractor = Extractor(browser=None)

    def test_build_prompt_json_with_schema(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        prompt = self.extractor._build_prompt(
            "some text", "Get the title", schema, [], output_format="json"
        )
        assert "schema" in prompt.lower() or "JSON" in prompt

    def test_build_prompt_json_without_schema(self):
        prompt = self.extractor._build_prompt(
            "some text", "Summarize", None, [], output_format="json"
        )
        assert "JSON" in prompt

    def test_build_prompt_markdown_format(self):
        """Markdown is default — with no schema it asks for JSON object output."""
        prompt = self.extractor._build_prompt(
            "some text", "Summarize", None, [], output_format="markdown"
        )
        # Markdown without schema defaults to JSON-object-style extraction
        assert "JSON" in prompt or "json" in prompt.lower()

    def test_build_prompt_text_format(self):
        prompt = self.extractor._build_prompt(
            "some text", "Summarize", None, [], output_format="text"
        )
        assert "plain text" in prompt.lower() or "JSON" not in prompt


class TestDistillRequestFormat:
    """Test DistillRequest format field and model validation."""

    def test_format_default_is_markdown(self):
        from huginn.models import DistillRequest
        req = DistillRequest(urls=["https://example.com"])
        assert req.format == "markdown"

    def test_format_can_be_json(self):
        from huginn.models import DistillRequest
        req = DistillRequest(urls=["https://example.com"], format="json")
        assert req.format == "json"

    def test_format_can_be_text(self):
        from huginn.models import DistillRequest
        req = DistillRequest(urls=["https://example.com"], format="text")
        assert req.format == "text"

    def test_backward_compat_alias(self):
        from huginn.models import DistillRequest, ExtractRequest
        assert ExtractRequest is DistillRequest


class TestBeliefUpdates:
    """Test mental model belief tracking."""

    def setup_method(self):
        self.extractor = Extractor(browser=None, mental_model=True)

    def test_update_beliefs_successful(self):
        beliefs = {}
        self.extractor._update_beliefs(beliefs, {"title": "Test", "content": "Hello"}, 1, 0.8, [])
        assert beliefs["field_title_present"] is True
        assert beliefs["field_content_present"] is True
        assert beliefs["last_confidence"] == 0.8

    def test_update_beliefs_missing_fields(self):
        beliefs = {}
        self.extractor._update_beliefs(beliefs, {"title": None, "content": ""}, 1, 0.3, [])
        assert beliefs["field_title_present"] is False
        assert beliefs["field_content_present"] is False

    def test_beliefs_persist_across_attempts(self):
        beliefs = {}
        self.extractor._update_beliefs(beliefs, {"title": "Test"}, 1, 0.5, [])
        self.extractor._update_beliefs(beliefs, {"title": "Better", "content": "Added"}, 2, 0.9, [])
        assert beliefs["attempt"] == 2
        assert beliefs["last_confidence"] == 0.9


class TestExtractionResult:
    """Test ExtractionResult data class."""

    def test_creation(self):
        result = ExtractionResult(data={"title": "Test"}, confidence=0.9, attempts=2)
        assert result.data == {"title": "Test"}
        assert result.confidence == 0.9
        assert result.attempts == 2


class TestPydanticValidation:
    """Test Pydantic model validation in extraction pipeline."""

    def setup_method(self):
        self.extractor = Extractor(browser=None, llm_provider="openai")

    def test_pydantic_valid_data(self):
        from pydantic import BaseModel

        class Product(BaseModel):
            name: str
            price: float

        result = self.extractor._validate_with_pydantic(
            {"name": "Widget", "price": 19.99}, Product
        )
        assert result["confidence"] == 1.0
        assert "validation_errors" not in result
        assert result["data"]["name"] == "Widget"

    def test_pydantic_invalid_data(self):
        from pydantic import BaseModel

        class Product(BaseModel):
            name: str
            price: float

        result = self.extractor._validate_with_pydantic(
            {"name": "Widget", "price": "free"}, Product
        )
        assert result["confidence"] < 1.0
        assert "validation_errors" in result
        assert any("price" in err for err in result["validation_errors"])

    def test_pydantic_non_dict_input(self):
        from pydantic import BaseModel

        class Product(BaseModel):
            name: str

        result = self.extractor._validate_with_pydantic("not a dict", Product)
        assert result["confidence"] < 1.0
        assert "validation_errors" in result


class TestExamplesInPrompt:
    """Test example-driven extraction prompt building."""

    def setup_method(self):
        self.extractor = Extractor(browser=None, llm_provider="openai")

    def test_prompt_includes_examples(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        examples = [{"title": "Example 1"}, {"title": "Example 2"}]
        prompt = self.extractor._build_prompt(
            text="Hello World",
            prompt="Extract the title",
            schema=schema,
            page_metadata=[{"url": "https://example.com", "title": "Test", "length": 11}],
            output_format="json",
            examples=examples,
        )
        assert "EXAMPLES" in prompt
        assert "Example 1" in prompt
        assert "Example 2" in prompt
        assert "Widget" not in prompt  # Sanity

    def test_prompt_without_examples(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        prompt = self.extractor._build_prompt(
            text="Hello World",
            prompt="Extract the title",
            schema=schema,
            page_metadata=[{"url": "https://example.com", "title": "Test", "length": 11}],
            output_format="json",
        )
        assert "EXAMPLES" not in prompt