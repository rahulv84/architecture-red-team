"""Glue between parsed artifacts and the deterministic tool framework."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ArchitectureArtifact, ToolResult, ToolRunSummary
from ..parsers.types import ParsedDocument
from ..tools import AGENT_RULES, ToolContext, dedupe_results, run_rules
from ..tools.checkov_adapter import run_checkov

ALL_RULE_IDS = sorted({r for rules in AGENT_RULES.values() for r in rules})
Docs = list[tuple[ArchitectureArtifact, ParsedDocument]]


def build_tool_context(review_id: str, model: ArchitectureModel, docs: Docs) -> ToolContext:
    resources = [r for _, d in docs for r in d.resources]
    names = {a.artifact_id: a.filename for a, _ in docs}
    texts = {a.artifact_id: d.text for a, d in docs if d.resources}
    params: set[str] = set()
    for _, d in docs:
        params |= set(d.metadata.get("parameters", []) or [])
    return ToolContext(review_id=review_id, resources=resources, model=model, artifact_names=names, artifact_texts=texts, parameters=params)


def run_builtin(ctx: ToolContext, rule_ids: list[str] | None = None) -> tuple[list[ToolResult], list[ToolRunSummary]]:
    results, summaries = run_rules(rule_ids or ALL_RULE_IDS, ctx)
    return dedupe_results(results), summaries


def run_checkov_for(docs: Docs, enabled: bool, timeout: int) -> tuple[list[ToolResult], ToolRunSummary]:
    if not enabled:
        return [], ToolRunSummary(tool_id="checkov", tool_name="Checkov", status="NOT_EXECUTED", detail="Disabled in settings.")
    files = [(a.artifact_id, a.filename, d.kind, d.text) for a, d in docs if d.resources]
    return run_checkov(files, timeout)
