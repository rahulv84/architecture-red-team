"""Exercise the *live-mode* code paths (LLM extraction, vision, agents, moderator, rebuttal) with an in-process fake
provider - no network, no API keys."""
from __future__ import annotations

import io
import json
import re

from backend.core.config import Settings
from backend.llm.base_provider import LLMProvider, ProviderCapabilities, ProviderValidation, RawCompletion, StructuredResponse
from backend.llm.contracts import (
    ExtractedComponent,
    ExtractedRelationship,
    ExtractionOutput,
    ModeratorAssessment,
    ModeratorReviewOutput,
    RebuttalOutput,
)
from backend.models.domain import (
    AgentReviewOutput,
    DecisionType,
    EvidenceLevel,
    FindingDraft,
    FindingStatus,
    Project,
    ReviewStatus,
    Severity,
    UsageRecord,
)
from backend.models.schemas import DecisionCreate, ReviewCreate
from tests.conftest import finding_by_title, wait_idle

TF = b'''resource "aws_db_instance" "main" {
  engine              = "postgres"
  publicly_accessible = true
}
resource "aws_s3_bucket" "logs" {
  bucket = "company-logs"
}
'''
DOC = b"# Payments\n\n## Overview\nThe checkout service writes orders to the main PostgreSQL database and exports logs to a bucket.\n"


class FakeLiveProvider(LLMProvider):
    """Non-simulated provider with vision support; records every prompt it receives."""

    name = "fakelive"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.prompts: list[tuple[str, str]] = []

    @property
    def model_id(self) -> str:
        return "fake-live-1"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(provider=self.name, model_id=self.model_id, vision=True)

    def validate_configuration(self) -> ProviderValidation:
        return ProviderValidation(ok=True, provider=self.name, model_id=self.model_id, message="ok")

    def _complete(self, system, user, *, max_tokens, schema, schema_name):  # pragma: no cover - not used
        return RawCompletion(text="{}")

    def analyze_image(self, prompt, image, media_type, *, agent_id=None):
        self.prompts.append(("image", prompt))
        payload = {"components": [{"id": "c1", "label": "Edge Cache", "type": "cache"}, {"id": "c2", "label": "main database", "type": "database"}],
                   "connections": [{"source": "c1", "target": "c2", "label": "reads"}], "notes": ""}
        return json.dumps(payload), UsageRecord(provider=self.name, model_id=self.model_id, task="analyze_image")

    def generate_structured(self, request):
        self.prompts.append((request.task, request.user_prompt))
        usage = UsageRecord(provider=self.name, model_id=self.model_id, agent_id=request.agent_id, task=request.task,
                            input_tokens=100, output_tokens=40, latency_ms=5)
        if request.task == "architecture_extraction":
            out = ExtractionOutput(
                components=[ExtractedComponent(name="Checkout Service", type="service", evidence_excerpt="The checkout service writes orders")],
                relationships=[ExtractedRelationship(source="Checkout Service", target="main", label="writes orders",
                                                     evidence_excerpt="writes orders to the main PostgreSQL database")],
            )
        elif request.task == "agent_review":
            ids = re.findall(r"\[((?:TOOL|COMP):[^\]]+)\]", request.user_prompt)
            tool_ids = [i for i in ids if i.startswith("TOOL:")]
            cite = (tool_ids or ids)[:2]
            findings = []
            if cite:
                findings.append(FindingDraft(
                    title=f"{request.agent_id} concern backed by evidence", description="A concrete concern derived from cited evidence.",
                    category="General", severity=Severity.HIGH, confidence=0.8, evidence_level=EvidenceLevel.CONFIRMED,
                    evidence_ids=cite, recommendation="Remediate the cited configuration.",
                    principle="ACME-SEC-001 Network exposure", principle_origin="ORGANIZATIONAL_STANDARD",
                    reasoning_summary="Derived from cited tool output.",
                ))
                findings.append(FindingDraft(  # cites an ID that was never supplied: must be dropped and downgraded
                    title=f"{request.agent_id} invented evidence", description="Cites evidence that does not exist.", category="General",
                    severity=Severity.MEDIUM, evidence_level=EvidenceLevel.CONFIRMED, evidence_ids=["TOOL:TR-doesnotexist"],
                    recommendation="n/a",
                ))
            out = AgentReviewOutput(findings=findings, information_gaps=["No load-test data."], coverage_summary="fake coverage")
        elif request.task == "moderator_review":
            data = json.loads(request.user_prompt.split("\n", 1)[1])
            out = ModeratorReviewOutput(assessments=[ModeratorAssessment(finding_id=f["finding_id"], verdict="KEEP", reasoning="ok") for f in data])
        elif request.task == "rebuttal":
            out = RebuttalOutput(stance="MAINTAIN", argument="The cited configuration is explicit.", evidence_ids=re.findall(r"\[((?:TOOL|COMP):[^\]]+)\]", request.user_prompt)[:1])
        else:  # pragma: no cover
            raise AssertionError(request.task)
        return StructuredResponse(parsed=out, raw_text=out.model_dump_json(), usage=usage)


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_live_review_end_to_end_with_fake_provider(container):
    fake = FakeLiveProvider(container.settings_store.get())
    container.provider_for = lambda review: fake  # type: ignore[method-assign]
    project = container.tables.projects.put(Project(name="live"))
    container.ingestion.add_document(project.project_id, "design.md", DOC, "PRIMARY_ARCHITECTURE")
    container.ingestion.add_document(project.project_id, "infra.tf", TF, "INFRASTRUCTURE")
    container.ingestion.add_document(project.project_id, "arch.png", _png(), "DIAGRAM")
    review = container.reviews.create_review(ReviewCreate(project_id=project.project_id, name="Live", demo_mode=False, provider="anthropic"))
    assert review.mode == "live" and not review.simulated
    container.workflow.start(review.review_id)
    wait_idle(container, review.review_id)
    r = container.reviews.get(review.review_id)
    assert r.status == ReviewStatus.AWAITING_HUMAN, r.error

    model = container.reviews.get_model(review.review_id)
    names = {c.name: c for c in model.components}
    assert "Checkout Service" in names and names["Checkout Service"].provenance.get("type") is None  # LLM-added, not explicit
    vision = [c for c in model.components if c.component_id.startswith("vision:")]
    assert vision and all(c.confidence <= 0.5 and c.provenance["type"] == "INFERRED" for c in vision)
    assert any(n.level == "INFO" and "vision" in n.message for n in model.notes)

    findings = container.reviews.findings(review.review_id)
    assert findings and not any(f.simulated for f in findings)  # live results are never labelled simulated
    invented = [f for f in findings if "invented evidence" in f.title]
    assert invented and all(f.evidence_level == EvidenceLevel.NEEDS_VALIDATION for f in invented)  # downgraded
    backed = next(f for f in findings if "backed by evidence" in f.title and f.agent_id == "security")
    assert backed.evidence_level == EvidenceLevel.CONFIRMED and backed.source_references and backed.tool_result_refs
    assert backed.principle_origin.value == "ORGANIZATIONAL_STANDARD"  # ID exists in the knowledge base
    execs = container.reviews.executions(review.review_id, 1)
    assert all(e.usage and e.usage.input_tokens == 100 and e.usage.provider == "fakelive" for e in execs)

    # prompts: untrusted evidence is delimited and only supplied IDs are cited
    review_prompts = [p for t, p in fake.prompts if t == "agent_review"]
    assert len(review_prompts) == 6 and all("<<<BEGIN_UNTRUSTED_EVIDENCE>>>" in p for p in review_prompts)
    assert any(t == "image" for t, _ in fake.prompts)

    # dispute -> live rebuttal path
    f = finding_by_title(container, review.review_id, "security concern backed")
    container.decisions.record(review.review_id, DecisionCreate(finding_id=f.finding_id, decision_type=DecisionType.DISPUTE,
                                                                  justification="The database sits behind a private endpoint."))
    container.workflow.resume(review.review_id)
    wait_idle(container, review.review_id)
    after = container.reviews.get_finding(f.finding_id)
    assert after.status == FindingStatus.REBUTTED
    reb = container.tables.rebuttals.list(finding_id=f.finding_id)[0]
    assert reb.stance == "MAINTAIN" and not reb.simulated


def test_vision_is_not_attempted_for_providers_without_support(container):
    from backend.llm.provider_factory import create_provider

    mock = create_provider("mock", container.settings_store.get())
    project = container.tables.projects.put(Project(name="img"))
    container.ingestion.add_document(project.project_id, "arch.png", _png(), "DIAGRAM")
    arts = container.ingestion.list_for_project(project.project_id)
    docs = [(a, container.ingestion.parsed(a.artifact_id)) for a in arts]
    model = container.architecture.build_model(title="t", description="", environment_hint="Unknown", docs=docs, provider=mock)
    assert not model.components
    limitation = [n for n in model.notes if n.level == "LIMITATION" and "NOT interpreted" in n.message]
    assert limitation and "manual inspection" in limitation[0].message
