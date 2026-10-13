"""Assembles the assessment report and writes JSON / PDF deliverables."""
from __future__ import annotations

from typing import Any

from ..core.exceptions import NotFoundError
from ..core.paths import DataPaths
from ..models.database import Tables
from ..models.domain import (
    AGENT_LABELS,
    CLOSED_STATUSES,
    SEVERITY_ORDER,
    Finding,
    FindingStatus,
    ReportArtifact,
    Review,
    ReviewStatus,
    utcnow,
)
from ..models.schemas import AssessmentReport, ReportFinding
from ..reporting.json_generator import write_json_report
from ..reporting.pdf_generator import build_pdf
from .decision_service import DecisionService
from .evaluation_service import EvaluationService
from .review_service import ReviewService
from .scoring_service import ScoringService

DISCLAIMERS = [
    "This report is advisory. Findings are produced by automated specialist critics and a moderator and must be reviewed by a human architect; "
    "no automated approval is granted.",
    "Compliance observations are architecture-level indications of possible control gaps. They are not certifications, audits or legal determinations.",
    "FinOps observations are qualitative cost-risk assessments; no pricing source was used and no monetary amounts are stated.",
]
LIMITATIONS = [
    "Findings are limited to the supplied material; absence of documentation is reported as 'needs validation', not as confirmed absence.",
    "Deterministic checks cover the supported Terraform / CloudFormation / Kubernetes constructs only; unsupported constructs are not evaluated.",
    "Diagram understanding covers structured formats; raster images require a vision-capable provider and are otherwise not interpreted.",
    "LLM-based inference can be incorrect; confidence and evidence levels indicate how each finding is supported.",
]


class ReportService:
    def __init__(self, tables: Tables, reviews: ReviewService, decisions: DecisionService, scoring: ScoringService,
                 evaluation: EvaluationService, paths: DataPaths) -> None:
        self.tables = tables
        self.reviews = reviews
        self.decisions = decisions
        self.scoring = scoring
        self.evaluation = evaluation
        self.paths = paths

    # ------------------------------------------------------------------ build
    def _remaining_risk(self, f: Finding) -> str:
        if f.status in (FindingStatus.RESOLVED, FindingStatus.VERIFIED):
            return "None identified" if f.status == FindingStatus.VERIFIED else "Not reproduced after revision; pending architect verification."
        if f.status == FindingStatus.REJECTED:
            return "None - finding withdrawn or rejected."
        if f.status == FindingStatus.DEFERRED:
            return f"Open - risk deferred by the architect ({f.severity.value}); not remediated."
        if f.status == FindingStatus.ACCEPTED:
            return f"Open - accepted as valid ({f.severity.value}); remediation not yet verified."
        return f"Open ({f.severity.value}) - awaiting architect decision or validation."

    def build(self, review_id: str) -> AssessmentReport:
        review = self.reviews.get(review_id)
        project = self.tables.projects.get(review.project_id)
        findings = self.reviews.findings(review_id)
        findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity.value], f.title))
        executions = self.reviews.executions(review_id)
        decisions = self.decisions.history(review_id)
        rebuttals = sorted(self.tables.rebuttals.list(review_id=review_id), key=lambda r: r.created_at)
        adrs = self.decisions.adrs(review_id)
        model = self.reviews.get_model(review_id, review.architecture_revision)
        score = self.scoring.scorecard(review, findings, executions)
        events = self.tables.list_events(review_id, limit=10000)
        revisions = self.reviews.revisions(review_id)
        tool_results = {t.tool_result_id: t for t in self.reviews.tool_results(review_id)}

        dec_by_f: dict[str, list[dict[str, Any]]] = {}
        for d in decisions:
            dec_by_f.setdefault(d.finding_id, []).append(d.model_dump(mode="json"))
        reb_by_f: dict[str, list[dict[str, Any]]] = {}
        for r in rebuttals:
            reb_by_f.setdefault(r.finding_id, []).append(r.model_dump(mode="json"))
        rf: list[ReportFinding] = []
        for f in findings:
            rf.append(ReportFinding(
                finding_id=f.finding_id, domain=f.agent_id, title=f.title, severity=f.severity.value, evidence_level=f.evidence_level.value,
                status=f.status.value, category=f.category, description=f.description, scenario=f.scenario, impact=f.impact,
                recommendation=f.recommendation, principle=f.principle, principle_origin=f.principle_origin.value,
                validation_method=f.validation_method, affected_components=f.affected_components, source_references=f.source_references,
                tool_results=f.tool_result_refs, contributing_agents=f.contributing_agents, moderator_status=f.moderator_status.value,
                moderator_notes=f.moderator_notes, assumptions=f.assumptions, architect_decisions=dec_by_f.get(f.finding_id, []),
                rebuttals=reb_by_f.get(f.finding_id, []), remaining_risk=self._remaining_risk(f), simulated=f.simulated,
                first_seen_revision=f.first_seen_revision, last_seen_revision=f.last_seen_revision,
                history=[h.model_dump(mode="json") for h in f.history],
            ))
        visible = [f for f in findings if f.moderator_status.value != "REJECTED"]
        open_f = [f for f in visible if f.status.value not in CLOSED_STATUSES]
        major = [f for f in open_f if f.severity.value in ("CRITICAL", "HIGH")][:6]
        validation = [f for f in visible if f.evidence_level.value == "NEEDS_VALIDATION" and f.status.value not in CLOSED_STATUSES]
        clar = list(model.unresolved) if model else []
        clar += [f"Validate: {f.title}" for f in validation[:6]]
        for d in score.domains:
            clar += [f"{d.label}: {g}" for g in d.information_gaps[:3]]
        recs: list[str] = []
        for f in open_f[:12]:
            line = f"[{f.severity.value}] {f.title}: {f.recommendation.splitlines()[0][:220]}"
            if line not in recs:
                recs.append(line)
        agents_cov = [{
            "agent_id": e.agent_id, "label": AGENT_LABELS.get(e.agent_id, e.agent_id), "round": e.round, "status": e.status.value,
            "findings": e.finding_count, "tool_count": e.tool_count, "tool_violations": e.tool_violations, "error": e.error,
            "information_gaps": e.information_gaps,
        } for e in executions]
        tool_events = [ev for ev in events if ev.event_type == "tool_executed"]
        tools_cov: dict[str, dict[str, Any]] = {}
        for ev in tool_events:
            p = ev.payload
            tools_cov[p.get("tool_id", "?")] = {"tool_id": p.get("tool_id"), "tool_name": p.get("tool_name"), "status": p.get("status"),
                                                "detail": p.get("detail", ""), "violations": p.get("violations", 0)}
        failed = [e for e in executions if e.status.value == "FAILED"]
        coverage_txt = (f"{len({e.agent_id for e in executions if e.status.value == 'COMPLETED'})} of {len(review.enabled_agents)} selected specialist agents "
                        f"completed; {len(failed)} failed (partial analysis flagged)." if executions else "No agent executions recorded.")
        status_text = {
            ReviewStatus.COMPLETED.value: "Completed - no unresolved Critical/High findings remain. Human architect sign-off is still required.",
            ReviewStatus.COMPLETED_WITH_OPEN_RISKS.value: "Completed with OPEN RISKS - unresolved Critical/High findings remain. This is not an approval.",
        }.get(review.status.value, f"In progress ({review.status.value}).")
        narrative = (
            f"{len(visible)} distinct finding(s) were produced for '{review.name}' ({len(open_f)} still open, "
            f"{sum(1 for f in open_f if f.severity.value in ('CRITICAL', 'HIGH'))} Critical/High). {score.overall_statement} "
            f"{len(decisions)} architect decision(s) and {len(rebuttals)} critic rebuttal(s) are recorded."
        )
        history = {
            "rounds": review.current_round, "max_rounds": review.max_rounds, "event_count": len(events),
            "revisions": [{
                "number": r.number, "source": r.source, "created_at": r.created_at.isoformat(), "change_notes": r.change_notes,
                "impact_summary": r.impact.summary if r.impact else "", "affected_agents": r.impact.affected_agents if r.impact else [],
                "confidence": r.impact.confidence if r.impact else None, "artifact_ids": r.artifact_ids,
            } for r in revisions],
            "timeline": [{"seq": e.seq, "time": e.timestamp.isoformat(), "type": e.event_type, "agent": e.agent_id, "message": e.message}
                         for e in events if e.event_type not in ("tool_executed",)][-120:],
        }
        trace = []
        for f in findings:
            trace.append({
                "finding_id": f.finding_id,
                "source_documents": sorted({r.filename for r in f.source_references if r.filename}),
                "components": f.affected_components, "agents": f.contributing_agents,
                "tool_results": [{"id": t, "rule_id": tool_results[t].rule_id} for t in f.tool_result_refs if t in tool_results],
                "moderator": {"status": f.moderator_status.value, "notes": f.moderator_notes},
                "human_decisions": [d["decision_type"] for d in dec_by_f.get(f.finding_id, [])],
                "adrs": [a.adr_id for a in adrs if f.finding_id in a.related_finding_ids],
            })
        evaluation = self.evaluation.evaluate(review_id)
        return AssessmentReport(
            review_id=review_id, project_name=project.name if project else "", architecture_name=review.name,
            review_status=review.status.value, architecture_revision=review.architecture_revision, simulated=review.simulated,
            provider=review.provider, model_id=review.model_id,
            executive_summary={
                "review_date": utcnow().date().isoformat(), "overall_assessment": status_text, "review_coverage": coverage_txt,
                "narrative": narrative, "major_risks": [{"finding_id": f.finding_id, "title": f.title, "severity": f.severity.value,
                                                         "status": f.status.value} for f in major],
                "clarifications": clar, "recommendations": recs,
                "counts": {"findings": len(visible), "open": len(open_f), "rejected": len(findings) - len(visible)},
            },
            scorecard=score, findings=rf, decisions=[d.model_dump(mode="json") for d in decisions],
            adrs=[{"adr_id": a.adr_id, "number": a.number, "title": a.title, "status": a.status, "owner": a.owner,
                   "related_finding_ids": a.related_finding_ids, "path": a.path} for a in adrs],
            review_history=history,
            coverage={"agents": agents_cov, "tools": list(tools_cov.values()), "documents": len(model.source_artifact_ids) if model else 0,
                      "extraction_notes": [n.message for n in (model.notes if model else [])]},
            architecture={
                "title": model.title if model else review.name, "environment": model.environment if model else "Unknown",
                "components": [{"id": c.component_id, "name": c.name, "type": c.type, "provider": c.provider,
                                "sources": [r.short() for r in c.source_references[:3]]} for c in (model.components if model else [])],
                "relationships": [{"source": r.source, "target": r.target, "type": r.relationship_type, "label": r.label}
                                  for r in (model.relationships if model else [])],
                "nfrs": [{"category": n.category, "statement": n.statement} for n in (model.nfrs if model else [])],
                "unresolved": model.unresolved if model else [],
            },
            traceability=trace, evaluation=evaluation,
            limitations=LIMITATIONS + (["SIMULATED DEMO MODE: findings come from fixtures and deterministic mock responses."] if review.simulated else []),
            disclaimers=DISCLAIMERS,
        )

    # ------------------------------------------------------------------ files
    def generate(self, review_id: str, formats: tuple[str, ...] = ("json", "pdf")) -> list[ReportArtifact]:
        review = self.reviews.get(review_id)
        report = self.build(review_id)
        folder = self.paths.review_report_dir(review_id)
        stamp = utcnow().strftime("%Y%m%dT%H%M%S")
        out: list[ReportArtifact] = []
        base = f"architecture-review-{review_id}-rev{review.architecture_revision}-{stamp}"
        for fmt in formats:
            path = folder / f"{base}.{fmt}"
            if fmt == "json":
                size = write_json_report(report, path)
            elif fmt == "pdf":
                size = build_pdf(report, path)
            else:
                continue
            art = ReportArtifact(review_id=review_id, format=fmt, path=str(path), filename=path.name, size_bytes=size,
                                 revision=review.architecture_revision)
            self.tables.reports.put(art)
            out.append(art)
        self.reviews.emit(review_id, "report_generated", f"Report generated ({', '.join(a.format.upper() for a in out)}).",
                          payload={"report_ids": [a.report_id for a in out]})
        return out

    def list(self, review_id: str) -> list[ReportArtifact]:
        return sorted(self.tables.reports.list(review_id=review_id), key=lambda r: r.created_at, reverse=True)

    def get(self, report_id: str) -> ReportArtifact:
        r = self.tables.reports.get(report_id)
        if r is None:
            raise NotFoundError(f"Report '{report_id}' not found.")
        return r


__all__ = ["ReportService", "Review"]
