"""Review lifecycle persistence queries: reviews, revisions, agent executions, models and events."""
from __future__ import annotations

from ..core.exceptions import NotFoundError
from ..models.architecture import ArchitectureModel
from ..models.database import Tables, _StoredModel
from ..models.domain import ALL_AGENT_IDS, AgentExecution, ArchitectureRevision, ExecutionEvent, Review, utcnow


class ReviewRepository:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables

    # ---- reviews ------------------------------------------------------------
    def add(self, review: Review) -> Review:
        return self.tables.reviews.put(review)

    def save(self, review: Review) -> Review:
        review.updated_at = utcnow()
        return self.tables.reviews.put(review)

    def get(self, review_id: str) -> Review | None:
        return self.tables.reviews.get(review_id)

    def require(self, review_id: str) -> Review:
        review = self.get(review_id)
        if review is None:
            raise NotFoundError(f"Review '{review_id}' not found.")
        return review

    def list(self, project_id: str | None = None) -> list[Review]:
        reviews = self.tables.reviews.list(project_id=project_id) if project_id else self.tables.reviews.list()
        return sorted(reviews, key=lambda r: r.created_at, reverse=True)

    # ---- revisions ------------------------------------------------------------
    def add_revision(self, revision: ArchitectureRevision) -> ArchitectureRevision:
        return self.tables.revisions.put(revision)

    def revision(self, revision_id: str) -> ArchitectureRevision | None:
        return self.tables.revisions.get(revision_id)

    def revisions(self, review_id: str) -> list[ArchitectureRevision]:
        return sorted(self.tables.revisions.list(review_id=review_id), key=lambda r: r.number)

    # ---- architecture models ------------------------------------------------------
    def save_model(self, review_id: str, model: ArchitectureModel) -> None:
        self.tables.models.put(_StoredModel(key=f"{review_id}:{model.revision}", review_id=review_id, revision=model.revision, model=model))

    def get_model(self, review_id: str, revision: int | None = None) -> ArchitectureModel | None:
        rows = self.tables.models.list(review_id=review_id)
        if not rows:
            return None
        if revision is None:
            return max(rows, key=lambda r: r.revision).model
        return next((r.model for r in rows if r.revision == revision), None)

    # ---- agent executions ----------------------------------------------------------
    def executions(self, review_id: str, round_no: int | None = None) -> list[AgentExecution]:
        items = self.tables.executions.list(review_id=review_id)
        if round_no is not None:
            items = [e for e in items if e.round == round_no]
        return sorted(items, key=lambda e: (e.round, ALL_AGENT_IDS.index(e.agent_id) if e.agent_id in ALL_AGENT_IDS else 99))

    def executions_for(self, review_id: str, agent_id: str, round_no: int) -> list[AgentExecution]:
        return self.tables.executions.list(review_id=review_id, agent_id=agent_id, round=round_no)

    def get_execution(self, execution_id: str) -> AgentExecution | None:
        return self.tables.executions.get(execution_id)

    def save_execution(self, execution: AgentExecution) -> AgentExecution:
        return self.tables.executions.put(execution)

    # ---- events -----------------------------------------------------------------------
    def add_event(self, event: ExecutionEvent) -> ExecutionEvent:
        return self.tables.add_event(event)

    def events(self, review_id: str, after: int = 0, limit: int = 1000) -> list[ExecutionEvent]:
        return self.tables.list_events(review_id, after=after, limit=limit)
