"""Graph execution: start, resume, status detection and restart recovery."""
from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command

from ..core.exceptions import ConflictError, ReviewCancelled, describe_error
from ..models.domain import RUNNING_REVIEW_STATUSES, AgentStatus, ReviewStatus
from .builder import build_review_graph
from .checkpoint import create_checkpointer, has_checkpoint

log = logging.getLogger(__name__)


class GraphExecutor:
    """Owns the compiled graph and its file-backed checkpointer for one container."""

    def __init__(self, container: Any) -> None:
        self.c = container
        s = container.settings_store.get()
        self.saver, self.conn = create_checkpointer(container.paths.checkpoint_db)
        self.graph = build_review_graph(self.saver)
        self._max_concurrency = s.max_concurrent_agents

    # ------------------------------------------------------------------ helpers
    def config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "max_concurrency": self.c.settings_store.get().max_concurrent_agents,
            "recursion_limit": 120,
        }

    def snapshot(self, review_id: str) -> Any:
        review = self.c.reviews.get(review_id)
        return self.graph.get_state(self.config(review.thread_id))

    def state_values(self, review_id: str) -> dict[str, Any]:
        return dict(self.snapshot(review_id).values or {})

    def is_interrupted(self, review_id: str) -> bool:
        snap = self.snapshot(review_id)
        return any(t.interrupts for t in snap.tasks)

    def has_checkpoint(self, review_id: str) -> bool:
        review = self.c.reviews.get(review_id)
        return has_checkpoint(self.saver, review.thread_id)

    # ------------------------------------------------------------------ run / resume
    def initial_state(self, review_id: str) -> dict[str, Any]:
        review = self.c.reviews.get(review_id)
        return {
            "review_id": review.review_id, "thread_id": review.thread_id, "project_id": review.project_id, "mode": review.mode,
            "provider": review.provider, "scenario_id": review.scenario_id, "enabled_agents": review.enabled_agents,
            "maximum_rounds": review.max_rounds, "architecture_revision": review.architecture_revision, "current_round": 1,
            "agent_results": [], "candidate_findings": [], "tool_results": [], "human_decisions": [], "rebuttals": [],
            "execution_events": [], "errors": [], "moderated_findings": [], "rerun_agents": [],
        }

    def run(self, review_id: str, *, resume_payload: dict[str, Any] | None = None) -> None:
        """Blocking execution used inside background tasks."""
        c = self.c
        review = c.reviews.get(review_id)
        cfg = self.config(review.thread_id)
        try:
            if resume_payload is not None:
                c.reviews.emit(review_id, "graph_resumed", f"Workflow resumed (action: {resume_payload.get('action')}).",
                               round_no=review.current_round)
                c.reviews.set_status(review_id, ReviewStatus.RE_REVIEWING if resume_payload.get("action") == "re_review"
                                     else ReviewStatus.MODERATING, "Resuming workflow", awaiting_human=False)
                self.graph.invoke(Command(resume=resume_payload), cfg)
            elif self.has_checkpoint(review_id) and self.snapshot(review_id).next:
                c.reviews.emit(review_id, "graph_resumed", "Continuing from the last LangGraph checkpoint.")
                self.graph.invoke(None, cfg)
            else:
                self.graph.invoke(self.initial_state(review_id), cfg)
            self._after_invoke(review_id)
        except ReviewCancelled:
            c.reviews.set_status(review_id, ReviewStatus.FAILED, "Cancelled", error="Review cancelled by the user.")
            c.reviews.emit(review_id, "review_cancelled", "Review cancelled by the user.", level="WARNING")
        except Exception as exc:  # any workflow failure is surfaced on the review, never swallowed
            msg = describe_error(exc)
            log.exception("Review %s failed", review_id)
            c.reviews.set_status(review_id, ReviewStatus.FAILED, "Failed", error=msg)
            c.reviews.emit(review_id, "workflow_failed", f"Workflow failed: {msg}", level="ERROR")
            self._fail_running_executions(review_id, msg)
        finally:
            c.snapshots.schedule_save()

    def _after_invoke(self, review_id: str) -> None:
        c = self.c
        snap = self.snapshot(review_id)
        review = c.reviews.get(review_id)
        if any(t.interrupts for t in snap.tasks):
            if review.status != ReviewStatus.AWAITING_HUMAN:
                c.reviews.set_status(review_id, ReviewStatus.AWAITING_HUMAN, "Awaiting architect decisions", awaiting_human=True)
            c.reviews.emit(review_id, "graph_interrupted", "Workflow paused for human review (LangGraph interrupt).",
                           round_no=review.current_round)
        elif not snap.next:
            values = snap.values or {}
            final = values.get("workflow_status")
            if final and review.status.value not in ("COMPLETED", "COMPLETED_WITH_OPEN_RISKS"):
                c.reviews.set_status(review_id, ReviewStatus(final), "Completed")

    def _fail_running_executions(self, review_id: str, msg: str) -> None:
        for ex in self.c.reviews.executions(review_id):
            if ex.status == AgentStatus.RUNNING:
                self.c.reviews.update_execution(ex.execution_id, status=AgentStatus.FAILED, error=msg)

    # ------------------------------------------------------------------ public async-ish API
    def start(self, review_id: str) -> None:
        review = self.c.reviews.ensure_startable(review_id)
        if self.c.tasks.is_active(review_id):
            raise ConflictError("This review is already running.")
        if review.status == ReviewStatus.FAILED:
            review.recovery_note = None
        accepted = self.c.tasks.submit(review_id, lambda: self.run(review_id))
        if not accepted:
            raise ConflictError("This review is already running.")

    def resume(self, review_id: str, payload: dict[str, Any]) -> None:
        review = self.c.reviews.get(review_id)
        if review.status != ReviewStatus.AWAITING_HUMAN:
            raise ConflictError(f"Review is {review.status.value}; it can only be resumed while AWAITING_HUMAN.")
        if not self.is_interrupted(review_id):
            # no live checkpoint: reconstruct a safe review state instead of pretending to continue
            self._reconstruct(review_id)
        if not self.c.tasks.submit(review_id, lambda: self.run(review_id, resume_payload=payload)):
            raise ConflictError("This review is already being processed.")

    # ------------------------------------------------------------------ recovery
    def _reconstruct(self, review_id: str) -> None:
        """Re-seed a fresh thread at the human boundary from persisted findings (no exact continuation)."""
        c = self.c
        review = c.reviews.get(review_id)
        findings = c.reviews.findings(review_id)
        model = c.reviews.get_model(review_id, review.architecture_revision)
        revs = c.reviews.revisions(review_id)
        current = next((r for r in revs if r.number == review.architecture_revision), None)
        manifest = []
        for aid in (current.artifact_ids if current else []):
            art = c.tables.artifacts.get(aid)
            if art:
                manifest.append({"artifact_id": art.artifact_id, "filename": art.filename, "sha256": art.sha256, "role": art.role.value,
                                 "detected_type": art.detected_type})
        decisions = [d.model_dump(mode="json") for d in c.decisions.history(review_id) if d.submitted]
        rebuttals = [r.model_dump(mode="json") for r in c.tables.rebuttals.list(review_id=review_id)]
        review.thread_id = f"{review.thread_id.split('-r')[0]}-r{len(review.thread_id.split('-r')) + 1}"
        review.recovery_note = ("The LangGraph checkpoint was unavailable, so a new graph thread was seeded from the persisted findings and "
                                "decisions. Human decisions were preserved; exact graph continuation was not possible.")
        c.reviews.save(review)
        state = self.initial_state(review_id)
        state.update({
            "architecture_model": model.model_dump(mode="json") if model else {}, "source_artifacts": manifest,
            "moderated_findings": [f.model_dump(mode="json") for f in findings], "human_decisions": decisions, "rebuttals": rebuttals,
            "current_round": max(1, review.current_round), "reconstructed": True, "next_step": "human_review",
        })
        cfg = self.config(review.thread_id)
        try:
            self.graph.invoke(state, cfg)  # runs entry_router -> human_review and pauses at the interrupt
        except Exception:
            log.exception("Reconstruction failed for %s", review_id)
            raise
        c.reviews.emit(review_id, "checkpoint_reconstructed", review.recovery_note, level="WARNING")

    def recover_on_startup(self) -> list[str]:
        """Mark interrupted work after a restart; never auto-run LLM calls."""
        c = self.c
        notes: list[str] = []
        for review in c.reviews.list():
            if review.status.value in RUNNING_REVIEW_STATUSES:
                has_cp = self.has_checkpoint(review.review_id)
                msg = ("The backend restarted while this review was running. "
                       + ("A LangGraph checkpoint exists - press Start to continue from it." if has_cp
                          else "No checkpoint exists - press Start to run the review again from the beginning."))
                c.reviews.set_status(review.review_id, ReviewStatus.FAILED, "Interrupted by restart", error=msg)
                self._fail_running_executions(review.review_id, "Interrupted by backend restart")
                c.reviews.emit(review.review_id, "recovered_after_restart", msg, level="WARNING")
                notes.append(review.review_id)
            elif review.status == ReviewStatus.AWAITING_HUMAN:
                has_cp = self.has_checkpoint(review.review_id) and self.is_interrupted(review.review_id)
                if not has_cp:
                    r = c.reviews.get(review.review_id)
                    r.recovery_note = ("No usable LangGraph checkpoint was found after restart. Decisions and findings were restored from "
                                       "snapshots; the workflow will be re-seeded at the human-review step when you resume.")
                    c.reviews.save(r)
                    notes.append(review.review_id)
        return notes
