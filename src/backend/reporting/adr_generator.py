"""Architecture Decision Record (ADR) drafts rendered as Markdown."""
from __future__ import annotations

import re

from ..models.domain import ArchitectureDecisionRecord, Finding, HumanDecision, Review, utcnow


def slugify(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "decision"


def build_adr(
    review: Review, finding: Finding, *, kind: str, number: int, decision: HumanDecision | None = None, determination: str = "",
) -> ArchitectureDecisionRecord:
    evidence = "; ".join(r.short() for r in finding.source_references[:4]) or "no source reference (see finding)"
    context = (
        f"Review '{review.name}' (architecture revision {finding.last_seen_revision}) identified: **{finding.title}** "
        f"[{finding.severity.value}, {finding.evidence_level.value}] by {', '.join(finding.contributing_agents or [finding.agent_id])}.\n\n"
        f"{finding.description}\n\nImpact: {finding.impact or 'n/a'}\n\nEvidence: {evidence}."
    )
    options = [f"Remediate now: {finding.recommendation.splitlines()[0][:220]}", "Defer remediation and accept the residual risk for a defined period",
               "Reject the finding with supporting evidence"]
    if kind == "DEFERRED_RISK":
        title = f"Defer remediation: {finding.title}"
        target = decision.remediation_target if decision else None
        review_date = decision.review_date if decision else None
        text = (f"Remediation of this finding is deferred"
                f"{' until ' + target if target else ''}{' with a risk review on ' + review_date if review_date else ''}.")
        rationale = (decision.justification if decision else "") or "No justification recorded."
        consequences = ("The risk described remains in the architecture. It is NOT resolved or verified. "
                        "Compensating controls and a review date should be tracked by the risk owner.")
        owner = (decision.risk_owner if decision else None) or "Unassigned"
    elif kind == "ACCEPTED_FINDING":
        title = f"Adopt remediation: {finding.title}"
        text = "The architect confirmed the finding as valid. The recommended remediation is to be implemented."
        rationale = (decision.notes if decision and decision.notes else "Architect confirmed the finding is valid.")
        consequences = "Acceptance is not remediation: the finding stays open until a revision is reviewed and verified."
        owner = (decision.risk_owner if decision else None) or "Architecture owner"
        options = options[:1] + options[2:]
    else:
        title = f"Disposition of disputed finding: {finding.title}"
        text = determination or "The finding was disputed by the architect and re-evaluated by the critic and moderator."
        rationale = (decision.justification if decision else "") or "See exchange in the review history."
        consequences = "The decision and the supporting exchange are preserved in the finding history."
        owner = "Architecture owner"
    risks = finding.scenario or finding.impact or "See finding."
    md = render_markdown(
        number=number, title=title, status="PROPOSED", context=context, options=options, decision=text, rationale=rationale,
        consequences=consequences, risks=risks, owner=owner, related=[finding.finding_id], review=review,
    )
    return ArchitectureDecisionRecord(
        review_id=review.review_id, number=number, title=title, status="PROPOSED", context=context, options=options,
        decision=text, rationale=rationale, consequences=consequences, risks=risks, owner=owner,
        related_finding_ids=[finding.finding_id], source_decision_id=decision.decision_id if decision else None, markdown=md,
        created_at=utcnow(),
    )


def render_markdown(*, number: int, title: str, status: str, context: str, options: list[str], decision: str, rationale: str,
                    consequences: str, risks: str, owner: str, related: list[str], review: Review) -> str:
    banner = "> SIMULATED DEMO MODE: findings behind this ADR were generated from fixtures, not discovered by a model.\n\n" if review.simulated else ""
    opts = "\n".join(f"- {o}" for o in options)
    return (
        f"# ADR-{number:03d}: {title}\n\n{banner}"
        f"- **Decision ID:** ADR-{number:03d}\n- **Status:** {status} (draft - requires architect approval)\n"
        f"- **Owner:** {owner}\n- **Date:** {utcnow().date().isoformat()}\n- **Related findings:** {', '.join(related)}\n\n"
        f"## Context\n{context}\n\n## Options considered\n{opts}\n\n## Decision\n{decision}\n\n"
        f"## Rationale\n{rationale}\n\n## Consequences\n{consequences}\n\n## Risks\n{risks}\n"
    )
