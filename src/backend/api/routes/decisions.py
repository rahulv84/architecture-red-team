"""Human decisions, resume, architecture revisions, re-review and history."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, UploadFile

from ...core.exceptions import ValidationFailed
from ...models.domain import HumanDecision
from ...models.schemas import DecisionCreate, HistoryView, ReReviewRequest, ResumeRequest
from ..deps import Container, read_upload

router = APIRouter(tags=["decisions"])


@router.post("/reviews/{review_id}/decisions", status_code=201)
def record_decision(review_id: str, payload: DecisionCreate, c: Container) -> HumanDecision:
    return c.decisions.record(review_id, payload)


@router.get("/reviews/{review_id}/decisions")
def list_decisions(review_id: str, c: Container, pending_only: bool = False) -> list[HumanDecision]:
    c.reviews.get(review_id)
    return c.decisions.pending(review_id) if pending_only else c.decisions.history(review_id)


@router.post("/reviews/{review_id}/resume", status_code=202)
def resume_review(review_id: str, payload: ResumeRequest, c: Container) -> dict[str, Any]:
    """Resume the interrupted LangGraph run with all pending architect decisions."""
    return c.workflow.resume(review_id, payload.action, payload.notes)


@router.post("/reviews/{review_id}/revisions", status_code=201)
async def create_revision(
    review_id: str, c: Container, change_notes: str = Form(default=""), use_demo_revision: bool = Form(default=False),
    files: list[UploadFile] = File(default=[]), roles: list[str] = Form(default=[]),
) -> dict[str, Any]:
    review = c.reviews.get(review_id)
    items: list[tuple[str, bytes, str | None]] = []
    notes = change_notes
    source = "upload"
    if use_demo_revision:
        if not review.scenario_id:
            raise ValidationFailed("This review has no demo scenario with a predefined revision.")
        demo_notes, demo_files = c.demo.revision_files(review.scenario_id)
        items.extend(demo_files)
        notes = notes or demo_notes
        source = "demo"
    limit = c.settings_store.get().max_upload_mb * 1024 * 1024
    for idx, f in enumerate(files or []):
        data = await read_upload(f, limit)
        items.append((f.filename or f"revision-{idx}", data, roles[idx] if idx < len(roles) and roles[idx] else None))
    rev = c.revisions.create_revision(review_id, notes, items, source=source)
    return rev.model_dump(mode="json")


@router.get("/reviews/{review_id}/revisions")
def list_revisions(review_id: str, c: Container) -> list[dict[str, Any]]:
    c.reviews.get(review_id)
    return [r.model_dump(mode="json") for r in c.reviews.revisions(review_id)]


@router.post("/reviews/{review_id}/re-review", status_code=202)
def re_review(review_id: str, payload: ReReviewRequest, c: Container) -> dict[str, Any]:
    return c.workflow.re_review(review_id, payload.revision_id, payload.notes)


@router.get("/reviews/{review_id}/history")
def history(review_id: str, c: Container) -> HistoryView:
    c.reviews.get(review_id)
    revisions = c.reviews.revisions(review_id)
    return HistoryView(
        review_id=review_id, revisions=[r.model_dump(mode="json") for r in revisions],
        decisions=c.decisions.history(review_id),
        rebuttals=[r.model_dump(mode="json") for r in sorted(c.tables.rebuttals.list(review_id=review_id), key=lambda r: r.created_at)],
        adrs=c.decisions.adrs(review_id), change_impacts=[r.impact for r in revisions if r.impact],
    )
