"""In-memory SQLite database (SQLAlchemy) and a small typed document-table layer.

Design notes
------------
* The primary application database is ``sqlite://`` (in-memory).  A ``StaticPool`` keeps one
  connection alive for the lifetime of the :class:`Database` so every FastAPI worker thread
  and background task sees the *same* in-memory database.
* A single re-entrant lock serialises transactions on that shared connection (SQLite
  connections are not safe for interleaved transactions).  All access goes through
  :meth:`Database.session`.
* Each domain entity is stored as one row: a few indexed key columns plus the full
  Pydantic payload as JSON.  That keeps snapshots trivial (dump/restore the payloads).
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Integer, String, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from .architecture import ArchitectureModel
from .domain import (
    AgentExecution,
    ArchitectureArtifact,
    ArchitectureDecisionRecord,
    ArchitectureRevision,
    ExecutionEvent,
    Finding,
    HumanDecision,
    Project,
    Rebuttal,
    ReportArtifact,
    Review,
    ToolResult,
)


class Base(DeclarativeBase):
    pass


class _Doc:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class ProjectRow(_Doc, Base):
    __tablename__ = "projects"


class ArtifactRow(_Doc, Base):
    __tablename__ = "artifacts"
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReviewRow(_Doc, Base):
    __tablename__ = "reviews"
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)


class RevisionRow(_Doc, Base):
    __tablename__ = "revisions"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    number: Mapped[int] = mapped_column(Integer)


class ModelRow(_Doc, Base):
    __tablename__ = "architecture_models"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)


class AgentExecutionRow(_Doc, Base):
    __tablename__ = "agent_executions"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str] = mapped_column(String(32))
    round: Mapped[int] = mapped_column(Integer)


class FindingRow(_Doc, Base):
    __tablename__ = "findings"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32))


class ToolResultRow(_Doc, Base):
    __tablename__ = "tool_results"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class DecisionRow(_Doc, Base):
    __tablename__ = "human_decisions"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    finding_id: Mapped[str] = mapped_column(String(64), index=True)


class RebuttalRow(_Doc, Base):
    __tablename__ = "rebuttals"
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    finding_id: Mapped[str] = mapped_column(String(64), index=True)


class AdrRow(_Doc, Base):
    __tablename__ = "adrs"
    review_id: Mapped[str] = mapped_column(String(64), index=True)


class ReportRow(_Doc, Base):
    __tablename__ = "reports"
    review_id: Mapped[str] = mapped_column(String(64), index=True)


class EventRow(Base):
    __tablename__ = "execution_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSON)


M = TypeVar("M", bound=BaseModel)


class Database:
    """Owns the in-memory engine, the lock and commit hooks."""

    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(self.engine)
        self._lock = threading.RLock()
        self._commit_hooks: list[Callable[[], None]] = []
        self._dirty = False
        self._depth = 0

    def on_commit(self, hook: Callable[[], None]) -> None:
        self._commit_hooks.append(hook)

    @contextmanager
    def session(self, *, write: bool = True) -> Iterator[Session]:
        """Run a unit of work on the shared connection (serialised, re-entrant)."""
        with self._lock:
            self._depth += 1
            session = Session(self.engine, expire_on_commit=False)
            try:
                yield session
                session.commit()
                if write:
                    self._dirty = True
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
                self._depth -= 1
            if self._depth == 0 and self._dirty and write:
                self._dirty = False
                for hook in self._commit_hooks:
                    hook()

    def reset(self) -> None:
        with self._lock:
            Base.metadata.drop_all(self.engine)
            Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


class EntityTable(Generic[M]):
    """Typed CRUD over one JSON-document table."""

    def __init__(
        self,
        db: Database,
        row_cls: type[Any],
        model_cls: type[M],
        id_field: str,
        columns: dict[str, Callable[[M], Any]] | None = None,
    ) -> None:
        self.db = db
        self.row_cls = row_cls
        self.model_cls = model_cls
        self.id_field = id_field
        self.columns = columns or {}

    def _to_row(self, model: M) -> dict[str, Any]:
        values = {"id": getattr(model, self.id_field), "data": model.model_dump(mode="json")}
        for col, fn in self.columns.items():
            value = fn(model)
            values[col] = getattr(value, "value", value)
        return values

    def put(self, model: M) -> M:
        values = self._to_row(model)
        with self.db.session() as s:
            row = s.get(self.row_cls, values["id"])
            if row is None:
                s.add(self.row_cls(**values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
        return model

    def put_many(self, models: list[M]) -> None:
        with self.db.session() as s:
            for model in models:
                values = self._to_row(model)
                row = s.get(self.row_cls, values["id"])
                if row is None:
                    s.add(self.row_cls(**values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)

    def get(self, entity_id: str) -> M | None:
        with self.db.session(write=False) as s:
            row = s.get(self.row_cls, entity_id)
            return self.model_cls.model_validate(row.data) if row else None

    def list(self, **filters: Any) -> list[M]:
        stmt = select(self.row_cls)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.row_cls, key) == getattr(value, "value", value))
        with self.db.session(write=False) as s:
            rows = s.execute(stmt).scalars().all()
            return [self.model_cls.model_validate(r.data) for r in rows]

    def delete(self, entity_id: str) -> None:
        with self.db.session() as s:
            row = s.get(self.row_cls, entity_id)
            if row is not None:
                s.delete(row)
class Tables:
    """All entity tables for one :class:`Database`."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.projects = EntityTable(db, ProjectRow, Project, "project_id")
        self.artifacts = EntityTable(
            db, ArtifactRow, ArchitectureArtifact, "artifact_id",
            {"project_id": lambda m: m.project_id, "active": lambda m: m.active},
        )
        self.reviews = EntityTable(
            db, ReviewRow, Review, "review_id",
            {"project_id": lambda m: m.project_id, "status": lambda m: m.status},
        )
        self.revisions = EntityTable(
            db, RevisionRow, ArchitectureRevision, "revision_id",
            {"review_id": lambda m: m.review_id, "number": lambda m: m.number},
        )
        self.models = EntityTable(db, ModelRow, _StoredModel, "key", {
            "review_id": lambda m: m.review_id, "revision": lambda m: m.revision})
        self.executions = EntityTable(
            db, AgentExecutionRow, AgentExecution, "execution_id",
            {"review_id": lambda m: m.review_id, "agent_id": lambda m: m.agent_id, "round": lambda m: m.round},
        )
        self.findings = EntityTable(
            db, FindingRow, Finding, "finding_id",
            {"review_id": lambda m: m.review_id, "agent_id": lambda m: m.agent_id,
             "severity": lambda m: m.severity, "status": lambda m: m.status},
        )
        self.tool_results = EntityTable(db, ToolResultRow, _StoredToolResult, "key", {
            "review_id": lambda m: m.review_id, "revision": lambda m: m.revision})
        self.decisions = EntityTable(
            db, DecisionRow, HumanDecision, "decision_id",
            {"review_id": lambda m: m.review_id, "finding_id": lambda m: m.finding_id},
        )
        self.rebuttals = EntityTable(
            db, RebuttalRow, Rebuttal, "rebuttal_id",
            {"review_id": lambda m: m.review_id, "finding_id": lambda m: m.finding_id},
        )
        self.adrs = EntityTable(
            db, AdrRow, ArchitectureDecisionRecord, "adr_id", {"review_id": lambda m: m.review_id}
        )
        self.reports = EntityTable(
            db, ReportRow, ReportArtifact, "report_id", {"review_id": lambda m: m.review_id}
        )

    # ---- events (autoincrement sequence) --------------------------------
    def add_event(self, event: ExecutionEvent) -> ExecutionEvent:
        with self.db.session() as s:
            row = EventRow(review_id=event.review_id, data={})
            s.add(row)
            s.flush()
            event.seq = row.seq
            row.data = event.model_dump(mode="json")
        return event

    def list_events(self, review_id: str, after: int = 0, limit: int = 1000) -> list[ExecutionEvent]:
        stmt = (
            select(EventRow)
            .where(EventRow.review_id == review_id, EventRow.seq > after)
            .order_by(EventRow.seq)
            .limit(limit)
        )
        with self.db.session(write=False) as s:
            return [ExecutionEvent.model_validate(r.data) for r in s.execute(stmt).scalars().all()]

    # ---- snapshot support ------------------------------------------------
    def entity_tables(self) -> dict[str, EntityTable[Any]]:
        return {
            "projects": self.projects,
            "artifacts": self.artifacts,
            "reviews": self.reviews,
            "revisions": self.revisions,
            "architecture_models": self.models,
            "agent_executions": self.executions,
            "findings": self.findings,
            "tool_results": self.tool_results,
            "decisions": self.decisions,
            "rebuttals": self.rebuttals,
            "adrs": self.adrs,
            "reports": self.reports,
        }

    def dump_events(self) -> list[dict[str, Any]]:
        with self.db.session(write=False) as s:
            return [r.data for r in s.execute(select(EventRow).order_by(EventRow.seq)).scalars().all()]

    def restore_events(self, events: list[dict[str, Any]]) -> None:
        with self.db.session() as s:
            s.execute(delete(EventRow))
            for data in events:
                evt = ExecutionEvent.model_validate(data)
                s.add(EventRow(seq=evt.seq, review_id=evt.review_id, data=evt.model_dump(mode="json")))


class _StoredModel(BaseModel):
    """Architecture model row payload keyed by ``review_id:revision``."""

    key: str
    review_id: str
    revision: int
    model: ArchitectureModel


class _StoredToolResult(BaseModel):
    """Per-review tool result row keyed by ``review_id:revision:tool_result_id``."""

    key: str
    review_id: str
    revision: int
    result: ToolResult
