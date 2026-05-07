"""Tests for Huginn templates module."""

import pytest

from huginn.templates import (
    ExtractTemplate,
    get_all_templates,
    get_template,
    list_templates,
    register_template,
)


class TestTemplateRegistry:
    """Test template registration and retrieval."""

    def test_list_templates(self):
        names = list_templates()
        assert "product" in names
        assert "article" in names
        assert "job_posting" in names
        assert "real_estate" in names
        assert len(names) == 10

    def test_get_template_product(self):
        t = get_template("product")
        assert t.name == "product"
        assert "product_name" in t.schema["properties"]
        assert "price" in t.schema["properties"]
        assert "product_name" in t.schema["required"]

    def test_get_template_article(self):
        t = get_template("article")
        assert t.name == "article"
        assert "title" in t.schema["required"]
        assert "content" in t.schema["required"]

    def test_get_template_missing(self):
        with pytest.raises(KeyError):
            get_template("nonexistent")

    def test_all_templates_have_schema(self):
        for name, t in get_all_templates().items():
            assert t.schema.get("type") == "object"
            assert t.schema.get("properties")
            assert t.system_prompt

    def test_fields_guide_present(self):
        t = get_template("product")
        assert "price" in t.fields_guide
        assert "rating" in t.fields_guide

    def test_register_custom_template(self):
        custom = ExtractTemplate(
            name="custom_test",
            description="Test template",
            schema={"type": "object", "properties": {"x": {"type": "string"}}},
            system_prompt="Extract x.",
        )
        register_template("custom_test", custom)
        assert "custom_test" in list_templates()
        t = get_template("custom_test")
        assert t.name == "custom_test"
