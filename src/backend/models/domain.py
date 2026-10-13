"""Core domain models shared by services, agents, the graph and the API.

Everything here is a Pydantic v2 model so that agent output, graph state payloads,
snapshots and API responses all use one validated vocabulary.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# --------------------------------------------------------------------------- enums
class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"


SEVERITY_ORDER: dict[str, int] = {
    Severity.CRITICAL.value: 4,
    Severity.HIGH.value: 3,
    Severity.MEDIUM.value: 2,
    Severity.LOW.value: 1,
    Severity.INFORMATIONAL.value: 0,
}


def severity_from_rank(rank: int) -> Severity:
    for sev, value in SEVERITY_ORDER.items():
        if value == rank:
            return Severity(sev)
    return Severity.INFORMATIONAL


class EvidenceLevel(StrEnum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    NEEDS_VALIDATION = "NEEDS_VALIDATION"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    DISPUTED = "DISPUTED"
    REBUTTED = "REBUTTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    REMEDIATION_PROPOSED = "REMEDIATION_PROPOSED"
    RESOLVED = "RESOLVED"
    VERIFIED = "VERIFIED"


# Statuses in which a finding no longer represents outstanding risk.
CLOSED_STATUSES = {FindingStatus.REJECTED.value, FindingStatus.RESOLVED.value, FindingStatus.VERIFIED.value}


class ModeratorStatus(StrEnum):
    VALIDATED = "VALIDATED"
    MERGED = "MERGED"
    REQUIRES_VALIDATION = "REQUIRES_VALIDATION"
    REJECTED = "REJECTED"


class ReviewStatus(StrEnum):
    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    EXTRACTING = "EXTRACTING"
    ANALYZING = "ANALYZING"
    MODERATING = "MODERATING"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    REBUTTING = "REBUTTING"
    RE_REVIEWING = "RE_REVIEWING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_OPEN_RISKS = "COMPLETED_WITH_OPEN_RISKS"
    FAILED = "FAILED"


TERMINAL_REVIEW_STATUSES = {
    ReviewStatus.COMPLETED.value,
    ReviewStatus.COMPLETED_WITH_OPEN_RISKS.value,
    ReviewStatus.FAILED.value,
}
RUNNING_REVIEW_STATUSES = {
    ReviewStatus.EXTRACTING.value,
    ReviewStatus.ANALYZING.value,
    ReviewStatus.MODERATING.value,
    ReviewStatus.REBUTTING.value,
    ReviewStatus.RE_REVIEWING.value,
}


class AgentStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AgentId(StrEnum):
    SECURITY = "security"
    RESILIENCE = "resilience"
    FINOPS = "finops"
    COMPLIANCE = "compliance"
    PERFORMANCE = "performance"
    OPERABILITY = "operability"


ALL_AGENT_IDS = [a.value for a in AgentId]

AGENT_LABELS = {
    "security": "Security",
    "resilience": "Resilience",
    "finops": "FinOps",
    "compliance": "Compliance",
    "performance": "Performance",
    "operability": "Operability",
}


class DocumentRole(StrEnum):
    PRIMARY_ARCHITECTURE = "PRIMARY_ARCHITECTURE"
    DIAGRAM = "DIAGRAM"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    STANDARD = "STANDARD"
    ADR = "ADR"
    INCIDENT = "INCIDENT"
    SUPPORTING = "SUPPORTING"


class DecisionType(StrEnum):
    ACCEPT = "ACCEPT"
    DISPUTE = "DISPUTE"
    DEFER = "DEFER"


class PrincipleOrigin(StrEnum):
    ORGANIZATIONAL_STANDARD = "ORGANIZATIONAL_STANDARD"
    CONTROL_MAPPING = "CONTROL_MAPPING"
    GENERAL_PRACTICE = "GENERAL_PRACTICE"


class Provenance(StrEnum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------- shared value objects
class SourceReference(BaseModel):
    """Pointer back into an uploaded artifact (or a tool result)."""

    artifact_id: str | None = None
    filename: str | None = None
    page: int | None = None
    slide: int | None = None
    section: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str | None = None
    node_id: str | None = None
    label: str | None = None
    resource_path: str | None = None
    tool_result_id: str | None = None

    def short(self) -> str:
        parts: list[str] = [self.filename or self.artifact_id or "unknown"]
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.slide is not None:
            parts.append(f"slide {self.slide}")
        if self.section:
            parts.append(f"§{self.section}")
        if self.line_start is not None:
            parts.append(f"L{self.line_start}" + (f"-{self.line_end}" if self.line_end and self.line_end != self.line_start else ""))
        if self.resource_path:
            parts.append(self.resource_path)
        elif self.node_id:
            parts.append(f"node {self.node_id}")
        if self.tool_result_id:
            parts.append(f"tool {self.tool_result_id}")
        return " · ".join(parts)


class UsageRecord(BaseModel):
    provider: str = "unknown"
    model_id: str = "unknown"
    agent_id: str | None = None
    task: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    retry_count: int = 0
    status: str = "OK"
    estimated_cost_usd: float | None = None  # None -> "N/A" (no reliable pricing configured)


# --------------------------------------------------------------------------- projects & artifacts
class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: new_id("prj"))
    name: str
    description: str = ""
    cloud_environment: str = "Unknown"
    scenario_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ArchitectureArtifact(BaseModel):
    """An uploaded source file with its ingestion manifest entry."""

    artifact_id: str = Field(default_factory=lambda: new_id("art"))
    project_id: str
    filename: str
    stored_path: str
    sha256: str
    size_bytes: int
    detected_type: str
    role: DocumentRole = DocumentRole.SUPPORTING
    parse_status: str = "PENDING"  # PENDING | PARSED | PARTIAL | FAILED | UNSUPPORTED
    parse_warnings: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    page_count: int | None = None
    requires_vision: bool = False
    active: bool = True
    superseded_by: str | None = None
    revision_added: int = 1
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- tool results
class ToolResult(BaseModel):
    """Normalised output of a deterministic check or external scanner."""

    tool_result_id: str
    tool_id: str
    tool_name: str
    source_artifact_id: str | None = None
    rule_id: str
    execution_status: str = "EXECUTED"  # EXECUTED | NOT_EXECUTED | ERROR
    outcome: str = "VIOLATION"  # VIOLATION | PASS | INFO
    domain: str = "security"
    title: str = ""
    message: str = ""
    observed_value: str | None = None
    expected_value: str | None = None
    evidence_level: EvidenceLevel = EvidenceLevel.CONFIRMED
    severity_hint: Severity = Severity.MEDIUM
    affected_component_ids: list[str] = Field(default_factory=list)
    evidence_reference: SourceReference | None = None
    raw_result_reference: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)


class ToolRunSummary(BaseModel):
    tool_id: str
    tool_name: str
    status: str  # EXECUTED | NOT_EXECUTED | ERROR
    detail: str = ""
    resources_checked: int = 0
    violations: int = 0


# --------------------------------------------------------------------------- findings
class FindingDraft(BaseModel):
    """Structured finding as proposed by a specialist (LLM output contract)."""

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3)
    category: str = Field(default="General", max_length=120)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0, default=0.6)
    evidence_level: EvidenceLevel
    affected_components: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list, description="IDs from the supplied evidence catalog")
    scenario: str = Field(default="", description="Attack or failure scenario")
    impact: str = ""
    recommendation: str = Field(min_length=3)
    principle: str = Field(default="", description="Applicable principle, standard or control")
    principle_origin: PrincipleOrigin = PrincipleOrigin.GENERAL_PRACTICE
    validation_method: str = ""
    assumptions: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""


class AgentReviewOutput(BaseModel):
    """Complete structured result of one specialist review call."""

    findings: list[FindingDraft] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    coverage_summary: str = ""


class FindingEvent(BaseModel):
    """One entry in a finding's audit trail."""

    timestamp: datetime = Field(default_factory=utcnow)
    event: str
    actor: str
    detail: str = ""
    round: int | None = None
    revision: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    finding_id: str = Field(default_factory=lambda: new_id("fnd"))
    review_id: str
    agent_id: str
    title: str
    description: str
    category: str
    severity: Severity
    confidence: float = 0.6
    evidence_level: EvidenceLevel
    affected_components: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    scenario: str = ""
    impact: str = ""
    recommendation: str = ""
    principle: str = ""
    principle_origin: PrincipleOrigin = PrincipleOrigin.GENERAL_PRACTICE
    validation_method: str = ""
    original_status: str = "PROPOSED"
    assumptions: list[str] = Field(default_factory=list)
    tool_result_refs: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    reasoning_by_agent: dict[str, str] = Field(default_factory=dict)

    # Lifecycle / moderation
    status: FindingStatus = FindingStatus.OPEN
    moderator_status: ModeratorStatus = ModeratorStatus.VALIDATED
    moderator_notes: str = ""
    contributing_agents: list[str] = Field(default_factory=list)
    merged_from: list[str] = Field(default_factory=list)
    original_severities: dict[str, str] = Field(default_factory=dict)
    fingerprints: list[str] = Field(default_factory=list)
    first_seen_revision: int = 1
    last_seen_revision: int = 1
    first_seen_round: int = 1
    simulated: bool = False
    dispute_count: int = 0
    history: list[FindingEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def domain(self) -> str:
        return self.agent_id

    def add_history(self, event: str, actor: str, detail: str = "", **kwargs: Any) -> None:
        self.history.append(FindingEvent(event=event, actor=actor, detail=detail, **kwargs))
        self.updated_at = utcnow()


# --------------------------------------------------------------------------- reviews & execution
class Review(BaseModel):
    review_id: str = Field(default_factory=lambda: new_id("rev"))
    project_id: str
    name: str
    description: str = ""
    cloud_environment: str = "Unknown"
    mode: str = "demo"  # demo | live
    provider: str = "mock"
    model_id: str = "mock-deterministic"
    scenario_id: str | None = None
    enabled_agents: list[str] = Field(default_factory=lambda: list(ALL_AGENT_IDS))
    status: ReviewStatus = ReviewStatus.CREATED
    phase: str = "Created"
    current_round: int = 0
    max_rounds: int = 3
    architecture_revision: int = 1
    thread_id: str = ""
    error: str | None = None
    recovery_note: str | None = None
    awaiting_human: bool = False
    cancel_requested: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def simulated(self) -> bool:
        return self.mode == "demo"


class AgentExecution(BaseModel):
    execution_id: str = Field(default_factory=lambda: new_id("exe"))
    review_id: str
    agent_id: str
    round: int = 1
    revision: int = 1
    status: AgentStatus = AgentStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    finding_count: int = 0
    tool_count: int = 0
    tool_violations: int = 0
    error: str | None = None
    information_gaps: list[str] = Field(default_factory=list)
    coverage_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    usage: UsageRecord | None = None
    simulated: bool = False
    skip_reason: str | None = None


class ExecutionEvent(BaseModel):
    seq: int = 0
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    review_id: str
    event_type: str
    message: str = ""
    level: str = "INFO"
    agent_id: str | None = None
    execution_id: str | None = None
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- human decisions
class HumanDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: new_id("dec"))
    review_id: str
    finding_id: str
    decision_type: DecisionType
    justification: str = ""
    notes: str = ""
    review_date: str | None = None
    risk_owner: str | None = None
    remediation_target: str | None = None
    actor: str = "architect"
    round: int = 1
    revision: int = 1
    submitted: bool = False  # delivered to the graph through a resume
    created_at: datetime = Field(default_factory=utcnow)


class ExchangeEntry(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    role: str  # ARCHITECT | CRITIC | MODERATOR
    actor: str
    content: str
    stance: str | None = None
    evidence_refs: list[SourceReference] = Field(default_factory=list)


class Rebuttal(BaseModel):
    rebuttal_id: str = Field(default_factory=lambda: new_id("reb"))
    review_id: str
    finding_id: str
    dispute_decision_id: str
    agent_id: str
    round: int = 1
    stance: str = "MAINTAIN"  # MAINTAIN | CONCEDE | ADJUST_SEVERITY
    argument: str = ""
    revised_severity: Severity | None = None
    evidence_refs: list[SourceReference] = Field(default_factory=list)
    moderator_determination: str = ""
    outcome: str = "PENDING"  # UPHELD_FINDING | DISPUTE_UPHELD | SEVERITY_ADJUSTED | NEEDS_VALIDATION
    exchange: list[ExchangeEntry] = Field(default_factory=list)
    simulated: bool = False
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- ADRs, reports, revisions
class ArchitectureDecisionRecord(BaseModel):
    adr_id: str = Field(default_factory=lambda: new_id("adr"))
    review_id: str
    number: int = 1
    title: str
    status: str = "PROPOSED"
    context: str = ""
    options: list[str] = Field(default_factory=list)
    decision: str = ""
    rationale: str = ""
    consequences: str = ""
    risks: str = ""
    owner: str = "Unassigned"
    related_finding_ids: list[str] = Field(default_factory=list)
    source_decision_id: str | None = None
    path: str | None = None
    markdown: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class ReportArtifact(BaseModel):
    report_id: str = Field(default_factory=lambda: new_id("rpt"))
    review_id: str
    format: str  # pdf | json
    path: str
    filename: str
    size_bytes: int = 0
    revision: int = 1
    created_at: datetime = Field(default_factory=utcnow)


class ChangeImpact(BaseModel):
    revision: int = 1
    changed_artifacts: list[str] = Field(default_factory=list)
    added_artifacts: list[str] = Field(default_factory=list)
    removed_artifacts: list[str] = Field(default_factory=list)
    unchanged_artifacts: list[str] = Field(default_factory=list)
    changed_components: list[str] = Field(default_factory=list)
    added_components: list[str] = Field(default_factory=list)
    removed_components: list[str] = Field(default_factory=list)
    changed_properties: list[str] = Field(default_factory=list)
    impacted_components: list[str] = Field(default_factory=list)
    affected_agents: list[str] = Field(default_factory=list)
    confidence: str = "HIGH"  # HIGH | LOW
    broadened: bool = False
    reasons: dict[str, list[str]] = Field(default_factory=dict)
    summary: str = ""


class ArchitectureRevision(BaseModel):
    revision_id: str = Field(default_factory=lambda: new_id("rvn"))
    review_id: str
    number: int
    artifact_ids: list[str] = Field(default_factory=list)
    change_notes: str = ""
    source: str = "initial"  # initial | upload | demo
    impact: ChangeImpact | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ScoreContribution(BaseModel):
    finding_id: str
    title: str
    severity: str
    evidence_level: str
    status: str
    penalty: float
    counted_as: str  # confirmed | unverified


class DomainScore(BaseModel):
    domain: str
    label: str
    score: int  # rounded to nearest 5 - indicative only
    confirmed_only_score: int
    band: str
    coverage: str  # FULL | PARTIAL | NONE
    open_findings: int
    confirmed_findings: int
    unverified_findings: int
    information_gaps: list[str] = Field(default_factory=list)
    contributions: list[ScoreContribution] = Field(default_factory=list)
    explanation: str = ""


class Scorecard(BaseModel):
    review_id: str
    revision: int
    domains: list[DomainScore]
    overall_band: str
    overall_statement: str
    methodology: str
    generated_at: datetime = Field(default_factory=utcnow)
