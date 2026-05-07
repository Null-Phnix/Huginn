"""
Huginn Extract Templates — Predefined schemas for common extraction tasks.

Instead of making users write JSON schemas from scratch, provide battle-tested
templates for the most common extraction workflows:
- product (e-commerce product pages)
- article (news, blog posts)
- job_posting (job listings)
- real_estate (property listings)
- person (contact/team pages)
- event (event/conference pages)
- review (product/service reviews)
- faq (FAQ pages)
- recipe (recipe pages)
- research_paper (academic papers)

Usage:
    from huginn.templates import get_template, ExtractTemplate
    template = get_template("product")
    result = await extractor.extract(urls=["https://example.com/item"], template=template)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class ExtractTemplate:
    """A predefined extraction template with schema and system prompt."""
    name: str
    description: str
    schema: Dict[str, Any]
    system_prompt: str
    fields_guide: Dict[str, str] = field(default_factory=dict)
    # Max context window per page for this template (some need more)
    max_page_chars: int = 50_000
    # Whether to merge multi-page results or treat separately
    merge_strategy: str = "concat"  # "concat", "list", "merge"


def _product_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="product",
        description="Extract product details from e-commerce pages",
        schema={
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "The name/title of the product"},
                "brand": {"type": "string", "description": "Brand or manufacturer name"},
                "price": {"type": "string", "description": "Current price with currency symbol"},
                "original_price": {"type": "string", "description": "Original/strikethrough price if on sale"},
                "currency": {"type": "string", "description": "Currency code: USD, EUR, GBP, etc."},
                "availability": {"type": "string", "description": "In stock, out of stock, pre-order, etc."},
                "rating": {"type": "number", "description": "Average rating (e.g. 4.5)"},
                "review_count": {"type": "integer", "description": "Number of reviews"},
                "description": {"type": "string", "description": "Product description or summary"},
                "features": {"type": "array", "items": {"type": "string"}, "description": "Key product features/bullet points"},
                "specifications": {"type": "object", "description": "Technical specs as key-value pairs"},
                "images": {"type": "array", "items": {"type": "string"}, "description": "URLs of product images"},
                "category": {"type": "string", "description": "Product category or breadcrumbs"},
                "sku": {"type": "string", "description": "Product SKU or model number"},
                "url": {"type": "string", "description": "Canonical product URL"},
            },
            "required": ["product_name", "price"],
        },
        system_prompt=(
            "You are a product data extraction engine. Extract product information from "
            "e-commerce page content. Be precise with prices — include currency symbols. "
            "If a field is not present on the page, use null. "
            "Return valid JSON only, no markdown wrappers, no commentary."
        ),
        fields_guide={
            "price": "Look for numbers near currency symbols ($, €, £, ¥). Check sale sections.",
            "rating": "Often shown as stars (1-5) or a number like 4.5/5. Extract just the number.",
            "availability": "Look for 'Add to Cart', 'Out of Stock', 'Sold Out', 'Pre-order' indicators.",
            "features": "Extract bullet points, often in a 'Features' or 'Highlights' section.",
        },
    )


def _article_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="article",
        description="Extract article/blog post content and metadata",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "author": {"type": "string"},
                "published_date": {"type": "string", "description": "ISO 8601 date if available"},
                "summary": {"type": "string", "description": "1-2 sentence summary of the article"},
                "content": {"type": "string", "description": "Full article text, cleaned"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "category": {"type": "string"},
                "reading_time_minutes": {"type": "integer"},
                "url": {"type": "string"},
                "word_count": {"type": "integer"},
            },
            "required": ["title", "content"],
        },
        system_prompt=(
            "You are an article extraction engine. Extract the full article text, metadata, "
            "and summary. Preserve the article structure in the content field. "
            "Remove navigation, ads, and unrelated sidebar content. "
            "Return valid JSON only."
        ),
        fields_guide={
            "published_date": "Look for <time> tags, 'Published on', 'Updated' near the article header.",
            "author": "Often near the title or in a byline. May be a name or a publication.",
            "content": "Extract the main article body, not comments or related articles.",
        },
    )


def _job_posting_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="job_posting",
        description="Extract job listing details",
        schema={
            "type": "object",
            "properties": {
                "job_title": {"type": "string"},
                "company": {"type": "string"},
                "location": {"type": "string"},
                "remote_status": {"type": "string", "description": "remote, hybrid, onsite, or unknown"},
                "salary": {"type": "string", "description": "Salary range or compensation info"},
                "employment_type": {"type": "string", "description": "full-time, part-time, contract, internship"},
                "description": {"type": "string", "description": "Full job description"},
                "requirements": {"type": "array", "items": {"type": "string"}, "description": "Required skills/qualifications"},
                "nice_to_have": {"type": "array", "items": {"type": "string"}, "description": "Preferred but not required skills"},
                "benefits": {"type": "array", "items": {"type": "string"}},
                "application_url": {"type": "string"},
                "posted_date": {"type": "string", "description": "ISO 8601 date"},
                "url": {"type": "string"},
            },
            "required": ["job_title", "company"],
        },
        system_prompt=(
            "You are a job posting extraction engine. Extract structured job listing data. "
            "Distinguish between required qualifications and nice-to-have. "
            "Infer remote status from phrases like 'remote', 'work from home', 'hybrid'. "
            "Return valid JSON only."
        ),
    )


def _real_estate_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="real_estate",
        description="Extract property listing details",
        schema={
            "type": "object",
            "properties": {
                "property_type": {"type": "string", "description": "house, apartment, condo, townhouse, land"},
                "price": {"type": "string"},
                "currency": {"type": "string"},
                "address": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "zip_code": {"type": "string"},
                "country": {"type": "string"},
                "bedrooms": {"type": "integer"},
                "bathrooms": {"type": "number"},
                "square_feet": {"type": "integer"},
                "lot_size": {"type": "string"},
                "year_built": {"type": "integer"},
                "description": {"type": "string"},
                "features": {"type": "array", "items": {"type": "string"}},
                "images": {"type": "array", "items": {"type": "string"}},
                "listing_agent": {"type": "string"},
                "agent_phone": {"type": "string"},
                "url": {"type": "string"},
                "status": {"type": "string", "description": "for_sale, for_rent, sold, pending"},
            },
            "required": ["price", "address"],
        },
        system_prompt=(
            "You are a real estate listing extraction engine. Extract property details. "
            "Infer property type from descriptions. Parse addresses into components when possible. "
            "Return valid JSON only."
        ),
    )


def _person_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="person",
        description="Extract person/team member information from bio pages",
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "title": {"type": "string", "description": "Job title or role"},
                "organization": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "linkedin": {"type": "string"},
                "twitter": {"type": "string"},
                "bio": {"type": "string", "description": "Biography or about text"},
                "expertise": {"type": "array", "items": {"type": "string"}},
                "education": {"type": "array", "items": {"type": "string"}},
                "image_url": {"type": "string"},
            },
            "required": ["name"],
        },
        system_prompt=(
            "You are a person profile extraction engine. Extract biographical information. "
            "Look for contact details in structured formats. Return valid JSON only."
        ),
    )


def _event_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="event",
        description="Extract event/conference/meetup details",
        schema={
            "type": "object",
            "properties": {
                "event_name": {"type": "string"},
                "organizer": {"type": "string"},
                "start_date": {"type": "string", "description": "ISO 8601 datetime"},
                "end_date": {"type": "string", "description": "ISO 8601 datetime"},
                "location": {"type": "string"},
                "venue": {"type": "string"},
                "description": {"type": "string"},
                "ticket_price": {"type": "string"},
                "registration_url": {"type": "string"},
                "speakers": {"type": "array", "items": {"type": "string"}},
                "topics": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string", "description": "conference, webinar, meetup, workshop, etc."},
                "url": {"type": "string"},
            },
            "required": ["event_name"],
        },
        system_prompt=(
            "You are an event extraction engine. Extract event details. "
            "Parse dates into ISO 8601 format. Distinguish virtual from in-person events. "
            "Return valid JSON only."
        ),
    )


def _review_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="review",
        description="Extract product/service reviews",
        schema={
            "type": "object",
            "properties": {
                "overall_rating": {"type": "number", "description": "Average rating out of 5"},
                "total_reviews": {"type": "integer"},
                "reviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "author": {"type": "string"},
                            "rating": {"type": "number"},
                            "date": {"type": "string"},
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "verified": {"type": "boolean"},
                        },
                    },
                },
                "pros": {"type": "array", "items": {"type": "string"}},
                "cons": {"type": "array", "items": {"type": "string"}},
                "url": {"type": "string"},
            },
            "required": ["overall_rating"],
        },
        system_prompt=(
            "You are a review extraction engine. Extract individual reviews with ratings, "
            "dates, and verified status. Return valid JSON only."
        ),
        merge_strategy="list",
    )


def _faq_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="faq",
        description="Extract FAQ question/answer pairs",
        schema={
            "type": "object",
            "properties": {
                "faq_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "answer": {"type": "string"},
                            "category": {"type": "string"},
                        },
                        "required": ["question", "answer"],
                    },
                },
                "url": {"type": "string"},
            },
            "required": ["faq_items"],
        },
        system_prompt=(
            "You are an FAQ extraction engine. Extract all question-answer pairs. "
            "Preserve the exact question text. Group by category if categories are present. "
            "Return valid JSON only."
        ),
        merge_strategy="merge",
    )


def _recipe_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="recipe",
        description="Extract recipe details from cooking pages",
        schema={
            "type": "object",
            "properties": {
                "recipe_name": {"type": "string"},
                "author": {"type": "string"},
                "description": {"type": "string"},
                "prep_time": {"type": "string"},
                "cook_time": {"type": "string"},
                "total_time": {"type": "string"},
                "servings": {"type": "string"},
                "difficulty": {"type": "string"},
                "ingredients": {"type": "array", "items": {"type": "string"}},
                "instructions": {"type": "array", "items": {"type": "string"}},
                "nutrition": {"type": "object", "description": "Nutritional info as key-value"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "cuisine": {"type": "string"},
                "diet": {"type": "array", "items": {"type": "string"}, "description": "vegan, gluten-free, etc."},
                "image_url": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["recipe_name", "ingredients", "instructions"],
        },
        system_prompt=(
            "You are a recipe extraction engine. Extract complete recipe information. "
            "Preserve ingredient quantities and units. Return valid JSON only."
        ),
    )


def _research_paper_template() -> ExtractTemplate:
    return ExtractTemplate(
        name="research_paper",
        description="Extract academic paper metadata and content",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "authors": {"type": "array", "items": {"type": "string"}},
                "abstract": {"type": "string"},
                "publication_date": {"type": "string", "description": "ISO 8601 date"},
                "journal": {"type": "string"},
                "doi": {"type": "string"},
                "arxiv_id": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "citations_count": {"type": "integer"},
                "pdf_url": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["title"],
        },
        system_prompt=(
            "You are an academic paper extraction engine. Extract paper metadata. "
            "Authors should be listed as full names. Return valid JSON only."
        ),
    )


_TEMPLATES: Dict[str, ExtractTemplate] = {}


def _register_templates():
    """Register all built-in templates."""
    global _TEMPLATES
    _TEMPLATES = {
        "product": _product_template(),
        "article": _article_template(),
        "job_posting": _job_posting_template(),
        "real_estate": _real_estate_template(),
        "person": _person_template(),
        "event": _event_template(),
        "review": _review_template(),
        "faq": _faq_template(),
        "recipe": _recipe_template(),
        "research_paper": _research_paper_template(),
    }


# Register on import
_register_templates()


def list_templates() -> List[str]:
    """Return names of all available templates."""
    return list(_TEMPLATES.keys())


def get_template(name: str) -> ExtractTemplate:
    """Get a template by name. Raises KeyError if not found."""
    if name not in _TEMPLATES:
        available = ", ".join(list_templates())
        raise KeyError(f"Unknown template '{name}'. Available: {available}")
    return _TEMPLATES[name]


def get_all_templates() -> Dict[str, ExtractTemplate]:
    """Return all registered templates."""
    return dict(_TEMPLATES)


def register_template(name: str, template: ExtractTemplate) -> None:
    """Register a custom template at runtime."""
    _TEMPLATES[name] = template
