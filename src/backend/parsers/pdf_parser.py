"""PDF parser (text layer + embedded-image extraction for vision analysis)."""
from __future__ import annotations

import io
from pathlib import Path

from ..core.exceptions import ParseError
from .types import DocSection, ParsedDocument, ParserLimits

MIN_TEXT_CHARS_PER_PAGE = 25
MAX_EXTRACTED_IMAGES = 12


def parse_pdf(data: bytes, filename: str, limits: ParserLimits, image_dir: Path | None = None) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError, OSError) as exc:
        raise ParseError(f"Could not read PDF: {exc}") from exc
    if reader.is_encrypted:
        raise ParseError("PDF is encrypted/password protected and cannot be parsed.")
    total_pages = len(reader.pages)
    if total_pages > limits.max_pdf_pages:
        raise ParseError(f"PDF has {total_pages} pages; the configured limit is {limits.max_pdf_pages}.")

    sections: list[DocSection] = []
    image_pages: list[int] = []
    warnings: list[str] = []
    image_paths: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:  # pypdf can raise many types on malformed content streams
            warnings.append(f"Page {number}: text extraction failed ({exc.__class__.__name__}).")
            text = ""
        if len(text) < MIN_TEXT_CHARS_PER_PAGE:
            image_pages.append(number)
            if image_dir is not None and len(image_paths) < MAX_EXTRACTED_IMAGES:
                try:
                    for idx, image in enumerate(page.images):
                        if len(image_paths) >= MAX_EXTRACTED_IMAGES:
                            break
                        image_dir.mkdir(parents=True, exist_ok=True)
                        ext = Path(image.name).suffix.lower() or ".png"
                        target = image_dir / f"page{number}_{idx}{ext}"
                        target.write_bytes(image.data)
                        image_paths.append(str(target))
                except Exception as exc:
                    warnings.append(f"Page {number}: embedded image extraction failed ({exc.__class__.__name__}).")
        sections.append(DocSection(heading=f"Page {number}", text=text, page=number))

    if image_pages:
        warnings.append(
            f"{len(image_pages)} page(s) have little or no extractable text (scanned or diagram pages: "
            f"{image_pages[:10]}). OCR is not built in; a vision-capable model is needed to read them."
        )
    text = "\n\n".join(f"[Page {s.page}]\n{s.text}" for s in sections if s.text)
    if not text.strip() and not image_paths:
        warnings.append("No text layer found in this PDF.")
    return ParsedDocument(
        filename=filename, kind="pdf", text=text, sections=sections, page_count=total_pages,
        warnings=warnings, requires_vision=bool(image_pages), image_paths=image_paths,
        metadata={"image_pages": image_pages},
    )
