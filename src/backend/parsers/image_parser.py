"""Raster image parser.

Raster diagrams contain no machine-readable structure.  This parser only validates the
image and records metadata; component extraction needs a vision-capable provider and is
performed (when available) by the architecture service, never faked here.
"""
from __future__ import annotations

import io

from ..core.exceptions import ParseError
from .types import ParsedDocument, ParserLimits

MAX_PIXELS = 50_000_000


def parse_image(data: bytes, filename: str, limits: ParserLimits) -> ParsedDocument:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = (img.format or "").lower()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ParseError(f"Could not read image: {exc}") from exc
    if width * height > MAX_PIXELS:
        raise ParseError("Image dimensions are too large to process safely.")
    return ParsedDocument(
        filename=filename,
        kind="image",
        text="",
        requires_vision=True,
        warnings=[
            "Raster image: no structure was extracted. A vision-capable model is required to identify "
            "components; otherwise the image is preserved for manual inspection only."
        ],
        metadata={"width": width, "height": height, "format": fmt},
    )
