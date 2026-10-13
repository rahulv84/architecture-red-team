"""Explainable domain scorecard.

The rubric lives in ``knowledge/agent_rubrics/scoring.yaml``.  Confirmed concerns and
unverified concerns are tracked separately and unknown information lowers *coverage*, not
the score.  Scores are rounded to the nearest 5 so they do not imply false precision.
"""
from __future__ import annotations

from typing import Any

from ..models.domain import (
    AGENT_LABELS,
    ALL_AGENT_IDS,
    AgentExecution,
    AgentStatus,
    DomainScore,
    Finding,
    Review,
    Scorecard,
    ScoreContribution,
)
from .context_service import ContextService

METHODOLOGY = (
    "Each domain starts at 100. Open findings subtract severity weight x evidence multiplier x status factor. "
    "Findings that need validation count at a low multiplier and are reported separately as unverified concerns. "
    "Missing information is reported as coverage, not failure. Scores are indicative and rounded to 5; they are not an approval."
)


def _round5(value: float) -> int:
    return int(max(0, min(100, round(value / 5.0) * 5)))


class ScoringService:
    def __init__(self, context: ContextService) -> None:
        self.context = context

    def _band(self, score: int, bands: list[dict[str, Any]]) -> str:
        for b in sorted(bands, key=lambda b: -b["min"]):
            if score >= b["min"]:
                return str(b["label"])
        return "At risk"

    def scorecard(self, review: Review, findings: list[Finding], executions: list[AgentExecution]) -> Scorecard:
        rubric = self.context.scoring_rubric()
        weights = rubric["severity_weights"]
        mult = rubric["evidence_multipliers"]
        status_factor = rubric["status_treatment"]
        bands = rubric["bands"]
        latest: dict[str, AgentExecution] = {}
        for ex in sorted(executions, key=lambda e: e.round):
            if ex.status != AgentStatus.SKIPPED or ex.agent_id not in latest:
                latest[ex.agent_id] = ex
        domains: list[DomainScore] = []
        for agent_id in ALL_AGENT_IDS:
            enabled = agent_id in review.enabled_agents
            ex = latest.get(agent_id)
            confirmed_pen = unverified_pen = 0.0
            contribs: list[ScoreContribution] = []
            open_n = conf_n = unv_n = 0
            for f in findings:
                if f.agent_id != agent_id:
                    continue  # a merged finding is counted once, under its primary domain
                if f.moderator_status.value == "REJECTED":
                    continue
                factor = float(status_factor.get(f.status.value, 1.0))
                if factor == 0:
                    continue
                penalty = weights[f.severity.value] * mult[f.evidence_level.value] * factor
                if penalty <= 0:
                    continue
                unverified = f.evidence_level.value == "NEEDS_VALIDATION"
                open_n += 1
                if unverified:
                    unverified_pen += penalty
                    unv_n += 1
                else:
                    confirmed_pen += penalty
                    conf_n += 1
                contribs.append(ScoreContribution(
                    finding_id=f.finding_id, title=f.title, severity=f.severity.value, evidence_level=f.evidence_level.value,
                    status=f.status.value, penalty=round(penalty, 1), counted_as="unverified" if unverified else "confirmed"))
            score = _round5(100 - confirmed_pen - unverified_pen)
            conf_only = _round5(100 - confirmed_pen)
            gaps = list(ex.information_gaps) if ex else []
            if not enabled:
                coverage = "NONE"
            elif ex is None or ex.status in (AgentStatus.PENDING, AgentStatus.FAILED):
                coverage = "NONE" if ex is None or ex.status == AgentStatus.PENDING else "PARTIAL"
            elif ex.status == AgentStatus.SKIPPED:
                coverage = "PARTIAL"
            else:
                coverage = "PARTIAL" if len(gaps) >= 3 else "FULL"
            band = self._band(score, bands)
            if coverage == "NONE":
                band = "Not assessed"
            explanation = (
                f"{AGENT_LABELS[agent_id]}: {conf_n} confirmed/inferred concern(s) cost {round(confirmed_pen)} points; "
                f"{unv_n} unverified concern(s) cost {round(unverified_pen)} points. Coverage {coverage.lower()}"
                + (f" ({len(gaps)} information gap(s))." if gaps else ".")
                if enabled else f"{AGENT_LABELS[agent_id]} was not selected; no assessment."
            )
            domains.append(DomainScore(
                domain=agent_id, label=AGENT_LABELS[agent_id], score=score if coverage != "NONE" else 0,
                confirmed_only_score=conf_only if coverage != "NONE" else 0, band=band, coverage=coverage,
                open_findings=open_n, confirmed_findings=conf_n, unverified_findings=unv_n, information_gaps=gaps,
                contributions=sorted(contribs, key=lambda c: -c.penalty), explanation=explanation,
            ))
        assessed = [d for d in domains if d.coverage != "NONE"]
        order = ["At risk", "Needs attention", "Adequate", "Strong"]
        worst = min((d.band for d in assessed), key=lambda b: order.index(b) if b in order else 99) if assessed else "Not assessed"
        crit_high = sum(
            1 for f in findings
            if f.severity.value in ("CRITICAL", "HIGH") and f.status.value not in ("REJECTED", "RESOLVED", "VERIFIED")
            and f.moderator_status.value != "REJECTED"
        )
        statement = (
            f"Weakest assessed domain band: {worst}. {crit_high} unresolved Critical/High finding(s). "
            "This is an advisory summary produced by automated critics; architecture approval remains a human decision."
        )
        return Scorecard(review_id=review.review_id, revision=review.architecture_revision, domains=domains,
                         overall_band=worst, overall_statement=statement, methodology=METHODOLOGY)
