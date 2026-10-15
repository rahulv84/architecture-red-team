"""API happy/error paths (16) and the complete offline demo workflow for every scenario (17)."""
from __future__ import annotations

import time

import pytest

from tests.conftest import wait_idle


def wait_status(client, rid: str, wanted: set[str], timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/v1/reviews/{rid}/status").json()
        if st["status"] in wanted:
            return st
        time.sleep(0.1)
    raise AssertionError(f"review stuck: {st}")


def test_health_config_providers(client):
    tc, _ = client
    h = tc.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["database"] == "sqlite-in-memory"
    cfg = tc.get("/api/v1/config").json()
    assert "anthropic_api_key" not in cfg["settings"] and "no authentication" in cfg["local_only_notice"]
    provs = {p["provider"]: p for p in tc.get("/api/v1/providers").json()}
    assert set(provs) == {"mock", "anthropic", "openai", "gemini", "bedrock"}
    v = tc.post("/api/v1/providers/validate", json={"provider": "anthropic"}).json()
    assert v["ok"] is False and "ANTHROPIC_API_KEY" in v["message"]
    g = tc.post("/api/v1/providers/validate", json={"provider": "gemini"}).json()
    assert g["ok"] is False and "GEMINI_API_KEY" in g["message"]
    assert tc.post("/api/v1/providers/validate", json={"provider": "nope"}).status_code == 422


def test_settings_update_never_returns_secrets(client):
    tc, _ = client
    r = tc.put("/api/v1/settings", json={"max_review_rounds": 2, "anthropic_api_key": "sk-ant-abcdefghijklmnopqrstuvwxyz",
                                         "gemini_api_key": "gemini-secret-1234567890", "gemini_model": "gemini-3.1-pro-preview"})
    assert r.status_code == 200
    body = r.json()
    assert body["max_review_rounds"] == 2 and body["anthropic_api_key_configured"] is True
    assert body["gemini_api_key_configured"] is True and body["gemini_model"] == "gemini-3.1-pro-preview"
    assert "sk-ant" not in r.text and "sk-ant" not in tc.get("/api/v1/settings").text
    assert "gemini-secret" not in r.text and "gemini-secret" not in tc.get("/api/v1/settings").text
    assert tc.put("/api/v1/settings", json={}).status_code == 422
    assert tc.put("/api/v1/settings", json={"llm_provider": "x"}).status_code == 422


def test_document_upload_error_paths(client):
    tc, _ = client
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    files = [
        ("files", ("ok.md", b"# Doc\nuses PostgreSQL", "text/markdown")),
        ("files", ("evil.exe", b"MZ\x00", "application/octet-stream")),
        ("files", ("lying.pdf", b"plain text pretending", "application/pdf")),
    ]
    r = tc.post(f"/api/v1/projects/{pid}/documents", files=files, data={"roles": ["PRIMARY_ARCHITECTURE", "SUPPORTING", "SUPPORTING"]})
    assert r.status_code == 201
    body = r.json()
    assert [a["filename"] for a in body["accepted"]] == ["ok.md"] and body["accepted"][0]["role"] == "PRIMARY_ARCHITECTURE"
    assert {x["filename"] for x in body["rejected"]} == {"evil.exe", "lying.pdf"}
    assert tc.post("/api/v1/projects/nope/documents", files=files[:1]).status_code == 404
    bad_role = tc.post(f"/api/v1/projects/{pid}/documents", files=files[:1], data={"roles": ["BOGUS"]})
    assert bad_role.status_code == 422 and bad_role.json()["error"]["code"] == "validation_failed"
    content = tc.get(f"/api/v1/documents/{body['accepted'][0]['artifact_id']}/content").json()
    assert "PostgreSQL" in content["text"] and content["sections"]


def test_review_error_paths(client):
    tc, _ = client
    assert tc.get("/api/v1/reviews/missing/status").status_code == 404
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    assert tc.post("/api/v1/reviews", json={"project_id": "zzz", "name": "x"}).status_code == 404
    bad_agent = tc.post("/api/v1/reviews", json={"project_id": pid, "name": "x", "enabled_agents": ["wizard"]})
    assert bad_agent.status_code == 422
    live_mock = tc.post("/api/v1/reviews", json={"project_id": pid, "name": "x", "demo_mode": False, "provider": "mock"})
    assert live_mock.status_code == 422
    rid = tc.post("/api/v1/reviews", json={"project_id": pid, "name": "x"}).json()["review_id"]
    assert tc.post(f"/api/v1/reviews/{rid}/resume", json={"action": "finalize"}).status_code == 409
    assert tc.post(f"/api/v1/reviews/{rid}/decisions", json={"finding_id": "f", "decision_type": "ACCEPT"}).status_code == 409
    assert tc.post(f"/api/v1/reviews/{rid}/re-review", json={}).status_code == 409
    assert tc.get("/api/v1/reports/none/download").status_code == 404


def test_demo_end_to_end_through_the_api(client):
    tc, c = client
    scenarios = tc.get("/api/v1/demo/scenarios").json()
    assert {s["scenario_id"] for s in scenarios} == {"aws-ecommerce", "microservices-k8s", "hybrid-onprem"}
    created = tc.post("/api/v1/demo/scenarios/aws-ecommerce/project").json()
    pid = created["project"]["project_id"]
    assert all(d["parse_status"] in ("PARSED", "PARTIAL") for d in created["documents"])
    rid = tc.post("/api/v1/reviews", json={"project_id": pid, "name": "ShopSphere", "scenario_id": "aws-ecommerce"}).json()["review_id"]
    assert tc.post(f"/api/v1/reviews/{rid}/start").status_code == 202
    assert tc.post(f"/api/v1/reviews/{rid}/start").status_code in (409, 202)  # duplicate submission is guarded
    st = wait_status(tc, rid, {"AWAITING_HUMAN", "FAILED"})
    assert st["status"] == "AWAITING_HUMAN" and st["simulated"] is True and len(st["agents"]) == 6
    assert all(a["status"] == "COMPLETED" for a in st["agents"]) and st["moderator"]["status"] == "COMPLETED"

    arch = tc.get(f"/api/v1/reviews/{rid}/architecture").json()
    assert arch["available"] and len(arch["model"]["components"]) >= 15
    fs = tc.get(f"/api/v1/reviews/{rid}/findings", params={"severity": "CRITICAL", "agent": "security"}).json()
    assert fs and all(f["severity"] == "CRITICAL" for f in fs)
    detail = tc.get(f"/api/v1/findings/{fs[0]['finding_id']}").json()
    assert detail["tool_results"] and detail["finding"]["source_references"]

    accept = tc.post(f"/api/v1/reviews/{rid}/decisions", json={"finding_id": fs[0]["finding_id"], "decision_type": "ACCEPT"})
    assert accept.status_code == 201

    rev = tc.post(f"/api/v1/reviews/{rid}/revisions", data={"use_demo_revision": "true"})
    assert rev.status_code == 201 and rev.json()["impact"]["affected_agents"]
    rr = tc.post(f"/api/v1/reviews/{rid}/re-review", json={})
    assert rr.status_code == 202 and "performance" not in rr.json()["affected_agents"]
    wait_idle(c, rid)
    st2 = wait_status(tc, rid, {"AWAITING_HUMAN", "FAILED"})
    assert st2["current_round"] == 2 and st2["architecture_revision"] == 2
    assert tc.post(f"/api/v1/reviews/{rid}/resume", json={"action": "finalize"}).status_code == 202
    wait_idle(c, rid)
    done = wait_status(tc, rid, {"COMPLETED", "COMPLETED_WITH_OPEN_RISKS", "FAILED"})
    assert done["status"] == "COMPLETED_WITH_OPEN_RISKS"
    reports = tc.get(f"/api/v1/reviews/{rid}/reports").json()
    pdf = next(r for r in reports if r["format"] == "pdf")
    dl = tc.get(f"/api/v1/reports/{pdf['report_id']}/download")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    score = tc.get(f"/api/v1/reviews/{rid}/score").json()
    assert {d["domain"] for d in score["domains"]} == {"security", "resilience", "finops", "compliance", "performance", "operability"}
    hist = tc.get(f"/api/v1/reviews/{rid}/history").json()
    assert len(hist["revisions"]) == 2 and hist["decisions"] and hist["change_impacts"]
    ev = tc.get(f"/api/v1/reviews/{rid}/events").json()
    kinds = {e["event_type"] for e in ev["events"]}
    assert {"review_started", "architecture_extracted", "agent_started", "tool_executed", "agent_completed", "moderator_completed",
            "graph_interrupted", "human_decision_received", "graph_resumed", "revision_received", "re_review_started",
            "report_generated"} <= kinds
    assert all(e["review_id"] == rid and e["timestamp"] for e in ev["events"])
    graph = tc.get(f"/api/v1/reviews/{rid}/graph").json()
    assert {"moderator", "human_review", "security_agent", "operability_agent"} <= set(graph["nodes"])


@pytest.mark.parametrize("scenario,min_findings", [("aws-ecommerce", 20), ("microservices-k8s", 18), ("hybrid-onprem", 18)])
def test_every_scenario_runs_offline_with_all_six_agents(container, scenario, min_findings):
    from tests.conftest import start_demo_review

    review = start_demo_review(container, scenario)
    rid = review.review_id
    assert review.status.value == "AWAITING_HUMAN" and review.simulated
    execs = container.reviews.executions(rid, 1)
    assert len(execs) == 6 and all(e.status.value == "COMPLETED" for e in execs)
    assert all(e.finding_count > 0 for e in execs), "every specialist should contribute in every sample"
    findings = container.reviews.findings(rid)
    assert len(findings) >= min_findings
    assert all(f.simulated for f in findings)  # fixture output is always marked as simulated
    assert any(f.moderator_status.value == "REJECTED" for f in findings)  # seeded hallucination handled
    assert any(f.moderator_status.value == "MERGED" for f in findings) or scenario != "aws-ecommerce"
    ev = container.evaluation.evaluate(rid)
    assert ev["labeled"] and ev["recall"] >= 0.85
    assert all(u["handled"] for u in ev["seeded_unsupported"])
    assert ev["deterministic_tool_detection"]["detected_by_tools"]
    # revision works for every sample
    notes, files = container.demo.revision_files(scenario)
    rev = container.revisions.create_revision(rid, notes, files, source="demo")
    assert rev.impact.affected_agents
    container.workflow.re_review(rid, rev.revision_id)
    wait_idle(container, rid)
    assert container.reviews.get(rid).status.value == "AWAITING_HUMAN"
    resolved = [f for f in container.reviews.findings(rid) if f.status.value == "RESOLVED"]
    assert resolved, "the hardening revision should resolve seeded flaws"


@pytest.mark.live
def test_live_provider_smoke_requires_keys():  # pragma: no cover - optional integration test
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")


@pytest.mark.live
def test_live_gemini_connectivity():  # pragma: no cover - optional integration test
    import os

    from backend.core.config import Settings
    from backend.llm.provider_factory import create_provider

    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    result = create_provider("gemini", Settings()).check_connectivity()
    assert result.ok, result.message
