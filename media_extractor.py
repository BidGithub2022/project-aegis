import asyncio
import io
import os
import re
from typing import Any, Optional

import fitz
import pytesseract
from PIL import Image

from document_heuristics import analyze_document_signals

MAX_MEDIA_BYTES = int(os.getenv("MAX_MEDIA_BYTES", str(8 * 1024 * 1024)))
PDF_OCR_MAX_PAGES = int(os.getenv("PDF_OCR_MAX_PAGES", "6"))
OCR_LANG = os.getenv("OCR_LANG", "eng")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_pdf(mimetype: str, data: bytes, filename: str) -> bool:
    if mimetype in ("application/pdf", "application/x-pdf"):
        return True
    if filename.lower().endswith(".pdf"):
        return True
    return len(data) >= 5 and data[:5] == b"%PDF-"


def _is_image(mimetype: str, filename: str) -> bool:
    if mimetype.startswith("image/"):
        return True
    return filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"))


def _ocr_image_bytes(data: bytes) -> str:
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    return _normalize_text(pytesseract.image_to_string(image, lang=OCR_LANG))


def _ocr_pdf_pages(data: bytes, max_pages: int) -> tuple[str, bool]:
    doc = fitz.open(stream=data, filetype="pdf")
    chunks: list[str] = []
    try:
        for page_index in range(min(doc.page_count, max_pages)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            text = _ocr_image_bytes(pix.tobytes("png"))
            if text:
                chunks.append(text)
    finally:
        doc.close()
    combined = _normalize_text(" ".join(chunks))
    return combined, bool(combined)


def extract_pdf_text(data: bytes) -> dict[str, Any]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        text_parts = [_normalize_text(page.get_text()) for page in doc]
        text = _normalize_text(" ".join(p for p in text_parts if p))
        metadata = doc.metadata or {}
        producer = " ".join(
            str(metadata.get(k, "")) for k in ("producer", "creator", "author", "title")
        ).strip()
        page_count = doc.page_count
    finally:
        doc.close()

    used_ocr = False
    ocr_text = ""
    if len(text) < 200 and page_count > 0:
        ocr_text, used_ocr = _ocr_pdf_pages(data, PDF_OCR_MAX_PAGES)

    full_text = _normalize_text(f"{text} {ocr_text}")
    return {
        "text": full_text,
        "page_count": page_count,
        "producer_metadata": producer,
        "used_ocr": used_ocr,
        "kind": "pdf",
    }


def extract_image_text(data: bytes) -> dict[str, Any]:
    text = _ocr_image_bytes(data)
    return {"text": text, "page_count": 0, "producer_metadata": "", "used_ocr": True, "kind": "image"}


def extract_text_from_media(
    data: bytes,
    mimetype: str = "",
    filename: str = "",
) -> dict[str, Any]:
    if len(data) > MAX_MEDIA_BYTES:
        return {"ok": False, "error": "media_too_large", "max_bytes": MAX_MEDIA_BYTES}

    name = (filename or "").strip() or "attachment"
    if _is_pdf(mimetype, data, name):
        extracted = extract_pdf_text(data)
        doc_signals = analyze_document_signals(
            extracted["text"],
            filename=name,
            producer_metadata=extracted["producer_metadata"],
            page_count=extracted["page_count"],
            used_ocr=extracted["used_ocr"],
        )
        return {"ok": True, "filename": name, "mimetype": mimetype or "application/pdf", **extracted, **doc_signals}

    if _is_image(mimetype, name):
        extracted = extract_image_text(data)
        return {"ok": True, "filename": name, "mimetype": mimetype or "image/jpeg", **extracted,
                "document_delta": 0, "document_flags": [], "document_triggers": [], "document_reasons": []}

    return {"ok": False, "error": "unsupported_media_type", "filename": name, "mimetype": mimetype}


async def extract_text_from_media_async(
    data: bytes,
    mimetype: str = "",
    filename: str = "",
) -> dict[str, Any]:
    return await asyncio.to_thread(extract_text_from_media, data, mimetype, filename)
