"""Huginn PDF Extraction — text and OCR extraction from PDF files.

Uses PyMuPDF (fitz) for text extraction with pytesseract OCR as fallback
for scanned/image-based PDFs.

Firecrawl has had this feature request open since Oct 2024 with no progress.
We shipped it in one session.
"""

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def extract_pdf_text(
    pdf_bytes: bytes,
    use_ocr_fallback: bool = True,
    ocr_lang: str = "eng",
) -> str:
    """Extract text from a PDF file.

    First tries PyMuPDF (fitz) for native text extraction.
    Falls back to pytesseract OCR for image-based/scanned PDFs.

    Args:
        pdf_bytes: Raw PDF file bytes.
        use_ocr_fallback: If True, try OCR when PyMuPDF finds no text.
        ocr_lang: Language hint for Tesseract OCR (default: English).

    Returns:
        Extracted text as a string, or empty string if extraction fails.
    """
    text = _extract_with_pymupdf(pdf_bytes)
    if text and len(text.strip()) > 50:
        return text

    if use_ocr_fallback:
        ocr_text = _extract_with_ocr(pdf_bytes, lang=ocr_lang)
        if ocr_text:
            logger.info(f"PDF OCR extracted {len(ocr_text)} chars")
            return ocr_text

    return text


def _extract_with_pymupdf(pdf_bytes: bytes) -> str:
    """Extract text using PyMuPDF (fitz). Returns empty string on failure."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF (fitz) not installed, PDF text extraction unavailable")
        return ""

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page_num, page in enumerate(doc):
            text = page.get_text()
            if text:
                pages.append(text)
            else:
                # Empty page — might be an image page, note it
                pages.append(f"[Page {page_num + 1}: no extractable text]")
        doc.close()
        return "\n\n".join(pages)
    except Exception as e:
        logger.warning(f"PyMuPDF extraction failed: {e}")
        return ""


def _extract_with_ocr(pdf_bytes: bytes, lang: str = "eng") -> str:
    """Extract text from PDF using Tesseract OCR on rendered page images."""
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        logger.warning("pytesseract or pdf2image not installed, OCR unavailable")
        return ""

    try:
        # Convert PDF pages to images
        images = convert_from_bytes(pdf_bytes, dpi=200)
        if not images:
            return ""

        pages = []
        for i, image in enumerate(images):
            text = pytesseract.image_to_string(image, lang=lang)
            if text:
                pages.append(f"[Page {i + 1}]\n{text}")
            else:
                pages.append(f"[Page {i + 1}: no text detected]")

        return "\n\n".join(pages)
    except Exception as e:
        logger.warning(f"OCR extraction failed: {e}")
        return ""


def is_pdf_content(content_type: str, url: str) -> bool:
    """Check if content appears to be a PDF based on content-type or URL."""
    if content_type:
        ct = content_type.lower()
        if "pdf" in ct or "application/octet-stream" in ct:
            return True
    if url.lower().endswith(".pdf"):
        return True
    return False
