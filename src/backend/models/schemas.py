"""API request/response contracts and the machine-readable assessment report."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .domain import (
    AgentExecution,
    ArchitectureDecisionRecord,
    ChangeImpact,
    DecisionType,
    Finding,
    HumanDecision,
    Scorecard,
    SourceReference,
    ToolResult,
    ToolRunSummary,
    utcnow,
)


# --------------------------------------------------------------------------- requests
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    cloud_environment: str = "Unknown"


class ReviewCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=160, description="Architecture name")
    description: str = ""
    cloud_environment: str = "Unknown"
    demo_mode: bool = True
    provider: str | None = None
    model_id: str | None = None
    scenario_id: str | None = None
    enabled_agents: list[str] | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=10)


class DecisionCreate(BaseModel):
    finding_id: str
    decision_type: DecisionType
    justification: str = ""
    notes: str = ""
    review_date: str | None = None
    risk_owner: str | None = None
    remediation_target: str | None = None
    actor: str = "architect"


class ResumeRequest(BaseModel):
    action: str = Field(default="submit_decisions", description="submit_decisions | finalize")
    notes: str = ""


class ReReviewRequest(BaseModel):
    revision_id: str | None = None
    notes: str = ""


class ProviderValidateRequest(BaseModel):
    provider: str
    check_connectivity: bool = False


class SettingsUpdate(BaseModel):
    llm_provider: str | None = None
    anthropic_model: str | None = None
    openai_model: str | None = None
    gemini_model: str | None = None
    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model_id: str | None = None
    demo_mode: bool | None = None
    max_concurrent_agents: int | None = None
    max_review_rounds: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    enable_checkov: bool | None = None
    llm_timeout_seconds: int | None = None
    llm_max_retries: int | None = None
    anthropic_api_key: str | None = Field(default=None, description="Held in memory only; never persisted or returned")
    openai_api_key: str | None = Field(default=None, description="Held in memory only; never persisted or returned")
    gemini_api_key: str | None = Field(default=None, description="Held in memory only; never persisted or returned")
    aws_bearer_token_bedrock: str | None = Field(default=None, description="Bedrock API key; held in memory only; never persisted or returned")


# --------------------------------------------------------------------------- responses
class UploadResponse(BaseModel):
    accepted: list[dict[str, Any]]
    rejected: list[dict[str, str]]


class AgentStatusView(BaseModel):
    agent_id: str
    label: str
    status: str
    round: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    finding_count: int = 0
    tool_count: int = 0
    tool_violations: int = 0
    error: str | None = None
    warnings: list[str] = []
    information_gaps: list[str] = []
    skip_reason: str | None = None
    simulated: bool = False
    usage: dict[str, Any] | None = None


class ReviewStatusView(BaseModel):
    review_id: str
    status: str
    phase: str
    current_round: int
    max_rounds: int
    architecture_revision: int
    awaiting_human: bool
    mode: str
    simulated: bool
    provider: str
    model_id: str
    error: str | None
    recovery_note: str | None
    agents: list[AgentStatusView]
    moderator: dict[str, Any]
    finding_counts: dict[str, int]
    pending_decisions: int
    open_critical_high: int
    can_re_review: bool
    can_finalize: bool
    pending_revision_id: str | None = None
    warnings: list[str] = []
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None


class FindingView(BaseModel):
    finding: Finding
    decisions: list[HumanDecision] = []
    rebuttals: list[dict[str, Any]] = []
    tool_results: list[ToolResult] = []
    adrs: list[str] = []


# --------------------------------------------------------------------------- assessment report
class ReportFinding(BaseModel):
    finding_id: str
    domain: str
    title: str
    severity: str
    evidence_level: str
    status: str
    category: str
    description: str
    scenario: str
    impact: str
    recommendation: str
    principle: str
    principle_origin: str
    validation_method: str
    affected_components: list[str]
    source_references: list[SourceReference]
    tool_results: list[str]
    contributing_agents: list[str]
    moderator_status: str
    moderator_notes: str
    assumptions: list[str]
    architect_decisions: list[dict[str, Any]]
    rebuttals: list[dict[str, Any]]
    remaining_risk: str
    simulated: bool
    first_seen_revision: int
    last_seen_revision: int
    history: list[dict[str, Any]]


class AssessmentReport(BaseModel):
    schema_version: str = "1.0"
    generated_at: datetime = Field(default_factory=utcnow)
    review_id: str
    project_name: str
    architecture_name: str
    review_status: str
    architecture_revision: int
    simulated: bool
    provider: str
    model_id: str
    executive_summary: dict[str, Any]
    scorecard: Scorecard
    findings: list[ReportFinding]
    decisions: list[dict[str, Any]]
    adrs: list[dict[str, Any]]
    review_history: dict[str, Any]
    coverage: dict[str, Any]
    architecture: dict[str, Any]
    traceability: list[dict[str, Any]]
    evaluation: dict[str, Any] | None = None
    limitations: list[str]
    disclaimers: list[str]


class AgentExecutionsView(BaseModel):
    executions: list[AgentExecution]
    tool_summaries: list[ToolRunSummary] = []
    tool_results: list[ToolResult] = []


class HistoryView(BaseModel):
    review_id: str
    revisions: list[dict[str, Any]]
    decisions: list[HumanDecision]
    rebuttals: list[dict[str, Any]]
    adrs: list[ArchitectureDecisionRecord]
    change_impacts: list[ChangeImpact]
