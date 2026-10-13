"""Offline evaluation harness: compare findings with seeded expectations (observed results only)."""
from __future__ import annotations

from typing import Any

from ..models.domain import AgentStatus, Finding
from .review_service import ReviewService


class EvaluationService:
    def __init__(self, reviews: ReviewService, demo: Any) -> None:
        self.reviews = reviews
        self.demo = demo

    @staticmethod
    def _matches(finding: Finding, flaw: dict[str, Any], rules_by_tr: dict[str, str]) -> bool:
        if flaw["agent_id"] not in (finding.contributing_agents or [finding.agent_id]):
            return False
        finding_rules = {rules_by_tr.get(t) for t in finding.tool_result_refs}
        if set(flaw.get("rule_ids", [])) & finding_rules:
            return True
        text = f"{finding.title} {finding.description} {' '.join(finding.affected_components)}".lower()
        needles = [n.lower() for n in flaw.get("match_keywords", [])]
        return bool(needles) and all(n in text for n in needles)

    def evaluate(self, review_id: str) -> dict[str, Any]:
        review = self.reviews.get(review_id)
        expected = self.demo.expected(review.scenario_id) if review.scenario_id else {}
        findings = [f for f in self.reviews.findings(review_id)]
        executions = self.reviews.executions(review_id)
        events = self.reviews.tables.list_events(review_id, limit=5000)
        tool_rules = {t.tool_result_id: t.rule_id for t in self.reviews.tool_results(review_id)}
        live = [f for f in findings if f.moderator_status.value != "REJECTED"]
        started = next((e.timestamp for e in events if e.event_type == "review_started"), None)
        ended = next((e.timestamp for e in reversed(events) if e.event_type in ("moderator_completed", "review_completed")), None)
        invocations: dict[str, int] = {}
        for ex in executions:
            if ex.status in (AgentStatus.COMPLETED, AgentStatus.FAILED):
                invocations[ex.agent_id] = invocations.get(ex.agent_id, 0) + 1
        candidates = sum(len(f.merged_from) or 1 for f in findings)
        out: dict[str, Any] = {
            "review_id": review_id, "scenario_id": review.scenario_id, "simulated": review.simulated,
            "labeled": bool(expected.get("expected_flaws")),
            "review_rounds": review.current_round,
            "agent_invocations": invocations,
            "execution_seconds": round((ended - started).total_seconds(), 1) if started and ended else None,
            "candidate_findings": candidates, "distinct_findings": len(live),
            "duplicates_merged": max(0, candidates - len(findings)),
            "unsupported_findings": sum(1 for f in findings if f.moderator_status.value in ("REJECTED", "REQUIRES_VALIDATION")),
            "residual_duplicates": self._residual_duplicates([f for f in live if f.status.value not in ("RESOLVED", "VERIFIED", "REJECTED")]),
            "precision": None, "recall": None, "matched": [], "missed": [], "unexpected": [],
            "note": "Observed results on included fixtures only; no target values are claimed.",
        }
        if not out["labeled"]:
            out["note"] = "No labeled expectations exist for this review, so precision and recall are N/A."
            return out
        flaws = expected["expected_flaws"]
        matched_flaws: set[str] = set()
        matched_findings: set[str] = set()
        for f in live:
            for flaw in flaws:
                if self._matches(f, flaw, tool_rules):
                    matched_flaws.add(flaw["flaw_id"])
                    matched_findings.add(f.finding_id)
        revision_resolved = {f.finding_id for f in findings if f.status.value in ("RESOLVED", "VERIFIED")}
        out["matched"] = sorted(matched_flaws)
        out["missed"] = sorted({fl["flaw_id"] for fl in flaws} - matched_flaws)
        out["unexpected"] = sorted(f.title for f in live if f.finding_id not in matched_findings)
        considered = [f for f in live if f.finding_id not in revision_resolved]
        out["precision"] = round(len([f for f in considered if f.finding_id in matched_findings]) / len(considered), 2) if considered else None
        out["recall"] = round(len(matched_flaws) / len(flaws), 2)
        tool_detected = [fl["flaw_id"] for fl in flaws if fl.get("rule_ids") and any(r in tool_rules.values() for r in fl["rule_ids"])]
        out["deterministic_tool_detection"] = {"flaws_with_tool_rules": len([fl for fl in flaws if fl.get("rule_ids")]),
                                               "detected_by_tools": sorted(tool_detected)}
        seeded_unsupported = expected.get("seeded_unsupported", [])
        out["seeded_unsupported"] = [
            {"flaw_id": s["flaw_id"], "title_contains": s["title_contains"],
             "handled": any(s["title_contains"].lower() in f.title.lower() and f.moderator_status.value in ("REJECTED", "REQUIRES_VALIDATION")
                            for f in findings)}
            for s in seeded_unsupported
        ]
        return out

    @staticmethod
    def _residual_duplicates(findings: list[Finding]) -> int:
        seen: set[tuple[str, str]] = set()
        dup = 0
        for f in findings:
            for fp in f.fingerprints:
                if fp.startswith("title:"):
                    continue
                key = (fp, "x")
                if key in seen:
                    dup += 1
                seen.add(key)
        return dup
