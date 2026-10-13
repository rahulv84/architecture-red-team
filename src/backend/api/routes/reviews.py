"""Review lifecycle, progress, architecture and analytics endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...graph.builder import graph_topology
from ...models.domain import ALL_AGENT_IDS, AgentExecution, Review, Scorecard
from ...models.schemas import AgentExecutionsView, ReviewCreate, ReviewStatusView
from ..deps import Container

router = APIRouter(tags=["reviews"])


@router.post("/reviews", status_code=201)
def create_review(payload: ReviewCreate, c: Container) -> Review:
    return c.reviews.create_review(payload)


@router.get("/reviews")
def list_reviews(c: Container, project_id: str | None = None) -> list[dict[str, Any]]:
    out = []
    for r in c.reviews.list(project_id):
        findings = c.reviews.findings(r.review_id)
        open_ch = sum(1 for f in findings if f.severity.value in ("CRITICAL", "HIGH") and f.status.value not in ("REJECTED", "RESOLVED", "VERIFIED")
                      and f.moderator_status.value != "REJECTED")
        domain_counts: dict[str, int] = {a: 0 for a in ALL_AGENT_IDS}
        for f in findings:
            if f.moderator_status.value != "REJECTED" and f.status.value not in ("REJECTED", "RESOLVED", "VERIFIED"):
                domain_counts[f.agent_id] = domain_counts.get(f.agent_id, 0) + 1
        out.append({**r.model_dump(mode="json"), "simulated": r.simulated, "finding_count": len(findings), "open_critical_high": open_ch,
                    "open_by_domain": domain_counts,
                    "severity_counts": {s: sum(1 for f in findings if f.severity.value == s and f.moderator_status.value != "REJECTED")
                                        for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")}})
    return out


@router.get("/reviews/{review_id}")
def get_review(review_id: str, c: Container) -> dict[str, Any]:
    r = c.reviews.get(review_id)
    project = c.tables.projects.get(r.project_id)
    return {**r.model_dump(mode="json"), "simulated": r.simulated, "project_name": project.name if project else None,
            "documents": [a.model_dump(mode="json") for a in c.ingestion.list_for_project(r.project_id)]}


@router.post("/reviews/{review_id}/start", status_code=202)
def start_review(review_id: str, c: Container) -> dict[str, Any]:
    return c.workflow.start(review_id)


@router.post("/reviews/{review_id}/cancel")
def cancel_review(review_id: str, c: Container) -> dict[str, Any]:
    return c.workflow.cancel(review_id)


@router.get("/reviews/{review_id}/status")
def review_status(review_id: str, c: Container) -> ReviewStatusView:
    return c.reviews.status_view(review_id)


@router.get("/reviews/{review_id}/events")
def review_events(review_id: str, c: Container, after: int = 0, limit: int = 500) -> dict[str, Any]:
    events = c.reviews.events(review_id, after=after, limit=max(1, min(limit, 2000)))
    return {"events": [e.model_dump(mode="json") for e in events], "last_seq": events[-1].seq if events else after}


@router.get("/reviews/{review_id}/agents")
def review_agents(review_id: str, c: Container, round: int | None = None) -> AgentExecutionsView:
    review = c.reviews.get(review_id)
    execs: list[AgentExecution] = c.reviews.executions(review_id, round)
    tool_events = [e for e in c.tables.list_events(review_id, limit=5000) if e.event_type == "tool_executed"]
    from ...models.domain import ToolRunSummary

    summaries: dict[str, ToolRunSummary] = {}
    for e in tool_events:
        p = e.payload
        summaries[p["tool_id"]] = ToolRunSummary(tool_id=p["tool_id"], tool_name=p["tool_name"], status=p["status"],
                                                 detail=p.get("detail", ""), resources_checked=p.get("resources_checked", 0),
                                                 violations=p.get("violations", 0))
    return AgentExecutionsView(executions=execs, tool_summaries=list(summaries.values()),
                               tool_results=c.reviews.tool_results(review_id, review.architecture_revision) or c.reviews.tool_results(review_id))


@router.get("/reviews/{review_id}/architecture")
def review_architecture(review_id: str, c: Container, revision: int | None = None) -> dict[str, Any]:
    review = c.reviews.get(review_id)
    model = c.reviews.get_model(review_id, revision or review.architecture_revision) or c.reviews.get_model(review_id)
    if model is None:
        return {"available": False, "model": None, "documents": []}
    docs = {a.artifact_id: a for a in c.ingestion.list_for_project(review.project_id, active_only=False)}
    return {
        "available": True, "revision": model.revision, "model": model.model_dump(mode="json"),
        "documents": [docs[i].model_dump(mode="json") for i in model.source_artifact_ids if i in docs],
        "revisions": [r.number for r in c.reviews.revisions(review_id)],
    }


@router.get("/reviews/{review_id}/score")
def review_score(review_id: str, c: Container) -> Scorecard:
    review = c.reviews.get(review_id)
    return c.scoring.scorecard(review, c.reviews.findings(review_id), c.reviews.executions(review_id))


@router.get("/reviews/{review_id}/evaluation")
def review_evaluation(review_id: str, c: Container) -> dict[str, Any]:
    return c.evaluation.evaluate(review_id)


@router.get("/reviews/{review_id}/graph")
def review_graph(review_id: str, c: Container) -> dict[str, Any]:
    c.reviews.get(review_id)
    topo = graph_topology(c.executor.graph)
    values = {}
    try:
        values = c.executor.state_values(review_id)
    except Exception:  # state may not exist before the first run
        values = {}
    snap_next: list[str] = []
    try:
        snap_next = list(c.executor.snapshot(review_id).next)
    except Exception:
        snap_next = []
    return {**topo, "next_nodes": snap_next, "workflow_status": values.get("workflow_status"),
            "interrupted": bool(c.executor.is_interrupted(review_id)) if values else False,
            "recovery_note": c.reviews.get(review_id).recovery_note}
