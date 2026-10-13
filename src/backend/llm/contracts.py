"""Structured-output contracts for non-review LLM tasks."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..models.domain import Severity


class RebuttalOutput(BaseModel):
    """A specialist's response to an architect's dispute."""

    stance: str = Field(description="MAINTAIN | CONCEDE | ADJUST_SEVERITY")
    argument: str = Field(min_length=3, description="Evidence-backed rebuttal text")
    evidence_ids: list[str] = Field(default_factory=list, description="IDs from the supplied evidence catalog")
    revised_severity: Severity | None = None
    open_questions: list[str] = Field(default_factory=list)


class ModeratorAssessment(BaseModel):
    finding_id: str
    verdict: str = Field(description="KEEP | DOWNGRADE | NEEDS_VALIDATION | REJECT")
    severity: Severity | None = None
    reasoning: str = ""


class ModeratorReviewOutput(BaseModel):
    assessments: list[ModeratorAssessment] = Field(default_factory=list)
    conflicting_interpretations: list[str] = Field(default_factory=list)
    unsupported_assumptions: list[str] = Field(default_factory=list)
    summary: str = ""


class ExtractedComponent(BaseModel):
    name: str
    type: str = "other"
    provider: str = "unknown"
    description: str = ""
    properties: dict[str, str] = Field(default_factory=dict)
    evidence_excerpt: str = ""


class ExtractedRelationship(BaseModel):
    source: str
    target: str
    label: str = ""
    synchronous: bool | None = None
    evidence_excerpt: str = ""


class ExtractedNfr(BaseModel):
    category: str = "other"
    statement: str
    value: str | None = None


class ExtractionOutput(BaseModel):
    """LLM-assisted augmentation of the deterministic architecture extraction."""

    title: str | None = None
    purpose: str | None = None
    environment: str | None = None
    components: list[ExtractedComponent] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    nfrs: list[ExtractedNfr] = Field(default_factory=list)
    security_controls: list[str] = Field(default_factory=list)
    operational_tooling: list[str] = Field(default_factory=list)
    deployment_regions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class VisionComponent(BaseModel):
    id: str
    label: str
    type: str = "other"


class VisionConnection(BaseModel):
    source: str
    target: str
    label: str = ""


class VisionDiagramOutput(BaseModel):
    components: list[VisionComponent] = Field(default_factory=list)
    connections: list[VisionConnection] = Field(default_factory=list)
    notes: str = ""
