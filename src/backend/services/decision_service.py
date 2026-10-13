"""Human decisions (accept / dispute / defer), finding lifecycle and ADR creation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.config import SettingsStore
from ..core.exceptions import ConflictError, ValidationFailed
from ..core.paths import DataPaths
from ..models.database import Tables
from ..models.domain import (
    ArchitectureDecisionRecord,
    DecisionType,
    Finding,
    FindingStatus,
    HumanDecision,
    Review,
    ReviewStatus,
    utcnow,
)
from ..models.schemas import DecisionCreate
from ..reporting.adr_generator import build_adr, slugify
from .review_service import ReviewService


def apply_decisions(findings: list[Finding], decisions: list[dict[str, Any]], max_disputes: int = 2) -> list[Finding]:
    """Pure, idempotent application of human decisions to findings (used by the graph node).

    A decision already present in a finding's history (by decision_id) is not applied twice.
    """
    by_id = {f.finding_id: f for f in findings}
    for raw in sorted(decisions, key=lambda d: d.get("created_at", "")):
        f = by_id.get(raw["finding_id"])
        if f is None:
            continue
        marker = raw["decision_id"]
        if any(h.data.get("decision_id") == marker for h in f.history):
            continue
        dtype = raw["decision_type"]
        actor = raw.get("actor", "architect")
        if dtype == DecisionType.ACCEPT.value:
            f.status = FindingStatus.ACCEPTED
            f.add_history("ACCEPTED", actor, raw.get("notes") or "Architect accepted the finding as valid.",
                          data={"decision_id": marker}, round=raw.get("round"), revision=raw.get("revision"))
        elif dtype == DecisionType.DISPUTE.value:
            f.status = FindingStatus.DISPUTED
            f.dispute_count = min(f.dispute_count + 1, max_disputes)
            f.add_history("DISPUTED", actor, raw.get("justification", ""), data={"decision_id": marker},
                          round=raw.get("round"), revision=raw.get("revision"))
        elif dtype == DecisionType.DEFER.value:
            f.status = FindingStatus.DEFERRED
            f.add_history("DEFERRED", actor, raw.get("justification", ""),
                          data={"decision_id": marker, "review_date": raw.get("review_date"), "risk_owner": raw.get("risk_owner"),
                                "remediation_target": raw.get("remediation_target")},
                          round=raw.get("round"), revision=raw.get("revision"))
    return list(by_id.values())


class DecisionService:
    def __init__(self, tables: Tables, reviews: ReviewService, paths: DataPaths, settings_store: SettingsStore) -> None:
        self.tables = tables
        self.reviews = reviews
        self.paths = paths
        self.settings_store = settings_store

    # ------------------------------------------------------------------ recording
    def record(self, review_id: str, payload: DecisionCreate) -> HumanDecision:
        review = self.reviews.get(review_id)
        if review.status != ReviewStatus.AWAITING_HUMAN:
            raise ConflictError(f"Decisions can only be recorded while the review is AWAITING_HUMAN (currently {review.status.value}).")
        finding = self.reviews.get_finding(payload.finding_id)
        if finding.review_id != review_id:
            raise ValidationFailed("Finding does not belong to this review.")
        if finding.moderator_status.value == "REJECTED" or finding.status in (FindingStatus.REJECTED, FindingStatus.VERIFIED):
            raise ConflictError(f"Finding is {finding.status.value}; no further decision is possible.")
        justification = payload.justification.strip()
        max_disputes = self.settings_store.get().max_disputes_per_finding
        if payload.decision_type == DecisionType.DISPUTE:
            if len(justification) < 10:
                raise ValidationFailed("A dispute requires a justification of at least 10 characters.")
            if finding.dispute_count >= max_disputes:
                raise ConflictError(f"This finding has already been disputed {finding.dispute_count} time(s); accept or defer it, or revise the architecture.")
        if payload.decision_type == DecisionType.DEFER and len(justification) < 10:
            raise ValidationFailed("Deferring a finding requires a justification of at least 10 characters.")
        decision = HumanDecision(
            review_id=review_id, finding_id=payload.finding_id, decision_type=payload.decision_type, justification=justification,
            notes=payload.notes.strip(), review_date=payload.review_date, risk_owner=payload.risk_owner,
            remediation_target=payload.remediation_target, actor=payload.actor or "architect", round=review.current_round,
            revision=review.architecture_revision,
        )
        self.tables.decisions.put(decision)
        # reflect immediately in the findings table (graph state is updated on resume)
        updated = apply_decisions([finding], [decision.model_dump(mode="json")], max_disputes)[0]
        self.tables.findings.put(updated)
        self.reviews.emit(review_id, "human_decision_received",
                          f"Architect decision {decision.decision_type.value} recorded for '{finding.title[:70]}'.",
                          round_no=review.current_round, payload={"finding_id": finding.finding_id, "decision_id": decision.decision_id})
        if payload.decision_type == DecisionType.DEFER:
            self.ensure_adr(review, updated, kind="DEFERRED_RISK", decision=decision)
        return decision

    def pending(self, review_id: str) -> list[HumanDecision]:
        return sorted([d for d in self.tables.decisions.list(review_id=review_id) if not d.submitted], key=lambda d: d.created_at)

    def mark_submitted(self, decisions: list[HumanDecision]) -> None:
        for d in decisions:
            d.submitted = True
        self.tables.decisions.put_many(decisions)

    def mark_ids_submitted(self, decision_ids: list[str]) -> None:
        rows = [self.tables.decisions.get(i) for i in decision_ids]
        self.mark_submitted([d for d in rows if d is not None])

    def history(self, review_id: str) -> list[HumanDecision]:
        return sorted(self.tables.decisions.list(review_id=review_id), key=lambda d: d.created_at)

    # ------------------------------------------------------------------ ADRs
    def ensure_adr(self, review: Review, finding: Finding, *, kind: str, decision: HumanDecision | None = None,
                   determination: str = "") -> ArchitectureDecisionRecord:
        existing = self.tables.adrs.list(review_id=review.review_id)
        for adr in existing:
            if finding.finding_id in adr.related_finding_ids and adr.title.startswith(
                {"DEFERRED_RISK": "Defer", "ACCEPTED_FINDING": "Adopt", "DISPUTE_RESOLUTION": "Disposition"}[kind]):
                if decision is None or adr.source_decision_id == decision.decision_id or kind != "DEFERRED_RISK":
                    return adr
        adr = build_adr(review, finding, kind=kind, number=len(existing) + 1, decision=decision, determination=determination)
        adr_dir = self.paths.review_report_dir(review.review_id) / "adrs"
        adr_dir.mkdir(parents=True, exist_ok=True)
        path = adr_dir / f"ADR-{adr.number:03d}-{slugify(finding.title)}.md"
        tmp = path.with_suffix(".md.part")
        tmp.write_text(adr.markdown, encoding="utf-8")
        os.replace(tmp, path)
        adr.path = str(path)
        self.tables.adrs.put(adr)
        self.reviews.emit(review.review_id, "adr_generated", f"ADR-{adr.number:03d} drafted: {adr.title[:80]}",
                          payload={"adr_id": adr.adr_id, "finding_id": finding.finding_id})
        return adr

    def ensure_review_adrs(self, review: Review, findings: list[Finding]) -> list[ArchitectureDecisionRecord]:
        """At finalization: ADRs for meaningful accepted decisions and deferred risks (idempotent)."""
        out: list[ArchitectureDecisionRecord] = []
        decisions = self.history(review.review_id)
        for f in findings:
            if f.moderator_status.value == "REJECTED":
                continue
            last = next((d for d in reversed(decisions) if d.finding_id == f.finding_id), None)
            if f.status == FindingStatus.DEFERRED and last is not None:
                out.append(self.ensure_adr(review, f, kind="DEFERRED_RISK", decision=last))
            elif f.status == FindingStatus.ACCEPTED and f.severity.value in ("CRITICAL", "HIGH", "MEDIUM") and last is not None:
                out.append(self.ensure_adr(review, f, kind="ACCEPTED_FINDING", decision=last))
        return out

    def adrs(self, review_id: str) -> list[ArchitectureDecisionRecord]:
        return sorted(self.tables.adrs.list(review_id=review_id), key=lambda a: a.number)
    def read_adr_file(path: str | None) -> str:
        return Path(path).read_text(encoding="utf-8") if path and Path(path).exists() else ""


__all__ = ["DecisionService", "apply_decisions", "utcnow"]
