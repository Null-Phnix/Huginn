"""
Tests for BlackCrawl Extractor — LLM prompt building, JSON parsing, schema validation.
"""

import json
import pytest

from blackcrawl.extractor import Extractor, ExtractionResult


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


class TestBeliefUpdates:
    """Test mental model belief tracking."""

    def setup_method(self):
        self.extractor = Extractor(browser=None, mental_model=True)

    def test_update_beliefs_successful(self):
        beliefs = {}
        self.extractor._update_beliefs(beliefs, {"title": "Test", "content": "Hello"}, 1, 0.8)
        assert beliefs["field_title_present"] is True
        assert beliefs["field_content_present"] is True
        assert beliefs["last_confidence"] == 0.8

    def test_update_beliefs_missing_fields(self):
        beliefs = {}
        self.extractor._update_beliefs(beliefs, {"title": None, "content": ""}, 1, 0.3)
        assert beliefs["field_title_present"] is False
        assert beliefs["field_content_present"] is False

    def test_beliefs_persist_across_attempts(self):
        beliefs = {}
        self.extractor._update_beliefs(beliefs, {"title": "Test"}, 1, 0.5)
        self.extractor._update_beliefs(beliefs, {"title": "Better", "content": "Added"}, 2, 0.9)
        assert beliefs["attempt"] == 2
        assert beliefs["last_confidence"] == 0.9


class TestExtractionResult:
    """Test ExtractionResult data class."""

    def test_creation(self):
        result = ExtractionResult(data={"title": "Test"}, confidence=0.9, attempts=2)
        assert result.data == {"title": "Test"}
        assert result.confidence == 0.9
        assert result.attempts == 2