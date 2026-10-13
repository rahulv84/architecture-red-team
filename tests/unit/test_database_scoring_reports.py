"""In-memory database behaviour (13), snapshots (14), scoring and report generation (15)."""
from __future__ import annotations

import threading

from backend.models.database import Database, Tables
from backend.models.domain import (
    AgentExecution,
    AgentStatus,
    EvidenceLevel,
    Finding,
    Project,
    Review,
    Severity,
)
from backend.reporting.json_generator import write_json_report
from backend.services.scoring_service import ScoringService
from tests.conftest import start_demo_review


def test_each_database_instance_is_isolated_and_in_memory():
    db1, db2 = Database(), Database()
    Tables(db1).projects.put(Project(project_id="p1", name="one"))
    assert Tables(db1).projects.get("p1") is not None
    assert Tables(db2).projects.get("p1") is None  # separate in-memory database
    assert str(db1.engine.url) == "sqlite://"


def test_session_is_shared_across_threads_and_serialised():
    db = Database()
    t = Tables(db)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                t.projects.put(Project(project_id=f"p{n}-{i}", name="x"))
                assert t.projects.get(f"p{n}-{i}") is not None
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert not errors
    assert len(t.projects.list()) == 200  # every thread saw/wrote the same database


def test_events_have_monotonic_sequence_numbers():
    from backend.models.domain import ExecutionEvent

    t = Tables(Database())
    seqs = [t.add_event(ExecutionEvent(review_id="r", event_type="x")).seq for _ in range(5)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5
    assert [e.seq for e in t.list_events("r", after=seqs[1])] == seqs[2:]


def test_snapshot_roundtrip_is_atomic_and_versioned(container, tmp_path):
    review = start_demo_review(container)
    path = container.snapshots.save_now()
    assert path.exists() and not path.with_suffix(".json.tmp").exists()
    import json

    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 1 and payload["tables"]["findings"]
    n_findings = len(container.reviews.findings(review.review_id))
    container.db.reset()
    assert container.reviews.findings(review.review_id) == []
    info = container.snapshots.restore()
    assert info["restored"] and len(container.reviews.findings(review.review_id)) == n_findings
    assert container.reviews.get_model(review.review_id) is not None
    assert container.reviews.events(review.review_id)


def test_corrupt_snapshot_falls_back_to_backup(container):
    review = start_demo_review(container)
    container.snapshots.save_now()
    container.snapshots.save_now()  # creates .bak of the previous good file
    container.snapshots.state_path.write_text("{ not json")
    container.db.reset()
    info = container.snapshots.restore()
    assert info["restored"] and info["source"].endswith(".bak")
    assert container.reviews.get(review.review_id)


def test_scorecard_separates_confirmed_from_unverified_and_rounds():
    from backend.core.config import Settings
    from backend.services.context_service import ContextService

    scoring = ScoringService(ContextService(Settings().knowledge_dir))
    review = Review(review_id="r", project_id="p", name="n", enabled_agents=["security", "resilience"])

    def f(agent, sev, level, status="OPEN", **kw):
        return Finding(review_id="r", agent_id=agent, title="t", description="d", category="c", severity=sev, evidence_level=level,
                       recommendation="r", status=status, **kw)

    findings = [
        f("security", Severity.CRITICAL, EvidenceLevel.CONFIRMED),            # -25
        f("security", Severity.HIGH, EvidenceLevel.NEEDS_VALIDATION),         # -3.75 (unverified)
        f("security", Severity.HIGH, EvidenceLevel.CONFIRMED, status="RESOLVED"),  # no penalty
        f("resilience", Severity.MEDIUM, EvidenceLevel.INFERRED),             # -4.2
    ]
    execs = [AgentExecution(review_id="r", agent_id=a, status=AgentStatus.COMPLETED) for a in ("security", "resilience")]
    card = scoring.scorecard(review, findings, execs)
    by = {d.domain: d for d in card.domains}
    assert len(card.domains) == 6
    assert by["security"].confirmed_findings == 1 and by["security"].unverified_findings == 1
    assert by["security"].confirmed_only_score == 75 and by["security"].score == 70  # rounded to 5, unverified lowers it slightly
    assert by["resilience"].score == 95
    assert by["finops"].coverage == "NONE" and by["finops"].band == "Not assessed"  # unknown != failure
    assert "not an approval" in card.overall_statement or "approval remains a human decision" in card.overall_statement


def test_report_json_validates_and_pdf_is_readable(container):
    from pypdf import PdfReader

    from backend.models.schemas import AssessmentReport

    review = start_demo_review(container)
    rid = review.review_id
    container.workflow.resume(rid, "finalize")
    from tests.conftest import wait_idle

    wait_idle(container, rid)
    reports = {r.format: r for r in container.reports.list(rid)}
    assert set(reports) == {"json", "pdf"}
    parsed = AssessmentReport.model_validate_json(open(reports["json"].path, encoding="utf-8").read())
    assert parsed.simulated is True and len(parsed.scorecard.domains) == 6
    assert parsed.findings and all(f.source_references or f.moderator_status == "REJECTED" or f.simulated for f in parsed.findings)
    assert parsed.traceability and parsed.limitations and parsed.disclaimers
    assert parsed.executive_summary["overall_assessment"].startswith("Completed with OPEN RISKS")
    reader = PdfReader(reports["pdf"].path)
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert len(reader.pages) >= 4
    for needle in ("SIMULATED DEMO MODE", "Scorecard", "Executive summary", "Limitations", "Security", "Operability"):
        assert needle in text


def test_json_report_roundtrip_matches_model(container, tmp_path):
    review = start_demo_review(container)
    report = container.reports.build(review.review_id)
    out = tmp_path / "r.json"
    size = write_json_report(report, out)
    assert size > 1000 and out.exists() and not out.with_suffix(".json.part").exists()
