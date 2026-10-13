"""Architecture extraction from deterministic fixtures (4) and the deterministic tool framework."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.models.architecture import ArchitectureModel
from backend.models.domain import ArchitectureArtifact
from backend.parsers.parser_registry import detect_type, parse_bytes
from backend.parsers.types import IacResource, ParserLimits
from backend.services.architecture_service import ArchitectureService
from backend.services.tool_service import ALL_RULE_IDS, build_tool_context, run_builtin
from backend.tools import AGENT_RULES, REGISTRY, ToolContext
from backend.tools.dependency_analyzer import DependencyGraph

ROOT = Path(__file__).resolve().parents[2] / "src"
FIXTURES = ROOT / "demo" / "fixtures"


def load_docs(scenario: str):
    docs = []
    folder = FIXTURES / scenario
    import json

    meta = json.loads((folder / "scenario.json").read_text())
    for i, f in enumerate(meta["files"]):
        data = (folder / f["filename"]).read_bytes()
        t = detect_type(f["filename"], data)
        parsed = parse_bytes(data, f["filename"], t, ParserLimits())
        art = ArchitectureArtifact(artifact_id=f"art-{i}", project_id="p", filename=f["filename"], stored_path="", sha256="x" * 64,
                                   size_bytes=len(data), detected_type=t, role=f["role"])
        parsed.artifact_id = art.artifact_id
        for r in parsed.resources:
            r.artifact_id, r.filename = art.artifact_id, art.filename
        docs.append((art, parsed))
    return docs


def build(scenario: str) -> tuple[ArchitectureModel, list]:
    docs = load_docs(scenario)
    model = ArchitectureService(ingestion=None).build_model(title=scenario, description="", environment_hint="Unknown", docs=docs)
    return model, docs


def results_for(scenario: str):
    model, docs = build(scenario)
    ctx = build_tool_context("r", model, docs)
    results, summaries = run_builtin(ctx)
    return model, results, summaries


# ----------------------------------------------------------------- extraction
def test_aws_model_components_relationships_and_nfrs():
    model, _ = build("aws-ecommerce")
    by_id = {c.component_id: c for c in model.components}
    assert by_id["aws_db_instance.orders"].type == "database"
    assert by_id["aws_db_instance.orders"].configuration["multi_az"] is False
    assert by_id["aws_db_instance.orders"].provenance["multi_az"] == "EXPLICIT"
    assert by_id["aws_db_instance.orders"].configuration["password"] == "<redacted>"  # secrets never enter the model
    assert by_id["aws_lb.alb"].type == "load_balancer"
    # diagram nodes were merged into IaC components (not duplicated)
    assert "diagram:orders-queue-sqs" not in by_id
    assert any(r.source == "aws_lb.alb" and r.target == "aws_autoscaling_group.app" for r in model.relationships)
    cats = {n.category: n.value for n in model.nfrs}
    assert cats["availability"] == "99.95" and cats["rto"] == "30 minutes" and cats["rpo"] == "5 minutes"
    assert "eu-west-1" in model.deployment_regions
    assert model.environment == "AWS"


def test_source_references_point_back_to_files_and_lines():
    model, docs = build("aws-ecommerce")
    db = model.component("aws_db_instance.orders")
    tf_ref = next(r for r in db.source_references if r.filename == "infrastructure.tf")
    assert tf_ref.resource_path == "aws_db_instance.orders" and tf_ref.line_start and tf_ref.excerpt.startswith("resource")
    assert any(r.node_id for r in db.source_references)  # diagram node
    art_ids = {a.artifact_id for a, _ in docs}
    assert all(r.artifact_id in art_ids for c in model.components for r in c.source_references if r.artifact_id)


def test_kubernetes_model_uses_service_selectors_and_skips_shared_configmap_noise():
    model, _ = build("microservices-k8s")
    gw = model.component("Deployment/payflow/api-gateway")
    assert gw.configuration["replicas"] == 1 and gw.configuration["readiness_probe"] is False
    assert model.component("StatefulSet/payflow/postgres").type == "database"
    pairs = {(r.source, r.target) for r in model.relationships}
    assert ("Ingress/payflow/public-api", "Deployment/payflow/api-gateway") in pairs
    # envFrom of a shared ConfigMap must not create all-to-all call edges
    assert ("Deployment/payflow/notification-service", "StatefulSet/payflow/postgres") not in pairs
    assert any(n.level == "LIMITATION" and "shared" in n.message for n in model.notes)


def test_hybrid_model_regions_and_unmapped_diagram_nodes():
    model, _ = build("hybrid-onprem")
    assert "us-east-1" in model.deployment_regions and "eu-central-1" in model.deployment_regions
    assert model.environment.startswith("Hybrid")
    assert model.component("diagram:oracle-ledger-db").type == "database"
    assert model.component("PartnerGatewayFunction") is not None


def test_unresolved_information_is_reported_when_absent():
    from backend.models.domain import DocumentRole

    art = ArchitectureArtifact(artifact_id="a", project_id="p", filename="n.md", stored_path="", sha256="x" * 64, size_bytes=5,
                               detected_type="markdown", role=DocumentRole.PRIMARY_ARCHITECTURE)
    parsed = parse_bytes(b"# Shop\nThe shop sells things.", "n.md", "markdown", ParserLimits())
    model = ArchitectureService(ingestion=None).build_model(title="x", description="", environment_hint="Unknown", docs=[(art, parsed)])
    assert any("RTO" in u for u in model.unresolved) and any("availability" in u.lower() for u in model.unresolved)


# ----------------------------------------------------------------- tool rules
def rule_hits(scenario: str) -> set[str]:
    return {r.rule_id for r in results_for(scenario)[1]}


def test_all_registered_rules_are_assigned_to_an_agent_and_exist():
    assigned = {r for rules in AGENT_RULES.values() for r in rules}
    assert assigned <= set(REGISTRY)
    assert set(AGENT_RULES) == {"security", "resilience", "finops", "compliance", "performance", "operability"}
    assert len(ALL_RULE_IDS) >= 30


def test_aws_fixture_triggers_expected_security_and_resilience_rules():
    hits = rule_hits("aws-ecommerce")
    assert {"SEC-NET-001", "SEC-NET-002", "SEC-STO-001", "SEC-ENC-001", "SEC-SEC-001", "SEC-IAM-001", "SEC-ENC-002"} <= hits
    assert {"RES-HA-001", "RES-BAK-001", "RES-MSG-001", "RES-NAT-001", "RES-HA-004"} <= hits
    assert {"COST-SIZE-001", "COST-LOG-001", "COST-S3-001", "PERF-CHAIN-001", "OPS-ALARM-001"} <= hits


def test_kubernetes_checks():
    hits = rule_hits("microservices-k8s")
    assert {"SEC-K8S-001", "SEC-K8S-002", "SEC-SEC-001", "RES-HA-002", "RES-HA-003", "CFG-K8S-001", "CFG-K8S-002", "CFG-REF-001"} <= hits


def test_cloudformation_checks():
    hits = rule_hits("hybrid-onprem")
    assert {"SEC-NET-001", "SEC-STO-001", "SEC-IAM-001", "SEC-SEC-001", "SEC-ENC-001", "RES-HA-001", "RES-MSG-001"} <= hits


def test_hardened_revision_resolves_flagged_rules():
    import json

    docs = load_docs("aws-ecommerce")
    old_hits = {r.rule_id for r in run_builtin(build_tool_context("r", build("aws-ecommerce")[0], docs))[0]}
    rev = (FIXTURES / "aws-ecommerce" / "revision-1" / "infrastructure.tf").read_bytes()
    parsed = parse_bytes(rev, "infrastructure.tf", "terraform", ParserLimits())
    for r in parsed.resources:
        r.artifact_id, r.filename = "art-2", "infrastructure.tf"
    docs2 = [(a, parsed if a.filename == "infrastructure.tf" else p) for a, p in docs]
    model2 = ArchitectureService(ingestion=None).build_model(title="x", description="", environment_hint="Unknown", docs=docs2)
    new_hits = {r.rule_id for r in run_builtin(build_tool_context("r", model2, docs2))[0]}
    assert {"SEC-NET-002", "SEC-IAM-001", "RES-HA-001", "RES-BAK-001", "SEC-SEC-001"} <= old_hits
    assert not ({"SEC-NET-002", "SEC-IAM-001", "RES-HA-001", "RES-BAK-001", "SEC-SEC-001", "SEC-ENC-001", "RES-MSG-001"} & new_hits)
    assert "RES-NAT-001" in new_hits and json.dumps(sorted(new_hits))  # unresolved items remain


def _ctx(resources: list[IacResource]) -> ToolContext:
    return ToolContext(review_id="r", resources=resources, model=ArchitectureModel())


def _res(rtype: str, name: str, props: dict, framework: str = "terraform") -> IacResource:
    return IacResource(framework=framework, resource_type=rtype, name=name, address=f"{rtype}.{name}", properties=props, artifact_id="a", filename="f.tf", line=1)


def test_unresolved_variables_are_not_flagged():
    ctx = _ctx([_res("aws_db_instance", "d", {"publicly_accessible": "${var.public}", "storage_encrypted": "${var.enc}"})])
    results, _ = run_builtin(ctx, ["SEC-NET-002", "SEC-ENC-001"])
    assert results == []


def test_open_web_ports_are_not_flagged_but_ssh_is():
    web = _res("aws_security_group", "web", {"ingress": [{"from_port": 443, "to_port": 443, "protocol": "tcp", "cidr_blocks": ["0.0.0.0/0"]}]})
    ssh = _res("aws_security_group", "ssh", {"ingress": [{"from_port": 22, "to_port": 22, "protocol": "tcp", "cidr_blocks": ["0.0.0.0/0"]}]})
    results, _ = run_builtin(_ctx([web, ssh]), ["SEC-NET-001"])
    assert [r.affected_component_ids for r in results] == [["aws_security_group.ssh"]]
    assert results[0].severity_hint.value == "CRITICAL"
    assert results[0].observed_value and results[0].expected_value and results[0].evidence_reference.line_start == 1


def test_missing_keyword_alone_never_creates_a_finding():
    """Absence of a word in prose must not be reported as a vulnerability."""
    model = ArchitectureModel()
    results, _ = run_builtin(ToolContext(review_id="r", resources=[], model=model), ALL_RULE_IDS)
    assert results == []


def test_dependency_graph_is_deterministic():
    model, _ = build("aws-ecommerce")
    g = DependencyGraph(model)
    db = "aws_db_instance.orders"
    assert g.reverse_dependencies(db) == sorted(g.reverse_dependencies(db))
    assert "aws_autoscaling_group.app" in g.failure_propagation(db) or any("app" in x for x in g.failure_propagation(db))
    assert g.single_dependency_bottlenecks(2)
    assert g.longest_sync_chain() == DependencyGraph(model).longest_sync_chain()
    impacted = g.impacted_after_revision([db])
    assert db in impacted and impacted == sorted(impacted)


def test_checkov_adapter_reports_not_executed_when_missing(monkeypatch):
    from backend.tools import checkov_adapter

    monkeypatch.setattr(checkov_adapter, "checkov_available", lambda: None)
    results, summary = checkov_adapter.run_checkov([("a", "x.tf", "terraform", "resource \"a\" \"b\" {}")])
    assert results == [] and summary.status == "NOT_EXECUTED" and "not installed" in summary.detail


@pytest.mark.parametrize("scenario", ["aws-ecommerce", "microservices-k8s", "hybrid-onprem"])
def test_tool_result_schema_fields_present(scenario):
    _, results, summaries = results_for(scenario)
    assert results and summaries
    for r in results:
        assert r.tool_id and r.tool_name and r.rule_id and r.execution_status == "EXECUTED" and r.timestamp
        assert r.tool_result_id.startswith("TR-")
