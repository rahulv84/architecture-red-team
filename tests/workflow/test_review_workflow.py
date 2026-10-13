"""LangGraph workflow behaviour: parallel fan-out (7), moderation (8), interrupt/resume (9), decisions (10),
rebuttal routing (11), targeted re-review (12) and checkpoint recovery (14)."""
from __future__ import annotations

import json
import time

import pytest

from backend.core.lifecycle import AppContainer, set_container
from backend.models.domain import AgentStatus, DecisionType, FindingStatus, ReviewStatus
from backend.models.schemas import DecisionCreate
from tests.conftest import finding_by_title, make_container, start_demo_review, wait_idle


def decide(c: AppContainer, review_id: str, fragment: str, dtype: DecisionType, **kw):
    f = finding_by_title(c, review_id, fragment)
    return c.decisions.record(review_id, DecisionCreate(finding_id=f.finding_id, decision_type=dtype, **kw)), f


def test_six_agents_run_in_parallel_and_results_are_aggregated(tmp_path):
    c = make_container(tmp_path, max_concurrent_agents=6)
    c.settings_store.update({"max_concurrent_agents": 6})
    c.settings_store._settings = c.settings_store.get().model_copy(update={"mock_latency_seconds": 0.5})
    try:
        review = start_demo_review(c)
        assert review.status == ReviewStatus.AWAITING_HUMAN
        execs = c.reviews.executions(review.review_id, 1)
        assert {e.agent_id for e in execs} == {"security", "resilience", "finops", "compliance", "performance", "operability"}
        assert all(e.status == AgentStatus.COMPLETED for e in execs)
        # concurrency: every agent started before the first one completed
        assert max(e.started_at for e in execs) < min(e.completed_at for e in execs)
        # reducers: all candidate findings made it into the moderated set (none overwritten)
        state = c.executor.state_values(review.review_id)
        assert len(state["agent_results"]) == 6
        proposed = sum(e.finding_count for e in execs)
        assert len(state["candidate_findings"]) == proposed
        assert len(state["moderated_findings"]) <= proposed
    finally:
        c.shutdown()
        set_container(None)


def test_graph_pauses_with_real_interrupt_and_resumes_on_command(container):
    review = start_demo_review(container)
    rid = review.review_id
    snap = container.executor.snapshot(rid)
    assert snap.next == ("human_review",) and container.executor.is_interrupted(rid)
    interrupt_value = snap.tasks[0].interrupts[0].value
    assert interrupt_value["kind"] == "human_review" and interrupt_value["round"] == 1
    status = container.reviews.status_view(rid)
    assert status.awaiting_human and status.status == "AWAITING_HUMAN"
    events = [e.event_type for e in container.reviews.events(rid)]
    assert "graph_interrupted" in events
    container.workflow.resume(rid, "finalize")
    wait_idle(container, rid)
    assert not container.executor.is_interrupted(rid)
    assert container.reviews.get(rid).status in (ReviewStatus.COMPLETED, ReviewStatus.COMPLETED_WITH_OPEN_RISKS)
    assert "graph_resumed" in [e.event_type for e in container.reviews.events(rid)]


def test_decisions_require_awaiting_human_and_validation(container):
    review = start_demo_review(container)
    rid = review.review_id
    f = finding_by_title(container, rid, "backups")
    from backend.core.exceptions import ValidationFailed

    with pytest.raises(ValidationFailed):
        container.decisions.record(rid, DecisionCreate(finding_id=f.finding_id, decision_type=DecisionType.DISPUTE, justification="no"))
    with pytest.raises(ValidationFailed):
        container.decisions.record(rid, DecisionCreate(finding_id=f.finding_id, decision_type=DecisionType.DEFER, justification=""))
    rejected = next(x for x in container.reviews.findings(rid) if x.status == FindingStatus.REJECTED)
    from backend.core.exceptions import ConflictError

    with pytest.raises(ConflictError):
        container.decisions.record(rid, DecisionCreate(finding_id=rejected.finding_id, decision_type=DecisionType.ACCEPT))


def test_accept_dispute_defer_flow_with_rebuttal_and_adr(container):
    review = start_demo_review(container)
    rid = review.review_id
    acc, f_acc = decide(container, rid, "backups are disabled", DecisionType.ACCEPT, notes="Will fix")
    dis, f_dis = decide(container, rid, "plaintext HTTP", DecisionType.DISPUTE, justification="TLS ends at the CDN, port 80 only redirects")
    dfr, f_def = decide(container, rid, "Very large database", DecisionType.DEFER, justification="Waiting for Q3 load tests",
                        risk_owner="Platform lead", review_date="2026-12-01", remediation_target="Q1 2027")
    assert container.reviews.get_finding(f_acc.finding_id).status == FindingStatus.ACCEPTED  # accepted != remediated
    assert len(container.decisions.pending(rid)) == 3
    assert len(container.decisions.adrs(rid)) == 1  # ADR drafted at defer time
    adr = container.decisions.adrs(rid)[0]
    assert "Defer remediation" in adr.title and f_def.finding_id in adr.related_finding_ids and adr.owner == "Platform lead"
    assert "NOT resolved" in adr.consequences

    container.workflow.resume(rid)
    wait_idle(container, rid)
    assert container.reviews.get(rid).status == ReviewStatus.AWAITING_HUMAN  # back at the human boundary
    assert container.decisions.pending(rid) == []
    # rebuttal routed to the originating specialist, then moderated
    rebuttals = container.tables.rebuttals.list(review_id=rid)
    assert len(rebuttals) == 1
    reb = rebuttals[0]
    assert reb.agent_id == f_dis.agent_id == "security" and reb.dispute_decision_id == dis.decision_id
    assert [e.role for e in reb.exchange] == ["ARCHITECT", "CRITIC", "MODERATOR"]
    after = container.reviews.get_finding(f_dis.finding_id)
    assert after.status == FindingStatus.REBUTTED and after.severity.value == "LOW"  # severity adjusted by moderator
    events = [e.event_type for e in container.reviews.events(rid)]
    assert {"rebuttal_requested", "rebuttal_received", "moderator_determination"} <= set(events)
    # the deferred finding is not technically resolved
    assert container.reviews.get_finding(f_def.finding_id).status == FindingStatus.DEFERRED
    # a second dispute is allowed once, then capped
    decide(container, rid, "plaintext HTTP", DecisionType.DISPUTE, justification="Second attempt with more detail here")
    from backend.core.exceptions import ConflictError

    container.workflow.resume(rid)
    wait_idle(container, rid)
    with pytest.raises(ConflictError):
        decide(container, rid, "plaintext HTTP", DecisionType.DISPUTE, justification="Third attempt should be refused")


def test_critic_can_concede_a_dispute(container):
    review = start_demo_review(container)
    rid = review.review_id
    _, f = decide(container, rid, "no caching layer", DecisionType.DISPUTE,
                  justification="CloudFront CDN fronts all catalogue reads and Redis sits in front of the database")
    container.workflow.resume(rid)
    wait_idle(container, rid)
    after = container.reviews.get_finding(f.finding_id)
    assert after.status == FindingStatus.REJECTED and after.moderator_status.value == "REJECTED"
    assert any(h.event == "MODERATOR_DETERMINATION" for h in after.history)


def test_targeted_rereview_runs_only_affected_agents_and_tracks_finding_history(container):
    review = start_demo_review(container)
    rid = review.review_id
    before = {f.finding_id: f for f in container.reviews.findings(rid)}
    notes, files = container.demo.revision_files("aws-ecommerce")
    rev = container.revisions.create_revision(rid, notes, files, source="demo")
    impact = rev.impact
    assert rev.number == 2 and impact.confidence == "HIGH" and not impact.broadened
    assert "performance" not in impact.affected_agents  # unaffected critic is not scheduled
    assert {"security", "resilience"} <= set(impact.affected_agents)
    assert impact.changed_artifacts == ["infrastructure.tf"] and "aws_db_instance.orders" in impact.changed_components
    assert container.reviews.get(rid).architecture_revision == 1  # previous revision preserved until re-review
    assert container.reviews.get_model(rid, 1) is not None and container.reviews.get_model(rid, 2) is not None

    container.workflow.re_review(rid, rev.revision_id)
    wait_idle(container, rid)
    r = container.reviews.get(rid)
    assert r.status == ReviewStatus.AWAITING_HUMAN and r.current_round == 2 and r.architecture_revision == 2
    round2 = {e.agent_id: e for e in container.reviews.executions(rid, 2)}
    assert round2["performance"].status == AgentStatus.SKIPPED and "Unaffected" in round2["performance"].skip_reason
    assert round2["security"].status == AgentStatus.COMPLETED
    assert not any(e.started_at for e in [round2["performance"]])  # never executed
    after = {f.finding_id: f for f in container.reviews.findings(rid)}
    assert set(before) <= set(after)  # no finding identity is lost
    resolved = [f for f in after.values() if f.status == FindingStatus.RESOLVED]
    assert resolved and all(any(h.event == "RESOLVED_BY_REVISION" for h in f.history) for f in resolved)
    assert any(f.first_seen_revision == 2 for f in after.values())  # new concern introduced by the revision
    # unaffected agents' findings carried unchanged
    perf_before = [f for f in before.values() if f.agent_id == "performance" and f.moderator_status.value != "REJECTED"]
    assert all(after[f.finding_id].status == f.status for f in perf_before)


def test_unknown_impact_broadens_review_instead_of_skipping(container):
    review = start_demo_review(container)
    rid = review.review_id
    rev = container.revisions.create_revision(rid, "Clarified the narrative", [("architecture.md", b"# Shop\n\nUpdated narrative text only.", "PRIMARY_ARCHITECTURE")])
    assert rev.impact.confidence == "LOW" and rev.impact.broadened
    assert set(rev.impact.affected_agents) == set(container.reviews.get(rid).enabled_agents)


def test_max_rounds_terminates_with_open_risks_not_approval(container):
    review = start_demo_review(container, max_rounds=1)
    rid = review.review_id
    from backend.core.exceptions import ConflictError

    notes, files = container.demo.revision_files("aws-ecommerce")
    container.revisions.create_revision(rid, notes, files, source="demo")
    with pytest.raises(ConflictError):
        container.workflow.re_review(rid)
    container.workflow.resume(rid, "finalize")
    wait_idle(container, rid)
    final = container.reviews.get(rid)
    assert final.status == ReviewStatus.COMPLETED_WITH_OPEN_RISKS  # never auto-approved
    assert container.reviews.status_view(rid).open_critical_high > 0


def test_one_failing_specialist_does_not_erase_other_results(container, monkeypatch):
    from backend.llm.mock_provider import MockProvider

    original = MockProvider._agent_review

    def broken(self, agent_id, scenario, ctx):
        if agent_id == "finops":
            raise RuntimeError("simulated provider outage")
        return original(self, agent_id, scenario, ctx)

    monkeypatch.setattr(MockProvider, "_agent_review", broken)
    review = start_demo_review(container)
    rid = review.review_id
    assert review.status == ReviewStatus.AWAITING_HUMAN
    execs = {e.agent_id: e for e in container.reviews.executions(rid, 1)}
    assert execs["finops"].status == AgentStatus.FAILED and "simulated provider outage" in execs["finops"].error
    assert execs["security"].status == AgentStatus.COMPLETED and execs["security"].finding_count > 0
    assert any(f.agent_id == "security" for f in container.reviews.findings(rid))
    assert "partial_analysis" in [e.event_type for e in container.reviews.events(rid)]
    report = container.reports.build(rid)
    assert next(a for a in report.coverage["agents"] if a["agent_id"] == "finops")["status"] == "FAILED"


def test_all_agents_failing_marks_review_failed_with_actionable_error(container, monkeypatch):
    from backend.llm.mock_provider import MockProvider

    monkeypatch.setattr(MockProvider, "_agent_review", lambda self, a, s, c: (_ for _ in ()).throw(RuntimeError("down")))
    review = start_demo_review(container)
    assert review.status == ReviewStatus.FAILED and "All specialist agents failed" in review.error


def test_live_mode_without_credentials_fails_clearly_and_never_falls_back_to_mock(container):
    from backend.models.domain import Project
    from backend.models.schemas import ReviewCreate

    project = container.tables.projects.put(Project(name="live"))
    container.ingestion.add_document(project.project_id, "a.md", b"# A\nThe system uses PostgreSQL.", "PRIMARY_ARCHITECTURE")
    review = container.reviews.create_review(ReviewCreate(project_id=project.project_id, name="live", demo_mode=False, provider="anthropic"))
    container.workflow.start(review.review_id)
    wait_idle(container, review.review_id)
    r = container.reviews.get(review.review_id)
    assert r.status == ReviewStatus.FAILED and "ANTHROPIC_API_KEY" in r.error
    assert not container.reviews.findings(review.review_id)


def test_cancel_stops_a_running_review(tmp_path):
    c = make_container(tmp_path)
    c.settings_store._settings = c.settings_store.get().model_copy(update={"mock_latency_seconds": 1.5})
    try:
        from backend.models.schemas import ReviewCreate

        project = c.tables.projects.put(c.demo.new_project("aws-ecommerce"))
        _, files = c.demo.scenario_files("aws-ecommerce")
        c.ingestion.add_documents(project.project_id, files)
        review = c.reviews.create_review(ReviewCreate(project_id=project.project_id, name="x", demo_mode=True, scenario_id="aws-ecommerce"))
        c.workflow.start(review.review_id)
        time.sleep(0.4)
        c.workflow.cancel(review.review_id)
        wait_idle(c, review.review_id)
        assert c.reviews.get(review.review_id).status == ReviewStatus.FAILED
        assert "cancel" in c.reviews.get(review.review_id).error.lower()
    finally:
        c.shutdown()
        set_container(None)


# ----------------------------------------------------------------- restart recovery (14)
def test_checkpoint_survives_restart_and_review_resumes(tmp_path):
    c1 = make_container(tmp_path)
    review = start_demo_review(c1)
    rid = review.review_id
    decide(c1, rid, "backups are disabled", DecisionType.ACCEPT, notes="agreed")
    c1.snapshots.flush()  # what the debounced autosave would have written
    c1.shutdown()
    set_container(None)

    c2 = make_container(tmp_path)  # same data dir, brand-new process state
    try:
        assert c2.restore_info["restored"]
        restored = c2.reviews.get(rid)
        assert restored.status == ReviewStatus.AWAITING_HUMAN
        pending = c2.decisions.pending(rid)
        assert len(pending) == 1 and pending[0].decision_type == DecisionType.ACCEPT  # decision not lost
        assert c2.executor.is_interrupted(rid)  # exact graph continuation available
        assert restored.recovery_note is None
        c2.workflow.resume(rid)
        wait_idle(c2, rid)
        assert c2.reviews.get_finding(finding_by_title(c2, rid, "backups are disabled").finding_id).status == FindingStatus.ACCEPTED
        assert c2.decisions.pending(rid) == []
        c2.workflow.resume(rid, "finalize")
        wait_idle(c2, rid)
        assert c2.reviews.get(rid).status == ReviewStatus.COMPLETED_WITH_OPEN_RISKS
        assert c2.reports.list(rid)
    finally:
        c2.shutdown()
        set_container(None)


def test_missing_checkpoint_is_reconstructed_and_reported_honestly(tmp_path):
    c1 = make_container(tmp_path)
    review = start_demo_review(c1)
    rid = review.review_id
    decide(c1, rid, "backups are disabled", DecisionType.ACCEPT)
    c1.workflow.resume(rid)  # submit so the decision is part of graph history
    wait_idle(c1, rid)
    c1.snapshots.flush()
    c1.shutdown()
    set_container(None)
    (tmp_path / "data" / "checkpoints" / "langgraph.sqlite").unlink()
    for wal in (tmp_path / "data" / "checkpoints").glob("langgraph.sqlite-*"):
        wal.unlink()

    c2 = make_container(tmp_path)
    try:
        r = c2.reviews.get(rid)
        assert r.status == ReviewStatus.AWAITING_HUMAN and r.recovery_note and "checkpoint" in r.recovery_note.lower()
        assert not c2.executor.is_interrupted(rid)
        assert c2.reviews.get_finding(finding_by_title(c2, rid, "backups are disabled").finding_id).status == FindingStatus.ACCEPTED
        c2.workflow.resume(rid, "finalize")
        wait_idle(c2, rid)
        final = c2.reviews.get(rid)
        assert final.status == ReviewStatus.COMPLETED_WITH_OPEN_RISKS
        assert "re-seeded" in (final.recovery_note or "") or "seeded" in (final.recovery_note or "")
        assert any(e.event_type == "checkpoint_reconstructed" for e in c2.reviews.events(rid))
    finally:
        c2.shutdown()
        set_container(None)


def test_review_interrupted_mid_run_is_marked_failed_with_guidance(tmp_path):
    c1 = make_container(tmp_path)
    review = start_demo_review(c1)
    rid = review.review_id
    c1.reviews.set_status(rid, ReviewStatus.ANALYZING, "Running critics")  # simulate crash during analysis
    c1.snapshots.flush()
    c1.shutdown()
    set_container(None)
    c2 = make_container(tmp_path)
    try:
        r = c2.reviews.get(rid)
        assert r.status == ReviewStatus.FAILED and "restarted" in r.error
        assert json.dumps(r.error)
    finally:
        c2.shutdown()
        set_container(None)
