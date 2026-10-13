"""Findings retrieval with filters and per-finding detail."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...models.domain import Finding
from ...models.schemas import FindingView
from ..deps import Container

router = APIRouter(tags=["findings"])


@router.get("/reviews/{review_id}/findings")
def list_findings(
    review_id: str, c: Container, severity: str | None = None, agent: str | None = None, status: str | None = None,
    evidence_level: str | None = None, component: str | None = None, revision: int | None = None, moderator_status: str | None = None,
    include_rejected: bool = True, q: str | None = None,
) -> list[Finding]:
    c.reviews.get(review_id)
    return c.reviews.findings_repo.filter(
        review_id, severity=severity, agent=agent, status=status, evidence_level=evidence_level, component=component,
        revision=revision, moderator_status=moderator_status, include_rejected=include_rejected, text=q,
    )


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str, c: Container) -> FindingView:
    f = c.reviews.get_finding(finding_id)
    decisions = [d for d in c.decisions.history(f.review_id) if d.finding_id == finding_id]
    rebuttals = [r.model_dump(mode="json") for r in c.tables.rebuttals.list(finding_id=finding_id)]
    tools = {t.tool_result_id: t for t in c.reviews.tool_results(f.review_id)}
    adrs = [a.adr_id for a in c.decisions.adrs(f.review_id) if finding_id in a.related_finding_ids]
    return FindingView(finding=f, decisions=decisions, rebuttals=rebuttals,
                       tool_results=[tools[t] for t in f.tool_result_refs if t in tools], adrs=adrs)


@router.get("/reviews/{review_id}/tool-results")
def tool_results(review_id: str, c: Container) -> list[dict[str, Any]]:
    review = c.reviews.get(review_id)
    return [t.model_dump(mode="json") for t in (c.reviews.tool_results(review_id, review.architecture_revision) or c.reviews.tool_results(review_id))]
