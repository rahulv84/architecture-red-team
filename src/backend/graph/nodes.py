"""LangGraph node implementations (main review graph and the reusable specialist subgraph).

Nodes are thin orchestration glue: they call services via the process-wide container, emit
execution events and return partial state updates.  Code that precedes ``interrupt()`` in
``human_review`` is side-effect free because LangGraph re-executes the node on resume.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from ..agents.moderator_agent import ModeratorAgent
from ..agents.rebuttal import request_rebuttal
from ..core.exceptions import ProviderConfigError, ReviewCancelled, ValidationFailed, describe_error
from ..core.lifecycle import get_container
from ..models.architecture import ArchitectureModel
from ..models.domain import (
    ALL_AGENT_IDS,
    CLOSED_STATUSES,
    AgentStatus,
    DecisionType,
    ExchangeEntry,
    Finding,
    FindingStatus,
    ModeratorStatus,
    Rebuttal,
    ReviewStatus,
    Severity,
    UsageRecord,
    utcnow,
)
from ..services.decision_service import apply_decisions
from ..services.tool_service import build_tool_context, run_builtin
from ..tools import CHECKOV_AGENTS
from .state import ReviewState, SpecialistState

log = logging.getLogger(__name__)


def _stage_event(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "message": message, "at": utcnow().isoformat(), **extra}


def _check_cancel(review_id: str) -> None:
    if get_container().reviews.is_cancelled(review_id):
        raise ReviewCancelled("Review was cancelled by the user.")


def _load_docs(state: ReviewState | SpecialistState) -> list[Any]:
    c = get_container()
    ids = [a["artifact_id"] for a in state.get("source_artifacts", [])]
    return c.review_cache.docs(state["review_id"], state.get("architecture_revision", state.get("revision", 1)), ids)


# =========================================================================== main graph
def validate_input(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    review = c.reviews.get(rid)
    c.reviews.clear_cancel(rid)
    c.reviews.set_status(rid, ReviewStatus.EXTRACTING, "Validating input", round_no=1, error="")
    c.reviews.emit(rid, "review_started", f"Review started ({'SIMULATED DEMO' if review.simulated else 'live'}; provider {review.provider}).")
    revs = c.reviews.revisions(rid)
    current = next((r for r in revs if r.number == review.architecture_revision), None)
    arts = []
    for aid in (current.artifact_ids if current else []):
        art = c.tables.artifacts.get(aid)
        if art and art.parse_status in ("PARSED", "PARTIAL") and art.active is not None:
            arts.append(art)
    if not arts:
        raise ValidationFailed("The review has no parsable architecture documents. Upload at least one supported file.")
    if not review.enabled_agents:
        raise ValidationFailed("No specialist agents are enabled.")
    provider = c.provider_for(review)
    if review.mode == "live":
        v = provider.validate_configuration()
        if not v.ok:
            raise ProviderConfigError(f"Provider '{review.provider}' is not usable: {v.message}")
    manifest = [{"artifact_id": a.artifact_id, "filename": a.filename, "sha256": a.sha256, "role": a.role.value,
                 "detected_type": a.detected_type} for a in arts]
    return {
        "source_artifacts": manifest, "current_round": 1, "workflow_status": ReviewStatus.EXTRACTING.value,
        "maximum_rounds": review.max_rounds, "enabled_agents": review.enabled_agents, "architecture_revision": review.architecture_revision,
        "execution_events": [_stage_event("input_validated", f"{len(arts)} document(s) accepted.")],
    }


def parse_documents(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    c.reviews.set_status(rid, ReviewStatus.EXTRACTING, "Parsing documents")
    docs = _load_docs(state)
    for art, parsed in docs:
        c.reviews.emit(
            rid, "document_parsed",
            f"{art.filename}: {parsed.kind}, {len(parsed.resources)} resource(s), {len(parsed.nodes)} diagram node(s)"
            + (f", {len(parsed.warnings)} warning(s)" if parsed.warnings else ""),
            level="WARNING" if parsed.warnings else "INFO",
            payload={"artifact_id": art.artifact_id, "kind": parsed.kind, "warnings": parsed.warnings},
        )
    if not docs:
        raise ValidationFailed("No document could be loaded for analysis.")
    return {"execution_events": [_stage_event("documents_parsed", f"{len(docs)} document(s) parsed.")]}


def extract_architecture(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    review = c.reviews.get(rid)
    c.reviews.set_status(rid, ReviewStatus.EXTRACTING, "Extracting architecture model")
    docs = _load_docs(state)
    provider = c.provider_for(review)
    model = c.architecture.build_model(
        title=review.name, description=review.description, environment_hint=review.cloud_environment, docs=docs,
        provider=provider, revision=state.get("architecture_revision", 1), use_llm=review.mode == "live",
    )
    c.reviews.save_model(rid, model)
    ctx = build_tool_context(rid, model, docs)
    results, _ = run_builtin(ctx)
    c.reviews.save_tool_results(rid, model.revision, results)  # baseline for later revision diffs
    s = model.summary_dict()
    c.reviews.emit(rid, "architecture_extracted",
                   f"Architecture model extracted: {s['components']} components, {s['relationships']} relationships, "
                   f"{s['nfrs']} requirements, {s['unresolved']} unresolved item(s).", payload=s)
    for note in model.notes:
        if note.level == "LIMITATION":
            c.reviews.emit(rid, "extraction_limitation", note.message, level="WARNING")
    return {"architecture_model": model.model_dump(mode="json"),
            "execution_events": [_stage_event("architecture_extracted", f"{s['components']} components extracted")]}


def retrieve_context(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    docs = _load_docs(state)
    model = ArchitectureModel.model_validate(state["architecture_model"])
    index = c.review_cache.index(rid, state.get("architecture_revision", 1), docs, c.context)
    evidence: dict[str, list[dict[str, Any]]] = {}
    from ..agents import AGENT_CLASSES

    total = 0
    for agent_id, cls in AGENT_CLASSES.items():
        if agent_id not in state.get("enabled_agents", ALL_AGENT_IDS):
            continue
        query = f"{cls.query_keywords} {model.environment} " + " ".join(f"{x.type} {x.name}" for x in model.components[:25])
        hits = index.search(query, k=6, domains={agent_id})
        evidence[agent_id] = [h.model_dump() for h in hits]
        total += len(hits)
    c.reviews.emit(rid, "context_retrieved", f"Retrieved {total} context chunk(s) from local standards and supplied context documents.")
    return {"contextual_evidence": evidence, "execution_events": [_stage_event("context_retrieved", f"{total} chunks")]}


def select_critics(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    review = c.reviews.get(rid)
    round_no = state.get("current_round", 1)
    enabled = [a for a in ALL_AGENT_IDS if a in state.get("enabled_agents", ALL_AGENT_IDS)]
    selected = [a for a in ALL_AGENT_IDS if a in (state.get("rerun_agents") or enabled) and a in enabled]
    revision = state.get("architecture_revision", 1)
    for a in ALL_AGENT_IDS:
        if a in selected:
            continue
        reason = "Not selected for this review" if a not in enabled else f"Unaffected by architecture revision {revision}; not re-executed"
        c.reviews.mark_skipped(rid, a, round_no, revision, reason, simulated=review.simulated)
    status = ReviewStatus.RE_REVIEWING if round_no > 1 else ReviewStatus.ANALYZING
    c.reviews.set_status(rid, status, f"Running {len(selected)} specialist critic(s) in parallel", round_no=round_no)
    c.reviews.emit(rid, "critics_selected", f"Round {round_no}: executing {', '.join(selected)}"
                   + (f" (skipped: {', '.join(a for a in enabled if a not in selected)})" if round_no > 1 else ""),
                   round_no=round_no, payload={"selected": selected})
    return {"rerun_agents": selected, "workflow_status": status.value,
            "execution_events": [_stage_event("critics_selected", ",".join(selected), round=round_no)]}


def make_agent_node(agent_id: str, specialist_graph: Any):
    """Create the node function for one critic; each wraps the shared specialist subgraph."""

    def node(state: dict[str, Any]) -> dict[str, Any]:
        c = get_container()
        rid = state["review_id"]
        round_no = state.get("current_round", 1)
        revision = state.get("architecture_revision", 1)
        review = c.reviews.get(rid)
        ex = c.reviews.begin_agent(rid, agent_id, round_no, revision, review.simulated)
        c.reviews.emit(rid, "agent_started", f"{agent_id} critic started.", agent_id=agent_id, execution_id=ex.execution_id, round_no=round_no)
        prior = [{"title": f["title"], "status": f["status"]} for f in state.get("moderated_findings", []) if f.get("agent_id") == agent_id]
        try:
            _check_cancel(rid)
            out = specialist_graph.invoke({
                "review_id": rid, "agent_id": agent_id, "execution_id": ex.execution_id, "round": round_no, "revision": revision,
                "mode": review.mode, "provider": review.provider, "scenario_id": review.scenario_id,
                "architecture_model": state["architecture_model"], "source_artifacts": state["source_artifacts"],
                "contextual_evidence": (state.get("contextual_evidence") or {}).get(agent_id, []),
                "prior_findings": prior, "change_notes": state.get("change_notes", ""),
            })
        except ReviewCancelled as exc:
            c.reviews.update_execution(ex.execution_id, status=AgentStatus.FAILED, error="Cancelled", completed_at=utcnow())
            c.reviews.emit(rid, "agent_failed", f"{agent_id} cancelled.", level="WARNING", agent_id=agent_id,
                           execution_id=ex.execution_id, round_no=round_no)
            raise exc
        except Exception as exc:  # one critic failing must not erase the others' work
            msg = describe_error(exc)
            log.warning("Agent %s failed: %s", agent_id, msg)
            c.reviews.update_execution(ex.execution_id, status=AgentStatus.FAILED, error=msg, completed_at=utcnow())
            c.reviews.emit(rid, "agent_failed", f"{agent_id} critic failed: {msg}", level="ERROR", agent_id=agent_id,
                           execution_id=ex.execution_id, round_no=round_no)
            return {"agent_results": [{"agent_id": agent_id, "round": round_no, "status": "FAILED", "error": msg}],
                    "errors": [{"agent_id": agent_id, "round": round_no, "error": msg}]}
        findings = out.get("findings", [])
        tool_results = out.get("tool_results", [])
        usage = out.get("usage")
        c.reviews.update_execution(
            ex.execution_id, status=AgentStatus.COMPLETED, completed_at=utcnow(), finding_count=len(findings),
            tool_count=len(tool_results), tool_violations=sum(1 for t in tool_results if t.get("outcome") == "VIOLATION"),
            information_gaps=out.get("information_gaps", []), coverage_summary=out.get("coverage_summary", ""),
            warnings=out.get("warnings", []), usage=UsageRecord.model_validate(usage) if usage else None,
        )
        c.reviews.emit(rid, "agent_completed", f"{agent_id} critic completed with {len(findings)} proposed finding(s).",
                       agent_id=agent_id, execution_id=ex.execution_id, round_no=round_no,
                       payload={"findings": len(findings), "tools": len(tool_results)})
        for f in findings:
            f["_round"] = round_no
        return {
            "agent_results": [{"agent_id": agent_id, "round": round_no, "status": "COMPLETED", "findings": len(findings)}],
            "candidate_findings": findings, "tool_results": tool_results,
        }

    return node


def moderator(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    review = c.reviews.get(rid)
    round_no = state.get("current_round", 1)
    revision = state.get("architecture_revision", 1)
    results = [r for r in state.get("agent_results", []) if r.get("round") == round_no]
    failed = {r["agent_id"] for r in results if r["status"] == "FAILED"}
    if results and len(failed) == len(results):
        raise RuntimeError("All specialist agents failed: " + "; ".join(f"{r['agent_id']}: {r.get('error', '')}" for r in results)[:600])
    c.reviews.set_status(rid, ReviewStatus.MODERATING, "Moderating findings", round_no=round_no)
    candidates = []
    for d in state.get("candidate_findings", []):
        if d.get("_round") == round_no:
            clean = {k: v for k, v in d.items() if not k.startswith("_")}
            candidates.append(Finding.model_validate(clean))
    previous = [Finding.model_validate(d) for d in state.get("moderated_findings", [])]
    model = ArchitectureModel.model_validate(state["architecture_model"])
    mod = ModeratorAgent(c.provider_for(review))
    rerun = set(state.get("rerun_agents") or []) if round_no > 1 else None
    known = {r.address for _, d in _load_docs(state) for r in d.resources}
    res = mod.moderate(
        review_id=rid, candidates=candidates, previous=previous, model=model,
        artifact_ids={a["artifact_id"] for a in state.get("source_artifacts", [])}, round_no=round_no, revision=revision,
        rerun_agents=rerun, failed_agents=failed, scenario_id=review.scenario_id, use_llm=True, known_resources=known,
    )
    for f in res.findings:
        f.simulated = f.simulated or review.simulated
    c.reviews.replace_findings(rid, res.findings)
    # tool results from this round (agents share deterministic ids)
    seen: dict[str, dict[str, Any]] = {}
    for t in state.get("tool_results", []):
        seen[t["tool_result_id"]] = t
    if failed:
        c.reviews.emit(rid, "partial_analysis", f"Partial analysis: {', '.join(sorted(failed))} failed; other specialists' results were preserved.",
                       level="WARNING", round_no=round_no)
    for w in res.warnings:
        c.reviews.emit(rid, "moderator_warning", w, level="WARNING", round_no=round_no)
    c.reviews.emit(rid, "moderator_completed", res.summary, round_no=round_no,
                   payload={"diagnostics": res.diagnostics[:60], "conflicts": res.conflicts, "llm_used": res.llm_used})
    c.reviews.set_status(rid, ReviewStatus.AWAITING_HUMAN, "Awaiting architect decisions", round_no=round_no, awaiting_human=True)
    c.snapshots.save_review_snapshot(rid, f"moderated-r{round_no}")
    return {
        "moderated_findings": [f.model_dump(mode="json") for f in res.findings],
        "moderation": {"summary": res.summary, "diagnostics": res.diagnostics[:100], "conflicts": res.conflicts, "round": round_no},
        "rerun_agents": [], "workflow_status": ReviewStatus.AWAITING_HUMAN.value,
        "execution_events": [_stage_event("moderator_completed", res.summary, round=round_no)],
    }


def _pending_disputes(findings: list[Finding], decisions: list[dict[str, Any]], rebuttals: list[dict[str, Any]]) -> list[tuple[Finding, dict[str, Any]]]:
    answered = {r["dispute_decision_id"] for r in rebuttals}
    by_id = {f.finding_id: f for f in findings}
    out: list[tuple[Finding, dict[str, Any]]] = []
    for d in sorted(decisions, key=lambda x: x.get("created_at", "")):
        if d["decision_type"] == DecisionType.DISPUTE.value and d["decision_id"] not in answered:
            f = by_id.get(d["finding_id"])
            if f is not None and f.status == FindingStatus.DISPUTED:
                out.append((f, d))
    return out


def human_review(state: ReviewState) -> dict[str, Any]:
    """Human-in-the-loop boundary using a real LangGraph interrupt (idempotent before the pause)."""
    findings = [Finding.model_validate(d) for d in state.get("moderated_findings", [])]
    round_no = state.get("current_round", 1)
    open_n = sum(1 for f in findings if f.status.value not in CLOSED_STATUSES and f.moderator_status != ModeratorStatus.REJECTED)
    payload = {
        "kind": "human_review", "review_id": state["review_id"], "round": round_no, "revision": state.get("architecture_revision", 1),
        "open_findings": open_n, "total_findings": len(findings),
        "disputes_awaiting_rebuttal": len(_pending_disputes(findings, state.get("human_decisions", []), state.get("rebuttals", []))),
        "can_re_review": round_no < state.get("maximum_rounds", 3),
        "actions": ["submit_decisions", "re_review", "finalize"],
    }
    resume = interrupt(payload)  # execution pauses here until Command(resume=...) is supplied
    # ---- everything below runs exactly once per resume ----------------------------------
    c = get_container()
    rid = state["review_id"]
    resume = resume or {}
    action = resume.get("action", "submit_decisions")
    new_decisions = [d for d in resume.get("decisions", []) if d["decision_id"] not in {x["decision_id"] for x in state.get("human_decisions", [])}]
    max_disputes = c.settings_store.get().max_disputes_per_finding
    findings = apply_decisions(findings, new_decisions + [], max_disputes)
    all_decisions = state.get("human_decisions", []) + new_decisions
    disputes = _pending_disputes(findings, all_decisions, state.get("rebuttals", []))
    errors: list[dict[str, Any]] = []
    next_step = "human_review"
    if disputes:
        next_step = "rebuttal"
    elif action == "re_review":
        if not resume.get("revision_id"):
            errors.append({"error": "Re-review requested without a pending architecture revision."})
        elif round_no >= state.get("maximum_rounds", 3):
            errors.append({"error": f"Maximum review rounds ({state.get('maximum_rounds', 3)}) reached; finalize instead."})
        else:
            next_step = "apply_revision"
    elif action == "finalize":
        next_step = "finalize"
    for e in errors:
        c.reviews.emit(rid, "resume_rejected", e["error"], level="WARNING", round_no=round_no)
    c.reviews.replace_findings(rid, findings)
    c.decisions.mark_ids_submitted([d["decision_id"] for d in new_decisions])
    return {
        "moderated_findings": [f.model_dump(mode="json") for f in findings], "human_decisions": new_decisions,
        "next_step": next_step, "last_action": action, "revision_id": resume.get("revision_id"),
        "change_notes": resume.get("notes", ""), "errors": errors,
        "execution_events": [_stage_event("human_resumed", f"action={action}, decisions={len(new_decisions)}", round=round_no)],
    }


def rebuttal(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    review = c.reviews.get(rid)
    c.reviews.set_status(rid, ReviewStatus.REBUTTING, "Critics preparing rebuttals", awaiting_human=False)
    findings = [Finding.model_validate(d) for d in state.get("moderated_findings", [])]
    by_id = {f.finding_id: f for f in findings}
    model = ArchitectureModel.model_validate(state["architecture_model"])
    tool_results = c.reviews.tool_results(rid, state.get("architecture_revision", 1)) or c.reviews.tool_results(rid)
    provider = c.provider_for(review)
    moderator_agent = ModeratorAgent(provider)
    round_no = state.get("current_round", 1)
    out_rebuttals: list[dict[str, Any]] = []
    from ..agents import AGENT_CLASSES

    for finding, decision in _pending_disputes(findings, state.get("human_decisions", []), state.get("rebuttals", [])):
        f = by_id[finding.finding_id]
        agent_id = f.agent_id if f.agent_id in AGENT_CLASSES else f.contributing_agents[0]
        agent = AGENT_CLASSES[agent_id](provider, c.context)
        reb = Rebuttal(review_id=rid, finding_id=f.finding_id, dispute_decision_id=decision["decision_id"], agent_id=agent_id,
                       round=round_no, simulated=review.simulated)
        reb.exchange.append(ExchangeEntry(role="ARCHITECT", actor=decision.get("actor", "architect"), content=decision.get("justification", "")))
        c.reviews.emit(rid, "rebuttal_requested", f"{agent_id} critic asked to rebut dispute of '{f.title[:60]}'.", agent_id=agent_id, round_no=round_no)
        prior = [e.content for e in reb.exchange]
        try:
            out, refs, usage, valid = request_rebuttal(agent, f, decision.get("justification", ""), model, tool_results, review.scenario_id, prior)
            det = moderator_agent.adjudicate(f, out, valid)
            reb.stance, reb.argument, reb.evidence_refs = out.stance.upper(), out.argument, refs
            reb.revised_severity = out.revised_severity
            reb.exchange.append(ExchangeEntry(role="CRITIC", actor=agent_id, content=out.argument, stance=reb.stance, evidence_refs=refs))
            reb.moderator_determination, reb.outcome = det["text"], det["outcome"]
            reb.exchange.append(ExchangeEntry(role="MODERATOR", actor="moderator", content=det["text"], stance=det["outcome"]))
            f.status = det["status"]
            if det["severity"] != f.severity:
                f.add_history("SEVERITY_ADJUSTED", "Moderator", f"{f.severity.value} -> {det['severity'].value} after rebuttal.", round=round_no)
                f.severity = det["severity"]
            f.evidence_level = det["evidence_level"]
            f.add_history("REBUTTAL", agent_id, out.argument, round=round_no, data={"rebuttal_id": reb.rebuttal_id, "stance": reb.stance})
            f.add_history("MODERATOR_DETERMINATION", "Moderator", det["text"], round=round_no,
                          data={"rebuttal_id": reb.rebuttal_id, "outcome": det["outcome"]})
            if det["outcome"] == "DISPUTE_UPHELD":
                f.moderator_status = ModeratorStatus.REJECTED
                c.decisions.ensure_adr(review, f, kind="DISPUTE_RESOLUTION", determination=det["text"])
            c.reviews.emit(rid, "rebuttal_received", f"{agent_id}: {reb.stance} - {out.argument[:120]}", agent_id=agent_id, round_no=round_no)
            c.reviews.emit(rid, "moderator_determination", f"{det['outcome']} for '{f.title[:60]}'.", round_no=round_no)
        except Exception as exc:  # keep the dispute open and report the failure
            msg = describe_error(exc)
            reb.outcome = "UNAVAILABLE"
            reb.moderator_determination = f"Rebuttal could not be produced: {msg}. The finding remains DISPUTED; dispute again or accept/defer."
            reb.exchange.append(ExchangeEntry(role="MODERATOR", actor="moderator", content=reb.moderator_determination))
            c.reviews.emit(rid, "rebuttal_failed", reb.moderator_determination, level="ERROR", agent_id=agent_id, round_no=round_no)
        c.tables.rebuttals.put(reb)
        out_rebuttals.append(reb.model_dump(mode="json"))
    c.reviews.replace_findings(rid, findings)
    c.snapshots.save_review_snapshot(rid, f"rebuttal-r{round_no}")
    return {"moderated_findings": [f.model_dump(mode="json") for f in findings], "rebuttals": out_rebuttals, "next_step": "human_review",
            "execution_events": [_stage_event("rebuttals_completed", f"{len(out_rebuttals)} rebuttal(s)", round=round_no)]}


def apply_revision(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    rev = c.tables.revisions.get(state.get("revision_id") or "")
    if rev is None or rev.review_id != rid:
        raise ValidationFailed("The requested architecture revision does not exist.")
    model = c.reviews.get_model(rid, rev.number)
    if model is None:
        raise ValidationFailed(f"No architecture model stored for revision {rev.number}.")
    impact = rev.impact.model_dump(mode="json") if rev.impact else {}
    review = c.reviews.get(rid)
    affected = [a for a in (rev.impact.affected_agents if rev.impact else []) if a in review.enabled_agents] or list(review.enabled_agents)
    manifest = []
    for aid in rev.artifact_ids:
        art = c.tables.artifacts.get(aid)
        if art:
            manifest.append({"artifact_id": art.artifact_id, "filename": art.filename, "sha256": art.sha256, "role": art.role.value,
                             "detected_type": art.detected_type})
    new_round = state.get("current_round", 1) + 1
    c.review_cache.invalidate(rid)
    c.reviews.set_status(rid, ReviewStatus.RE_REVIEWING, f"Applying revision {rev.number}", round_no=new_round, revision=rev.number,
                         awaiting_human=False)
    c.reviews.emit(rid, "re_review_started", f"Re-review round {new_round} for revision {rev.number}; agents: {', '.join(affected)}.",
                   round_no=new_round, payload={"impact": impact})
    return {
        "architecture_model": model.model_dump(mode="json"), "architecture_revision": rev.number, "source_artifacts": manifest,
        "change_impact": impact, "rerun_agents": affected, "current_round": new_round, "workflow_status": ReviewStatus.RE_REVIEWING.value,
        "change_notes": rev.change_notes,
        "execution_events": [_stage_event("revision_applied", f"revision {rev.number}", round=new_round)],
    }


def finalize(state: ReviewState) -> dict[str, Any]:
    c = get_container()
    rid = state["review_id"]
    review = c.reviews.get(rid)
    findings = [Finding.model_validate(d) for d in state.get("moderated_findings", [])]
    open_ch = [
        f for f in findings
        if f.severity in (Severity.CRITICAL, Severity.HIGH) and f.status.value not in CLOSED_STATUSES
        and f.moderator_status != ModeratorStatus.REJECTED
    ]
    status = ReviewStatus.COMPLETED if not open_ch else ReviewStatus.COMPLETED_WITH_OPEN_RISKS
    c.decisions.ensure_review_adrs(review, findings)
    c.reviews.set_status(rid, status, "Completed", round_no=state.get("current_round", 1))
    c.reviews.emit(rid, "review_completed",
                   f"Review finalized: {status.value}. {len(open_ch)} unresolved Critical/High finding(s). "
                   "Architecture approval remains a human decision.", level="WARNING" if open_ch else "INFO")
    report_ids: list[str] = []
    try:
        report_ids = [r.report_id for r in c.reports.generate(rid)]
    except Exception as exc:  # report failure is surfaced, review result is already persisted
        c.reviews.emit(rid, "report_failed", f"Report generation failed: {describe_error(exc)}", level="ERROR")
        log.exception("Report generation failed for %s", rid)
    c.snapshots.save_review_snapshot(rid, "final")
    return {"workflow_status": status.value, "final_report": {"status": status.value, "open_critical_high": len(open_ch), "report_ids": report_ids},
            "execution_events": [_stage_event("review_completed", status.value)]}


# =========================================================================== specialist subgraph
def sp_select_rules(state: SpecialistState) -> dict[str, Any]:
    c = get_container()
    agent = c.agent_for(state["review_id"], state["agent_id"])
    c.reviews.emit(state["review_id"], "rules_selected", f"{state['agent_id']}: {len(agent.rule_ids)} deterministic rule(s) selected.",
                   agent_id=state["agent_id"], execution_id=state.get("execution_id"), round_no=state.get("round"))
    return {"rule_ids": agent.rule_ids}


def sp_run_tools(state: SpecialistState) -> dict[str, Any]:
    c = get_container()
    rid, agent_id = state["review_id"], state["agent_id"]
    _check_cancel(rid)
    model = ArchitectureModel.model_validate(state["architecture_model"])
    docs = c.review_cache.docs(rid, state["revision"], [a["artifact_id"] for a in state["source_artifacts"]])
    ctx = build_tool_context(rid, model, docs)
    results, summaries = run_builtin(ctx, state.get("rule_ids"))
    if agent_id in CHECKOV_AGENTS:
        cx_results, cx_summary = c.review_cache.checkov(rid, state["revision"], docs, c.settings_store.get())
        results = results + cx_results
        summaries = summaries + [cx_summary]
    c.reviews.save_tool_results(rid, state["revision"], results)
    for s in summaries:
        lvl = "WARNING" if s.status != "EXECUTED" else "INFO"
        c.reviews.emit(rid, "tool_executed",
                       f"{s.tool_name}: {s.status} - {s.violations} violation(s) over {s.resources_checked} resource(s). {s.detail}".strip(),
                       level=lvl, agent_id=agent_id, execution_id=state.get("execution_id"), round_no=state.get("round"),
                       payload={"tool_id": s.tool_id, "tool_name": s.tool_name, "status": s.status, "detail": s.detail,
                                "violations": s.violations, "resources_checked": s.resources_checked})
    return {"tool_results": [t.model_dump(mode="json") for t in results], "tool_summaries": [s.model_dump() for s in summaries]}


def sp_scope(state: SpecialistState) -> dict[str, Any]:
    from ..models.domain import ToolResult

    c = get_container()
    agent = c.agent_for(state["review_id"], state["agent_id"])
    model = ArchitectureModel.model_validate(state["architecture_model"])
    tools = [ToolResult.model_validate(t) for t in state.get("tool_results", [])]
    view = agent.scope(model, tools)
    c.reviews.emit(state["review_id"], "context_scoped", f"{state['agent_id']}: scoped to {len(view.components)} component(s), {len(view.relationships)} relationship(s).",
                   agent_id=state["agent_id"], execution_id=state.get("execution_id"), round_no=state.get("round"))
    return {"scope": {"components": [x.component_id for x in view.components], "brief": view.brief}}


def sp_retrieve(state: SpecialistState) -> dict[str, Any]:
    from ..agents.base_agent import doc_section_items
    from ..models.domain import ToolResult
    from ..services.context_service import KnowledgeChunk

    c = get_container()
    rid = state["review_id"]
    agent = c.agent_for(rid, state["agent_id"])
    model = ArchitectureModel.model_validate(state["architecture_model"])
    tools = [ToolResult.model_validate(t) for t in state.get("tool_results", [])]
    view = agent.scope(model, tools)
    docs = c.review_cache.docs(rid, state["revision"], [a["artifact_id"] for a in state["source_artifacts"]])
    index = c.review_cache.index(rid, state["revision"], docs, c.context)
    chunks = {h["chunk_id"]: KnowledgeChunk.model_validate(h) for h in state.get("contextual_evidence", [])}
    for h in agent.retrieve(index, model, view, tools):
        chunks.setdefault(h.chunk_id, h)
    roles = {a["artifact_id"]: a["role"] for a in state["source_artifacts"]}
    doc_items = doc_section_items(docs, agent.retrieval_query(model, view, tools), role_by_artifact=roles)
    catalog = agent.build_catalog(model, view, tools, list(chunks.values())[:8], doc_items)
    c.reviews.emit(rid, "evidence_assembled", f"{state['agent_id']}: evidence catalog of {len(catalog)} item(s) assembled.",
                   agent_id=state["agent_id"], execution_id=state.get("execution_id"), round_no=state.get("round"))
    return {"catalog": catalog}


def sp_analyze(state: SpecialistState) -> dict[str, Any]:
    from ..models.domain import ToolResult

    c = get_container()
    rid = state["review_id"]
    _check_cancel(rid)
    review = c.reviews.get(rid)
    agent = c.agent_for(rid, state["agent_id"])
    model = ArchitectureModel.model_validate(state["architecture_model"])
    tools = [ToolResult.model_validate(t) for t in state.get("tool_results", [])]
    view = agent.scope(model, tools)
    out, usage = agent.analyze(
        model, view, tools, state["catalog"], scenario_id=review.scenario_id, round_no=state["round"],
        prior=state.get("prior_findings"), revision_note=state.get("change_notes", ""),
    )
    return {"draft": out.model_dump(mode="json"), "usage": usage.model_dump(mode="json")}


def sp_validate(state: SpecialistState) -> dict[str, Any]:
    from ..models.domain import AgentReviewOutput, ToolResult

    c = get_container()
    rid = state["review_id"]
    agent = c.agent_for(rid, state["agent_id"])
    model = ArchitectureModel.model_validate(state["architecture_model"])
    tools = [ToolResult.model_validate(t) for t in state.get("tool_results", [])]
    output = AgentReviewOutput.model_validate(state["draft"])
    findings, warnings = agent.validate(
        output, review_id=rid, model=model, catalog=state["catalog"], tool_results=tools, revision=state["revision"],
        round_no=state["round"], simulated=agent.provider.capabilities().simulated,
    )
    for w in warnings:
        c.reviews.emit(rid, "validation_warning", w, level="WARNING", agent_id=state["agent_id"],
                       execution_id=state.get("execution_id"), round_no=state.get("round"))
    return {
        "findings": [f.model_dump(mode="json") for f in findings], "warnings": warnings, "information_gaps": output.information_gaps,
        "coverage_summary": output.coverage_summary,
    }
