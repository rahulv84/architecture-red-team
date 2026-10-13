"""File-type detection, signature validation and parser dispatch."""
from __future__ import annotations

import re
from pathlib import Path

from ..core.exceptions import ParseError, UnsupportedFileError
from . import diagram_parser, document_parser, iac_parser, image_parser, pdf_parser
from .types import ParsedDocument, ParserLimits

# extension -> detected type
EXTENSION_TYPES: dict[str, str] = {
    ".pdf": "pdf", ".txt": "text", ".md": "markdown", ".markdown": "markdown", ".rst": "text",
    ".log": "text", ".csv": "text", ".ini": "text", ".conf": "text", ".toml": "text",
    ".docx": "docx", ".pptx": "pptx",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".svg": "svg", ".drawio": "drawio", ".xml": "xml",
    ".mmd": "mermaid", ".mermaid": "mermaid", ".puml": "plantuml", ".plantuml": "plantuml", ".pu": "plantuml",
    ".tf": "terraform", ".tfvars": "terraform",
    ".yaml": "structured", ".yml": "structured", ".json": "structured",
}

SUPPORTED_EXTENSIONS = tuple(sorted(EXTENSION_TYPES))

_SIGNATURES = {
    "pdf": lambda d: d.startswith(b"%PDF-"),
    "docx": lambda d: d.startswith(b"PK"),
    "pptx": lambda d: d.startswith(b"PK"),
    "image": lambda d: (
        d.startswith(b"\x89PNG\r\n\x1a\n") or d.startswith(b"\xff\xd8\xff") or (d[:4] == b"RIFF" and d[8:12] == b"WEBP")
    ),
}

_DOCUMENT_TYPE_DEFAULT_ROLE = {
    "drawio": "DIAGRAM", "mermaid": "DIAGRAM", "plantuml": "DIAGRAM", "svg": "DIAGRAM", "image": "DIAGRAM",
    "terraform": "INFRASTRUCTURE", "cloudformation": "INFRASTRUCTURE", "kubernetes": "INFRASTRUCTURE",
    "compose": "INFRASTRUCTURE",
}


def safe_filename(name: str) -> str:
    """Reduce a user-supplied filename to a safe basename (never used as a path)."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._\- ]+", "_", base).strip(" .")
    return (base or "upload")[:120]


def detect_type(filename: str, data: bytes) -> str:
    """Return the detected type or raise :class:`UnsupportedFileError`."""
    ext = Path(filename).suffix.lower()
    detected = EXTENSION_TYPES.get(ext)
    if detected is None:
        raise UnsupportedFileError(
            f"Unsupported file type '{ext or filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}",
            details={"supported_extensions": list(SUPPORTED_EXTENSIONS)},
        )
    check = _SIGNATURES.get(detected)
    if check and not check(data[:16]):
        raise UnsupportedFileError(f"File content does not match its '{ext}' extension (invalid signature).")
    if detected == "xml":
        head = data[:4000].decode("utf-8", errors="ignore")
        if "mxfile" in head or "mxGraphModel" in head:
            return "drawio"
        raise UnsupportedFileError("XML file is not a recognised Draw.io diagram.")
    if detected == "drawio" and b"mxfile" not in data[:4000] and b"mxGraphModel" not in data[:4000]:
        raise UnsupportedFileError("File does not look like Draw.io XML.")
    return detected


def default_role(detected_type: str) -> str:
    return _DOCUMENT_TYPE_DEFAULT_ROLE.get(detected_type, "SUPPORTING")


def parse_bytes(
    data: bytes,
    filename: str,
    detected_type: str,
    limits: ParserLimits,
    image_dir: Path | None = None,
) -> ParsedDocument:
    """Parse ``data`` according to ``detected_type``.  Raises ``ParseError`` on failure."""
    if len(data) > limits.max_bytes:
        raise ParseError(f"File exceeds the {limits.max_bytes // (1024 * 1024)} MB size limit.")
    if detected_type == "pdf":
        return pdf_parser.parse_pdf(data, filename, limits, image_dir)
    if detected_type == "docx":
        return document_parser.parse_docx(data, filename, limits)
    if detected_type == "pptx":
        return document_parser.parse_pptx(data, filename, limits)
    if detected_type == "image":
        return image_parser.parse_image(data, filename, limits)
    if detected_type == "svg":
        return diagram_parser.parse_svg(data, filename, limits)
    if detected_type == "drawio":
        return diagram_parser.parse_drawio(data, filename, limits)
    if detected_type == "mermaid":
        return diagram_parser.parse_mermaid(data, filename, limits)
    if detected_type == "plantuml":
        return diagram_parser.parse_plantuml(data, filename, limits)
    if detected_type == "terraform":
        return iac_parser.parse_terraform(data, filename, limits)
    if detected_type == "structured":
        return iac_parser.parse_structured(data, filename, limits)
    if detected_type == "markdown":
        return document_parser.parse_text(data, filename, limits, markdown=True)
    if detected_type == "text":
        return document_parser.parse_text(data, filename, limits, markdown=False)
    raise UnsupportedFileError(f"No parser available for type '{detected_type}'.")
