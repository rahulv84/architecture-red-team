"""Application-level workflow commands (start, resume, re-review, cancel) used by the API."""
from __future__ import annotations

from typing import Any

from ..core.exceptions import ConflictError, NotFoundError, ValidationFailed
from ..models.domain import ReviewStatus


class WorkflowService:
    def __init__(self, container: Any) -> None:
        self.c = container

    def start(self, review_id: str) -> dict[str, Any]:
        self.c.executor.start(review_id)
        return {"review_id": review_id, "status": "STARTING"}

    def resume(self, review_id: str, action: str = "submit_decisions", notes: str = "") -> dict[str, Any]:
        if action not in ("submit_decisions", "finalize"):
            raise ValidationFailed("action must be 'submit_decisions' or 'finalize' (use /re-review for revisions).")
        review = self.c.reviews.get(review_id)
        if review.status != ReviewStatus.AWAITING_HUMAN:
            raise ConflictError(f"Review is {review.status.value}; it can only be resumed while AWAITING_HUMAN.")
        pending = self.c.decisions.pending(review_id)
        payload = {"action": action, "decisions": [d.model_dump(mode="json") for d in pending], "notes": notes}
        self.c.executor.resume(review_id, payload)
        return {"review_id": review_id, "action": action, "decisions_submitted": len(pending)}

    def re_review(self, review_id: str, revision_id: str | None = None, notes: str = "") -> dict[str, Any]:
        review = self.c.reviews.get(review_id)
        if review.status != ReviewStatus.AWAITING_HUMAN:
            raise ConflictError(f"Review is {review.status.value}; re-review requires AWAITING_HUMAN.")
        if review.current_round >= review.max_rounds:
            raise ConflictError(f"The maximum number of review rounds ({review.max_rounds}) has been reached. Finalize the review instead.")
        revisions = self.c.reviews.revisions(review_id)
        rev = None
        if revision_id:
            rev = next((r for r in revisions if r.revision_id == revision_id), None)
            if rev is None:
                raise NotFoundError(f"Revision '{revision_id}' not found for this review.")
        else:
            rev = next((r for r in reversed(revisions) if r.number > review.architecture_revision), None)
        if rev is None or rev.number <= review.architecture_revision:
            raise ValidationFailed("Submit an architecture revision (files or change notes) before requesting a re-review.")
        pending = self.c.decisions.pending(review_id)
        payload = {
            "action": "re_review", "revision_id": rev.revision_id, "notes": notes or rev.change_notes,
            "decisions": [d.model_dump(mode="json") for d in pending],
        }
        self.c.executor.resume(review_id, payload)
        return {"review_id": review_id, "action": "re_review", "revision": rev.number,
                "affected_agents": rev.impact.affected_agents if rev.impact else [], "decisions_submitted": len(pending)}

    def cancel(self, review_id: str) -> dict[str, Any]:
        review = self.c.reviews.get(review_id)
        if not self.c.tasks.is_active(review_id):
            raise ConflictError(f"Review {review.status.value} is not running; nothing to cancel.")
        self.c.reviews.request_cancel(review_id)
        self.c.reviews.emit(review_id, "cancel_requested", "Cancellation requested; in-flight work will stop at the next checkpoint.", level="WARNING")
        return {"review_id": review_id, "cancel_requested": True}
