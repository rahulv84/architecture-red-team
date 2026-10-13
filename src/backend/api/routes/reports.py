"""Reports and ADRs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ...core.exceptions import NotFoundError, ValidationFailed
from ...models.domain import ReportArtifact
from ..deps import Container

router = APIRouter(tags=["reports"])


@router.post("/reviews/{review_id}/reports", status_code=201)
def generate_reports(review_id: str, c: Container, formats: str = "json,pdf") -> list[ReportArtifact]:
    wanted = tuple(dict.fromkeys(f.strip().lower() for f in formats.split(",") if f.strip()))
    unknown = [f for f in wanted if f not in ("json", "pdf")]
    if unknown or not wanted:
        raise ValidationFailed(f"Unsupported report format(s): {unknown or formats!r}. Use json and/or pdf.")
    return c.reports.generate(review_id, wanted)


@router.get("/reviews/{review_id}/reports")
def list_reports(review_id: str, c: Container) -> list[ReportArtifact]:
    c.reviews.get(review_id)
    return c.reports.list(review_id)


@router.get("/reviews/{review_id}/report-preview")
def report_preview(review_id: str, c: Container) -> dict[str, Any]:
    """Structured report without writing files (used by the Reports page)."""
    return c.reports.build(review_id).model_dump(mode="json")


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, c: Container) -> FileResponse:
    art = c.reports.get(report_id)
    path = Path(art.path)
    if not path.exists():
        raise NotFoundError("Report file is missing from disk.")
    media = "application/pdf" if art.format == "pdf" else "application/json"
    return FileResponse(path, media_type=media, filename=art.filename)


@router.get("/reviews/{review_id}/adrs")
def list_adrs(review_id: str, c: Container) -> list[dict[str, Any]]:
    c.reviews.get(review_id)
    return [a.model_dump(mode="json") for a in c.decisions.adrs(review_id)]
