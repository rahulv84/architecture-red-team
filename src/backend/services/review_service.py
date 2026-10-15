"""Review lifecycle persistence: reviews, events, agent executions, findings and status views."""
from __future__ import annotations

import threading
from typing import Any

from ..core.config import SettingsStore
from ..core.exceptions import ConflictError, NotFoundError, ValidationFailed
from ..models.architecture import ArchitectureModel
from ..models.database import Tables
from ..models.domain import (
    AGENT_LABELS,
    ALL_AGENT_IDS,
    CLOSED_STATUSES,
    TERMINAL_REVIEW_STATUSES,
    AgentExecution,
    AgentStatus,
    ArchitectureRevision,
    ExecutionEvent,
    Finding,
    Review,
    ReviewStatus,
    ToolResult,
    utcnow,
)
from ..models.schemas import AgentStatusView, ReviewCreate, ReviewStatusView
from ..repositories.findings_repository import FindingsRepository
from ..repositories.project_repository import ProjectRepository
from ..repositories.review_repository import ReviewRepository
from .ingestion_service import IngestionService


class ReviewService:
    def __init__(self, tables: Tables, settings_store: SettingsStore, ingestion: IngestionService) -> None:
        self.tables = tables
        self.repo = ReviewRepository(tables)
        self.findings_repo = FindingsRepository(tables)
        self.projects_repo = ProjectRepository(tables)
        self.settings_store = settings_store
        self.ingestion = ingestion
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reviews
    def create_review(self, payload: ReviewCreate) -> Review:
        project = self.projects_repo.require(payload.project_id)
        settings = self.settings_store.get()
        agents = payload.enabled_agents or list(ALL_AGENT_IDS)
        bad = [a for a in agents if a not in ALL_AGENT_IDS]
        if bad:
            raise ValidationFailed(f"Unknown agent id(s): {bad}", details={"valid": ALL_AGENT_IDS})
        if not agents:
            raise ValidationFailed("At least one specialist agent must be enabled.")
        demo = payload.demo_mode
        provider = "mock" if demo else (payload.provider or settings.llm_provider)
        if not demo and provider == "mock":
            raise ValidationFailed("Live mode requires a real provider (anthropic, openai, gemini or bedrock); 'mock' is demo-only.")
        model_id = "mock-deterministic"
        if not demo:
            model_id = payload.model_id or {
                "anthropic": settings.anthropic_model, "openai": settings.openai_model, "gemini": settings.gemini_model, "bedrock": settings.bedrock_model_id,
            }.get(provider, "unknown")
        arts = self.ingestion.list_for_project(payload.project_id)
        usable = [a for a in arts if a.parse_status in ("PARSED", "PARTIAL")]
        review = Review(
            project_id=payload.project_id, name=payload.name, description=payload.description,
            cloud_environment=payload.cloud_environment, mode="demo" if demo else "live", provider=provider, model_id=model_id,
            scenario_id=payload.scenario_id or project.scenario_id, enabled_agents=agents,
            max_rounds=payload.max_rounds or settings.max_review_rounds,
        )
        review.thread_id = f"thread-{review.review_id}"
        self.tables.reviews.put(review)
        self.tables.revisions.put(ArchitectureRevision(
            review_id=review.review_id, number=1, artifact_ids=[a.artifact_id for a in usable], source="initial",
            change_notes="Initial architecture submission.",
        ))
        self.emit(review.review_id, "review_created", f"Review '{review.name}' created ({'SIMULATED DEMO' if demo else 'live'} mode, provider {provider}).")
        return review

    def get(self, review_id: str) -> Review:
        return self.repo.require(review_id)

    def list(self, project_id: str | None = None) -> list[Review]:
        return self.repo.list(project_id)

    def save(self, review: Review) -> Review:
        return self.repo.save(review)

    def set_status(
        self, review_id: str, status: ReviewStatus, phase: str | None = None, *, error: str | None = None,
        round_no: int | None = None, awaiting_human: bool | None = None, revision: int | None = None,
    ) -> Review:
        review = self.get(review_id)
        review.status = status
        if phase:
            review.phase = phase
        if error is not None:
            review.error = error
        if round_no is not None:
            review.current_round = round_no
        if awaiting_human is not None:
            review.awaiting_human = awaiting_human
        if revision is not None:
            review.architecture_revision = revision
        if status.value in TERMINAL_REVIEW_STATUSES:
            review.completed_at = utcnow()
            review.awaiting_human = False
        if status == ReviewStatus.EXTRACTING and review.started_at is None:
            review.started_at = utcnow()
        return self.save(review)

    # ------------------------------------------------------------------ events
    def emit(self, review_id: str, event_type: str, message: str, *, level: str = "INFO", agent_id: str | None = None,
             execution_id: str | None = None, round_no: int | None = None, payload: dict[str, Any] | None = None) -> ExecutionEvent:
        return self.tables.add_event(ExecutionEvent(
            review_id=review_id, event_type=event_type, message=message, level=level, agent_id=agent_id,
            execution_id=execution_id, round=round_no, payload=payload or {},
        ))

    def events(self, review_id: str, after: int = 0, limit: int = 1000) -> list[ExecutionEvent]:
        self.get(review_id)
        return self.repo.events(review_id, after=after, limit=limit)

    def request_cancel(self, review_id: str) -> None:
        with self._lock:
            self._cancel.add(review_id)

    def clear_cancel(self, review_id: str) -> None:
        with self._lock:
            self._cancel.discard(review_id)

    def is_cancelled(self, review_id: str) -> bool:
        with self._lock:
            return review_id in self._cancel

    # ------------------------------------------------------------------ agent executions
    def begin_agent(self, review_id: str, agent_id: str, round_no: int, revision: int, simulated: bool) -> AgentExecution:
        # reuse an existing execution row for the same (round, agent) so retries do not duplicate
        for ex in self.tables.executions.list(review_id=review_id, agent_id=agent_id, round=round_no):
            ex.status, ex.started_at, ex.completed_at, ex.error = AgentStatus.RUNNING, utcnow(), None, None
            ex.simulated = simulated
            return self.tables.executions.put(ex)
        ex = AgentExecution(review_id=review_id, agent_id=agent_id, round=round_no, revision=revision, status=AgentStatus.RUNNING,
                            started_at=utcnow(), simulated=simulated)
        return self.tables.executions.put(ex)

    def update_execution(self, execution_id: str, **fields: Any) -> AgentExecution:
        ex = self.tables.executions.get(execution_id)
        if ex is None:
            raise NotFoundError(f"Execution '{execution_id}' not found.")
        for k, v in fields.items():
            setattr(ex, k, v)
        return self.tables.executions.put(ex)

    def mark_skipped(self, review_id: str, agent_id: str, round_no: int, revision: int, reason: str, simulated: bool = False) -> AgentExecution:
        for ex in self.tables.executions.list(review_id=review_id, agent_id=agent_id, round=round_no):
            ex.status, ex.skip_reason = AgentStatus.SKIPPED, reason
            return self.tables.executions.put(ex)
        ex = AgentExecution(review_id=review_id, agent_id=agent_id, round=round_no, revision=revision, status=AgentStatus.SKIPPED,
                            skip_reason=reason, simulated=simulated)
        return self.tables.executions.put(ex)

    def executions(self, review_id: str, round_no: int | None = None) -> list[AgentExecution]:
        return self.repo.executions(review_id, round_no)

    def findings(self, review_id: str) -> list[Finding]:
        return self.findings_repo.list(review_id)

    def get_finding(self, finding_id: str) -> Finding:
        return self.findings_repo.require(finding_id)

    def replace_findings(self, review_id: str, findings: list[Finding]) -> None:
        self.findings_repo.replace_all(review_id, findings)

    def save_tool_results(self, review_id: str, revision: int, results: list[ToolResult]) -> None:
        self.findings_repo.save_tool_results(review_id, revision, results)

    def tool_results(self, review_id: str, revision: int | None = None) -> list[ToolResult]:
        return self.findings_repo.tool_results(review_id, revision)

    def save_model(self, review_id: str, model: ArchitectureModel) -> None:
        self.repo.save_model(review_id, model)

    def get_model(self, review_id: str, revision: int | None = None) -> ArchitectureModel | None:
        return self.repo.get_model(review_id, revision)

    def revisions(self, review_id: str) -> list[ArchitectureRevision]:
        return self.repo.revisions(review_id)

    def status_view(self, review_id: str) -> ReviewStatusView:
        review = self.get(review_id)
        execs = self.executions(review_id)
        findings = self.findings(review_id)
        decisions = self.tables.decisions.list(review_id=review_id)
        agents: list[AgentStatusView] = []
        for agent_id in ALL_AGENT_IDS:
            mine = [e for e in execs if e.agent_id == agent_id]
            current = [e for e in mine if e.round == review.current_round]
            ex = current[-1] if current else (mine[-1] if mine else None)
            if ex is None:
                enabled = agent_id in review.enabled_agents
                agents.append(AgentStatusView(
                    agent_id=agent_id, label=AGENT_LABELS[agent_id],
                    status=(AgentStatus.PENDING if enabled else AgentStatus.SKIPPED).value,
                    skip_reason=None if enabled else "Not selected for this review",
                ))
                continue
            agents.append(AgentStatusView(
                agent_id=agent_id, label=AGENT_LABELS[agent_id], status=ex.status.value, round=ex.round, started_at=ex.started_at,
                completed_at=ex.completed_at, finding_count=ex.finding_count, tool_count=ex.tool_count,
                tool_violations=ex.tool_violations, error=ex.error, warnings=ex.warnings, information_gaps=ex.information_gaps,
                skip_reason=ex.skip_reason, simulated=ex.simulated, usage=ex.usage.model_dump() if ex.usage else None,
            ))
        events = self.tables.list_events(review_id, limit=5000)
        mod_events = [e for e in events if e.event_type.startswith("moderator")]
        if review.status == ReviewStatus.MODERATING:
            mod_state = "RUNNING"
        elif mod_events:
            mod_state = "COMPLETED"
        else:
            mod_state = "PENDING"
        counts: dict[str, int] = {"total": len(findings)}
        for f in findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
            counts[f"status:{f.status.value}"] = counts.get(f"status:{f.status.value}", 0) + 1
        open_ch = sum(
            1 for f in findings
            if f.severity.value in ("CRITICAL", "HIGH") and f.status.value not in CLOSED_STATUSES and f.moderator_status.value != "REJECTED"
        )
        revisions = self.revisions(review_id)
        pending_rev = next((r for r in reversed(revisions) if r.number > review.architecture_revision), None)
        awaiting = review.status == ReviewStatus.AWAITING_HUMAN
        return ReviewStatusView(
            review_id=review.review_id, status=review.status.value, phase=review.phase, current_round=review.current_round,
            max_rounds=review.max_rounds, architecture_revision=review.architecture_revision, awaiting_human=awaiting,
            mode=review.mode, simulated=review.simulated, provider=review.provider, model_id=review.model_id, error=review.error,
            recovery_note=review.recovery_note, agents=agents,
            moderator={"status": mod_state, "summary": mod_events[-1].message if mod_events else ""},
            finding_counts=counts, pending_decisions=sum(1 for d in decisions if not d.submitted), open_critical_high=open_ch,
            can_re_review=awaiting and review.current_round < review.max_rounds and pending_rev is not None,
            can_finalize=awaiting, pending_revision_id=pending_rev.revision_id if pending_rev else None,
            warnings=[w for e in events if e.level == "WARNING" for w in [e.message]][-8:],
            started_at=review.started_at, completed_at=review.completed_at, updated_at=review.updated_at,
        )

    def ensure_startable(self, review_id: str) -> Review:
        review = self.get(review_id)
        if review.status not in (ReviewStatus.CREATED, ReviewStatus.FAILED):
            raise ConflictError(f"Review is {review.status.value}; only CREATED or FAILED reviews can be started.")
        return review
