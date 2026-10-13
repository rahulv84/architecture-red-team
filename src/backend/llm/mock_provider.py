"""Deterministic offline provider used by SIMULATED DEMO MODE and by the test-suite.

The mock never calls a network service.  It answers structured tasks from versioned
fixture files (``demo/seeded_findings/<scenario>.json``) whose flaws are only emitted when
their ``applies_when`` conditions match the *current* architecture model / tool results.
That keeps the real graph, tools, moderation and decision workflow exercised end to end,
while ensuring simulated output can never be mistaken for model-discovered findings
(every mock finding is tagged ``simulated`` by the agent runner).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..core.exceptions import ProviderError
from ..models.domain import AgentReviewOutput, FindingDraft, UsageRecord
from .base_provider import (
    LLMProvider,
    ProviderCapabilities,
    ProviderValidation,
    RawCompletion,
    StructuredRequest,
    StructuredResponse,
)
from .contracts import ModeratorAssessment, ModeratorReviewOutput, RebuttalOutput

_FLAW_ID_RE = re.compile(r"fixture ([A-Z0-9\-]+)")
_CONDITION_KEYS = {"always", "tool_rule_ids", "tool_component_contains", "components", "model_empty", "model_nonempty", "nfr_category", "region_any"}


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, settings: Settings, fixtures_dir: Path | None = None, latency: float | None = None) -> None:
        super().__init__(settings)
        self._fixtures_dir = fixtures_dir or (settings.demo_dir / "seeded_findings")
        self._latency = latency if latency is not None else float(getattr(settings, "mock_latency_seconds", 0.0))
        self._cache: dict[str, dict[str, Any]] = {}

    @property
    def model_id(self) -> str:
        return "mock-deterministic"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name, model_id=self.model_id, structured_output=True, vision=False, simulated=True,
            notes=["SIMULATED: responses come from demo fixtures; no model is invoked.",
                   "No image understanding is available in simulated mode."],
        )

    def validate_configuration(self) -> ProviderValidation:
        ok = self._fixtures_dir.exists()
        return ProviderValidation(
            ok=ok, provider=self.name, model_id=self.model_id,
            message="Deterministic offline provider ready." if ok else f"Fixture directory missing: {self._fixtures_dir}",
            capabilities=self.capabilities(),
        )

    def _complete(self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None, schema_name: str) -> RawCompletion:
        raise ProviderError("The mock provider only serves structured demo tasks.")

    # ---- fixtures ----------------------------------------------------------
    def fixture(self, scenario_id: str | None) -> dict[str, Any]:
        if not scenario_id:
            return {"flaws": []}
        if scenario_id not in self._cache:
            path = self._fixtures_dir / f"{scenario_id}.json"
            self._cache[scenario_id] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"flaws": []}
        return self._cache[scenario_id]

    # ---- structured generation ----------------------------------------------
    def generate_structured(self, request: StructuredRequest) -> StructuredResponse:
        started = time.monotonic()
        if self._latency > 0:
            time.sleep(self._latency * (0.6 + (sum(map(ord, request.agent_id or "m")) % 7) / 10))
        ctx = request.context
        scenario = ctx.get("scenario_id")
        if request.task == "agent_review":
            parsed: Any = self._agent_review(request.agent_id or "", scenario, ctx)
        elif request.task == "rebuttal":
            parsed = self._rebuttal(scenario, ctx)
        elif request.task == "moderator_review":
            parsed = self._moderator(scenario, ctx)
        else:
            raise ProviderError(f"Mock provider has no handler for task '{request.task}'.")
        usage = UsageRecord(
            provider=self.name, model_id=self.model_id, agent_id=request.agent_id, task=request.task,
            input_tokens=None, output_tokens=None, latency_ms=int((time.monotonic() - started) * 1000),
            status="SIMULATED",
        )
        return StructuredResponse(parsed=parsed, raw_text=parsed.model_dump_json(), usage=usage)

    # ---- condition evaluation ------------------------------------------------
    @staticmethod
    def _component_matches(comp: dict[str, Any], matcher: dict[str, Any]) -> bool:
        if "type" in matcher and comp.get("type") != matcher["type"]:
            return False
        if "name_contains" in matcher and matcher["name_contains"].lower() not in (comp.get("name", "") + " " + comp.get("component_id", "")).lower():
            return False
        cfg = comp.get("configuration") or {}
        for key, val in (matcher.get("config_equals") or {}).items():
            if cfg.get(key) != val:
                return False
        for key in matcher.get("config_missing") or []:
            if key in cfg:
                return False
        return all(key in cfg for key in matcher.get("config_present") or [])

    def _evaluate(self, flaw: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
        """Return (applies, affected component ids, tool result ids)."""
        cond = flaw.get("applies_when") or {"always": True}
        unknown = set(cond) - _CONDITION_KEYS
        if unknown:
            raise ProviderError(f"Fixture flaw {flaw.get('flaw_id')} uses unknown applies_when keys: {sorted(unknown)}")
        model = ctx.get("architecture_model") or {}
        comps = model.get("components") or []
        tool_results = [t for t in ctx.get("tool_results", []) if t.get("outcome", "VIOLATION") == "VIOLATION"]
        affected: list[str] = []
        tools: list[str] = []
        ok = True
        if "tool_rule_ids" in cond:
            matched = [t for t in tool_results if t["rule_id"] in cond["tool_rule_ids"]]
            name_filter = cond.get("tool_component_contains")
            if name_filter:
                matched = [t for t in matched if any(name_filter.lower() in c.lower() for c in t.get("affected_component_ids", []))]
            if not matched:
                ok = False
            for t in matched:
                tools.append(t["tool_result_id"])
                affected += t.get("affected_component_ids", [])
        if ok and "components" in cond:
            for matcher in cond["components"]:
                hit = [c for c in comps if self._component_matches(c, matcher)]
                if not hit:
                    ok = False
                    break
                affected += [c["component_id"] for c in hit]
        if ok and "region_any" in cond:
            regions = model.get("deployment_regions") or []
            if not any(r in regions for r in cond["region_any"]):
                ok = False
            elif not affected:
                affected = [c["component_id"] for c in comps if any(r in str(c.get("configuration", {})) for r in cond["region_any"])]
        if ok and "model_empty" in cond:
            for fld in cond["model_empty"]:
                if model.get(fld):
                    ok = False
        if ok and "model_nonempty" in cond:
            for fld in cond["model_nonempty"]:
                if not model.get(fld):
                    ok = False
        if ok and "nfr_category" in cond:
            if not any(n.get("category") == cond["nfr_category"] for n in model.get("nfrs", [])):
                ok = False
        seen: set[str] = set()
        affected = [a for a in affected if not (a in seen or seen.add(a))]
        return ok, affected, sorted(set(tools))

    # ---- tasks -----------------------------------------------------------------
    def _agent_review(self, agent_id: str, scenario: str | None, ctx: dict[str, Any]) -> AgentReviewOutput:
        fx = self.fixture(scenario)
        catalog = ctx.get("evidence_catalog", [])
        findings: list[FindingDraft] = []
        by_id = {c["id"]: c for c in catalog}
        names = {c["component_id"]: c.get("name", c["component_id"]) for c in (ctx.get("architecture_model") or {}).get("components", [])}
        for flaw in fx.get("flaws", []):
            if flaw.get("agent_id") != agent_id:
                continue
            ok, affected, tool_ids = self._evaluate(flaw, ctx)
            if not ok:
                continue
            evidence: list[str] = [f"TOOL:{t}" for t in tool_ids if f"TOOL:{t}" in by_id]
            evidence += [f"COMP:{a}" for a in affected if f"COMP:{a}" in by_id]
            hints = [h.lower() for h in flaw.get("evidence_hints", [])]
            if hints:
                docs = [c for c in catalog if c["kind"] == "DOC" and any(h in c.get("text", "").lower() for h in hints)]
                evidence += [d["id"] for d in docs[:2]]
            for kb in flaw.get("knowledge_hints", []):
                match = next((c for c in catalog if c["kind"] == "KB" and kb.lower() in (c.get("text", "") + c.get("title", "")).lower()), None)
                if match:
                    evidence.append(match["id"])
            if flaw.get("seed_type") == "unsupported_claim":
                evidence = []  # intentionally cites nothing verifiable
            component_names = ", ".join(names.get(a, a) for a in affected[:4]) or "the architecture"
            fmt = {"components": component_names}
            findings.append(
                FindingDraft(
                    title=flaw["title"].format(**fmt),
                    description=flaw["description"].format(**fmt),
                    category=flaw.get("category", "General"),
                    severity=flaw["severity"],
                    confidence=flaw.get("confidence", 0.8),
                    evidence_level=flaw["evidence_level"],
                    affected_components=affected if not flaw.get("affected_components") else flaw["affected_components"],
                    evidence_ids=list(dict.fromkeys(evidence)),
                    scenario=flaw.get("scenario", "").format(**fmt),
                    impact=flaw.get("impact", "").format(**fmt),
                    recommendation=flaw["recommendation"].format(**fmt),
                    principle=flaw.get("principle", ""),
                    principle_origin=flaw.get("principle_origin", "GENERAL_PRACTICE"),
                    validation_method=flaw.get("validation_method", ""),
                    assumptions=flaw.get("assumptions", []),
                    reasoning_summary=f"SIMULATED analysis (fixture {flaw['flaw_id']}): {flaw.get('reasoning', 'matched seeded condition against extracted architecture facts.')}",
                )
            )
        gaps = (fx.get("information_gaps") or {}).get(agent_id, [])
        return AgentReviewOutput(
            findings=findings, information_gaps=gaps,
            coverage_summary=f"SIMULATED {agent_id} review of scenario '{scenario}': {len(findings)} seeded concern(s) matched.",
        )

    def _flaw_for_finding(self, scenario: str | None, ctx: dict[str, Any]) -> dict[str, Any] | None:
        fx = self.fixture(scenario)
        flaws = {f["flaw_id"]: f for f in fx.get("flaws", [])}
        agent = ctx.get("agent_id")
        candidates: list[str] = []
        for text in ctx.get("reasoning_summaries", []):
            candidates += _FLAW_ID_RE.findall(text or "")
        for fid in candidates:
            if fid in flaws and flaws[fid].get("agent_id") == agent:
                return flaws[fid]
        for fid in candidates:
            if fid in flaws:
                return flaws[fid]
        return None

    def _rebuttal(self, scenario: str | None, ctx: dict[str, Any]) -> RebuttalOutput:
        flaw = self._flaw_for_finding(scenario, ctx)
        justification = (ctx.get("dispute_justification") or "").lower()
        catalog_ids = [c["id"] for c in ctx.get("evidence_catalog", [])][:3]
        if flaw and flaw.get("rebuttal"):
            reb = flaw["rebuttal"]
            for kw in reb.get("concede_if_justification_contains", []):
                if kw.lower() in justification:
                    return RebuttalOutput(
                        stance="CONCEDE",
                        argument=f"SIMULATED: {reb.get('concede_argument', 'The architect supplied information that resolves the concern; withdrawing the finding.')}",
                        evidence_ids=catalog_ids[:1],
                    )
            stance = reb.get("stance", "MAINTAIN")
            return RebuttalOutput(
                stance=stance,
                argument="SIMULATED: " + reb.get("argument", "The cited evidence still shows the condition described in the finding."),
                evidence_ids=catalog_ids,
                revised_severity=reb.get("revised_severity"),
                open_questions=reb.get("open_questions", []),
            )
        return RebuttalOutput(
            stance="MAINTAIN",
            argument="SIMULATED: The supplied justification does not change the evidence cited in the original finding; "
                     "please provide configuration or documentation that demonstrates the mitigating control.",
            evidence_ids=catalog_ids,
        )

    def _moderator(self, scenario: str | None, ctx: dict[str, Any]) -> ModeratorReviewOutput:
        fx = self.fixture(scenario)
        flaws = {f["flaw_id"]: f for f in fx.get("flaws", [])}
        assessments: list[ModeratorAssessment] = []
        for f in ctx.get("findings", []):
            ids = _FLAW_ID_RE.findall(f.get("reasoning_summary", ""))
            flaw = next((flaws[i] for i in ids if i in flaws), None)
            verdict = (flaw or {}).get("moderator_verdict", "KEEP")
            note = (flaw or {}).get("moderator_note") or "Evidence cross-checked against extracted facts and deterministic tool output."
            assessments.append(ModeratorAssessment(finding_id=f["finding_id"], verdict=verdict, reasoning=f"SIMULATED: {note}"))
        return ModeratorReviewOutput(
            assessments=assessments,
            conflicting_interpretations=list(fx.get("conflicts", [])),
            unsupported_assumptions=[],
            summary=f"SIMULATED moderation of {len(assessments)} finding(s).",
        )
