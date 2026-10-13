"""Deterministic tool framework: context, helpers, rule registry and runner.

Every check parses *structured* resource definitions (Terraform, CloudFormation,
Kubernetes) or the extracted architecture model.  A check never reports a vulnerability
because a keyword is merely absent from free text.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..models.architecture import ArchitectureModel
from ..models.domain import EvidenceLevel, Severity, SourceReference, ToolResult, ToolRunSummary
from ..parsers.types import IacResource

_MISSING = object()
_UNRESOLVED_RE = re.compile(r"\$\{\s*(var|local|data|module|each|count|path|terraform)\b|^\s*\{\{resolve:|^var\.|^local\.")


@dataclass
class ToolContext:
    """Inputs for deterministic checks (all JSON-safe, no clients or open files)."""

    review_id: str
    resources: list[IacResource]
    model: ArchitectureModel
    artifact_names: dict[str, str] = field(default_factory=dict)
    artifact_texts: dict[str, str] = field(default_factory=dict)
    parameters: set[str] = field(default_factory=set)  # CloudFormation template parameters

    def by_type(self, *types: str) -> list[IacResource]:
        wanted = set(types)
        return [r for r in self.resources if r.resource_type in wanted]

    def by_address(self, address: str) -> IacResource | None:
        for r in self.resources:
            if r.address == address:
                return r
        return None

    def frameworks(self) -> set[str]:
        return {r.framework for r in self.resources}


# --------------------------------------------------------------------------- value helpers
def get_path(props: Any, *names: str, default: Any = _MISSING) -> Any:
    """Return the first property found among ``names`` (case-insensitive, dotted paths allowed)."""
    for name in names:
        cur: Any = props
        ok = True
        for part in name.split("."):
            cur = _dict_first(cur)
            if not isinstance(cur, dict):
                ok = False
                break
            key = next((k for k in cur if str(k).lower() == part.lower()), None)
            if key is None:
                ok = False
                break
            cur = cur[key]
        if ok:
            return cur
    return default


def has(value: Any) -> bool:
    return value is not _MISSING


def _dict_first(value: Any) -> Any:
    """HCL nested blocks arrive as a one-element list; unwrap."""
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return value


def as_list(value: Any) -> list[Any]:
    if value is _MISSING or value is None:
        return []
    return value if isinstance(value, list) else [value]


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "enabled", "1"):
            return True
        if low in ("false", "no", "disabled", "0"):
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def as_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
    except (ValueError, TypeError):
        return None
    return None


def is_unresolved(value: Any) -> bool:
    """True for references we cannot evaluate statically (variables, data sources, dynamic refs)."""
    if isinstance(value, dict):
        return any(k in ("Ref", "Fn::GetAtt", "Fn::Sub", "Fn::ImportValue", "Fn::If", "Fn::Join") for k in value)
    return isinstance(value, str) and bool(_UNRESOLVED_RE.search(value))


def stringify(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def references_in(value: Any) -> set[str]:
    """Terraform-style resource addresses referenced inside an expression/value tree."""
    found: set[str] = set()
    if isinstance(value, dict):
        for v in value.values():
            found |= references_in(v)
    elif isinstance(value, list):
        for v in value:
            found |= references_in(v)
    elif isinstance(value, str) and "${" in value:
        for m in re.finditer(r"\b(data\.[a-z0-9_]+\.[A-Za-z0-9_\-]+|module\.[A-Za-z0-9_\-]+|[a-z][a-z0-9]*_[a-z0-9_]+\.[A-Za-z0-9_\-]+)", value):
            found.add(m.group(1))
    return found


# --------------------------------------------------------------------------- result builders
def evidence_ref(res: IacResource, excerpt: str | None = None) -> SourceReference:
    return SourceReference(
        artifact_id=res.artifact_id, filename=res.filename, resource_path=res.address,
        line_start=res.line, line_end=res.line, excerpt=excerpt,
    )


def make_result(
    *, tool_id: str, tool_name: str, rule_id: str, domain: str, res: IacResource | None, title: str, message: str,
    observed: Any = None, expected: Any = None, level: EvidenceLevel = EvidenceLevel.CONFIRMED,
    severity: Severity = Severity.MEDIUM, components: Iterable[str] | None = None, excerpt: str | None = None,
    outcome: str = "VIOLATION", qualifier: str = "",
) -> ToolResult:
    comp_ids = list(components) if components is not None else ([res.address] if res else [])
    key = f"{rule_id}|{res.artifact_id if res else ''}|{res.address if res else ','.join(sorted(comp_ids))}|{qualifier}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return ToolResult(
        tool_result_id=f"TR-{digest}",
        tool_id=tool_id,
        tool_name=tool_name,
        source_artifact_id=res.artifact_id if res else None,
        rule_id=rule_id,
        domain=domain,
        title=title,
        message=message,
        outcome=outcome,
        observed_value=stringify(observed) if observed is not None else None,
        expected_value=stringify(expected) if expected is not None else None,
        evidence_level=level,
        severity_hint=severity,
        affected_component_ids=comp_ids,
        evidence_reference=evidence_ref(res, excerpt) if res else None,
        raw_result_reference=f"{res.filename}:{res.address}" if res else None,
    )


# --------------------------------------------------------------------------- registry
CheckFn = Callable[[ToolContext], tuple[list[ToolResult], int]]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    tool_id: str
    tool_name: str
    domain: str
    title: str
    fn: CheckFn


REGISTRY: dict[str, Rule] = {}


def register(rule_id: str, tool_id: str, tool_name: str, domain: str, title: str) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        REGISTRY[rule_id] = Rule(rule_id, tool_id, tool_name, domain, title, fn)
        return fn

    return deco


def run_rules(rule_ids: Iterable[str], ctx: ToolContext) -> tuple[list[ToolResult], list[ToolRunSummary]]:
    """Execute registered rules.  A crashing rule is reported as an ERROR summary, not hidden."""
    results: list[ToolResult] = []
    per_tool: dict[str, ToolRunSummary] = {}
    for rule_id in rule_ids:
        rule = REGISTRY.get(rule_id)
        if rule is None:
            continue
        summary = per_tool.setdefault(rule.tool_id, ToolRunSummary(tool_id=rule.tool_id, tool_name=rule.tool_name, status="EXECUTED"))
        try:
            found, checked = rule.fn(ctx)
        except Exception as exc:  # a rule bug must surface as an explicit tool error
            summary.status = "ERROR"
            summary.detail = f"{summary.detail}; {rule_id} failed: {exc.__class__.__name__}: {exc}".strip("; ")
            continue
        summary.resources_checked += checked
        summary.violations += len(found)
        results.extend(found)
    return results, list(per_tool.values())


def dedupe_results(results: Iterable[ToolResult]) -> list[ToolResult]:
    seen: dict[str, ToolResult] = {}
    for r in results:
        seen.setdefault(r.tool_result_id, r)
    return list(seen.values())
