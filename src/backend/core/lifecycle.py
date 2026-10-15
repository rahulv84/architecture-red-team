"""Application container (service wiring) and process lifecycle.

The container is the single designated holder of runtime services.  FastAPI routes receive
it via dependency injection; LangGraph nodes retrieve it with :func:`get_container` because
graph state must stay JSON-serialisable (no clients or sessions inside checkpoints).
"""
from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

from ..agents import AGENT_CLASSES, BaseAgent
from ..llm.base_provider import LLMProvider
from ..llm.provider_factory import create_provider
from ..models.database import Database, Tables
from ..models.domain import Review
from ..services.architecture_service import ArchitectureService
from ..services.context_service import ContextService, TfidfIndex
from ..services.decision_service import DecisionService
from ..services.demo_service import DemoService
from ..services.evaluation_service import EvaluationService
from ..services.ingestion_service import IngestionService
from ..services.report_service import ReportService
from ..services.review_service import ReviewService
from ..services.revision_service import RevisionService
from ..services.scoring_service import ScoringService
from ..services.snapshot_service import SnapshotService
from ..services.tool_service import run_checkov_for
from ..services.workflow_service import WorkflowService
from .config import Settings, SettingsStore
from .logging import setup_logging
from .paths import DataPaths
from .tasks import TaskManager

log = logging.getLogger(__name__)


class ReviewCache:
    """Per-review parsed-document, retrieval-index and scanner caches (rebuildable, never authoritative)."""

    def __init__(self, ingestion: IngestionService) -> None:
        self.ingestion = ingestion
        self._lock = threading.RLock()
        self._docs: dict[tuple[str, int], list[Any]] = {}
        self._index: dict[tuple[str, int], TfidfIndex] = {}
        self._checkov: dict[tuple[str, int], tuple[list[Any], Any]] = {}

    def docs(self, review_id: str, revision: int, artifact_ids: list[str]) -> list[Any]:
        key = (review_id, revision)
        with self._lock:
            if key not in self._docs:
                loaded = []
                for aid in artifact_ids:
                    art = self.ingestion.tables.artifacts.get(aid)
                    parsed = self.ingestion.parsed(aid)
                    if art is not None and parsed is not None:
                        loaded.append((art, parsed))
                self._docs[key] = loaded
            return self._docs[key]

    def index(self, review_id: str, revision: int, docs: list[Any], context: ContextService) -> TfidfIndex:
        key = (review_id, revision)
        with self._lock:
            if key not in self._index:
                self._index[key] = context.build_index(context.chunks_from_documents(docs))
            return self._index[key]

    def checkov(self, review_id: str, revision: int, docs: list[Any], settings: Settings) -> tuple[list[Any], Any]:
        key = (review_id, revision)
        with self._lock:
            if key not in self._checkov:
                self._checkov[key] = run_checkov_for(docs, settings.enable_checkov, settings.checkov_timeout_seconds)
            return self._checkov[key]

    def invalidate(self, review_id: str) -> None:
        with self._lock:
            for store in (self._docs, self._index, self._checkov):
                for key in [k for k in store if k[0] == review_id]:
                    store.pop(key, None)


class AppContainer:
    def __init__(self, settings: Settings | None = None, *, persist_settings: bool = True, autosave: bool = True,
                 start_logging: bool = False) -> None:
        self.settings_store = SettingsStore(settings, persist=persist_settings)
        s = self.settings_store.get()
        self.paths = DataPaths(s.data_dir)
        self.paths.ensure()
        if start_logging:
            setup_logging(self.paths.logs)
        self.db = Database()
        self.tables = Tables(self.db)
        self.ingestion = IngestionService(self.tables, self.paths, self.settings_store)
        self.context = ContextService(s.knowledge_dir)
        self.architecture = ArchitectureService(self.ingestion)
        self.reviews = ReviewService(self.tables, self.settings_store, self.ingestion)
        self.decisions = DecisionService(self.tables, self.reviews, self.paths, self.settings_store)
        self.scoring = ScoringService(self.context)
        self.demo = DemoService(self.settings_store)
        self.evaluation = EvaluationService(self.reviews, self.demo)
        self.reports = ReportService(self.tables, self.reviews, self.decisions, self.scoring, self.evaluation, self.paths)
        self.snapshots = SnapshotService(self.tables, self.paths, self.settings_store)
        self.revisions = RevisionService(self)
        self.workflow = WorkflowService(self)
        self.tasks = TaskManager(max_workers=4)
        self.review_cache = ReviewCache(self.ingestion)
        self._executor: Any = None
        self._executor_lock = threading.Lock()
        self.restore_info: dict[str, Any] = {"restored": False}
        self._autosave = autosave

    # ---- lazily constructed graph executor (imports langgraph) -------------------
    @property
    def executor(self) -> Any:
        with self._executor_lock:
            if self._executor is None:
                from ..graph.execution import GraphExecutor

                self._executor = GraphExecutor(self)
            return self._executor

    # ---- providers & agents ---------------------------------------------------------
    def provider_for(self, review: Review) -> LLMProvider:
        settings = self.settings_store.get()
        if review.mode == "demo":
            return create_provider("mock", settings)
        overrides: dict[str, str] = {}
        if review.model_id and review.model_id != "mock-deterministic":
            key = {"anthropic": "anthropic_model", "openai": "openai_model", "gemini": "gemini_model",
                   "bedrock": "bedrock_model_id"}.get(review.provider)
            if key:
                overrides[key] = review.model_id
        return create_provider(review.provider, settings.model_copy(update=overrides))

    def agent_for(self, review_id: str, agent_id: str) -> BaseAgent:
        review = self.reviews.get(review_id)
        return AGENT_CLASSES[agent_id](self.provider_for(review), self.context)

    # ---- lifecycle -----------------------------------------------------------------------
    def startup(self) -> None:
        self.restore_info = self.snapshots.restore()
        if self.restore_info.get("restored"):
            log.info("Restored application state from %s (saved %s)", self.restore_info["source"], self.restore_info["saved_at"])
        notes = self.executor.recover_on_startup()
        if notes:
            log.warning("Recovery notes recorded for %d review(s)", len(notes))
        if self._autosave:
            self.snapshots.enable_autosave()

    def shutdown(self) -> None:
        try:
            self.snapshots.flush()
        except Exception:
            log.exception("Final snapshot failed")
        self.tasks.shutdown()
        if self._executor is not None:
            with contextlib.suppress(Exception):
                self._executor.conn.close()
        self.db.dispose()


_CONTAINER: AppContainer | None = None
_LOCK = threading.Lock()


def get_container() -> AppContainer:
    with _LOCK:
        if _CONTAINER is None:
            raise RuntimeError("Application container has not been initialised.")
        return _CONTAINER


def set_container(container: AppContainer | None) -> None:
    global _CONTAINER
    with _LOCK:
        _CONTAINER = container
