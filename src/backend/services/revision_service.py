"""Architecture revisions: artifact/model diffs and targeted re-review impact analysis."""
from __future__ import annotations

import re
from typing import Any

from ..core.exceptions import ConflictError, ValidationFailed
from ..models.architecture import ArchitectureModel
from ..models.domain import ALL_AGENT_IDS, ArchitectureArtifact, ArchitectureRevision, ChangeImpact, Review, ReviewStatus, ToolResult
from ..parsers.types import ParsedDocument
from ..tools import AGENT_RULES
from ..tools.dependency_analyzer import DependencyGraph
from .tool_service import build_tool_context, run_builtin

# property-name pattern -> specialist domains affected (documented in docs/agent-workflows.md)
PROPERTY_DOMAINS: list[tuple[re.Pattern[str], tuple[str, ...], str]] = [
    (re.compile(r"iam|policy|role|assume|principal|permission"), ("security", "compliance"), "IAM / privilege change"),
    (re.compile(r"security_group|ingress|egress|cidr|port|publicly|public_|acl|firewall|network_polic|listener|protocol|tls|ssl|https"),
     ("security",), "network exposure / transport change"),
    (re.compile(r"encrypt|kms|secret|password|credential|token"), ("security", "compliance"), "encryption / secrets change"),
    (re.compile(r"multi_az|failover|availability_zone|zone|subnet|replica|backup|retention_period|deletion_protection|min_size|desired|"
                r"probe|redrive|dlq|dead_letter|nat|region|replicas|hpa"), ("resilience",), "redundancy / recovery change"),
    (re.compile(r"multi_az|replica|nat|region|min_size|max_size|desired|node_count"), ("finops",), "capacity / redundancy cost driver"),
    (re.compile(r"instance_type|instance_class|node_type|allocated_storage|storage|lifecycle|retention_in_days|autoscal|tags|reserved"),
     ("finops",), "sizing / lifecycle change"),
    (re.compile(r"cache|instance_type|instance_class|timeout|concurrency|max_size|read_replica|iops|throughput|connection|queue|batch|"
                r"visibility|autoscal|replicas"), ("performance",), "capacity / latency change"),
    (re.compile(r"log|alarm|metric|monitor|trac|probe|health|deploy|rollback|alert|runbook|access_logs|limits|resources|image"),
     ("operability",), "observability / deployment change"),
    (re.compile(r"log|audit|trail|flow|retention|region|residency|tags|classification|backup|encrypt"), ("compliance",),
     "audit / retention / residency change"),
    (re.compile(r"cache"), ("operability",), "cache operations"),
    (re.compile(r"log|logging|audit"), ("security", "compliance", "operability"), "logging change"),
]
TYPE_DOMAINS: dict[str, tuple[str, ...]] = {
    "database": ("security", "resilience", "performance", "compliance", "finops"),
    "cache": ("performance", "operability", "finops"),
    "queue": ("resilience", "operability"),
    "load_balancer": ("security", "resilience", "performance"),
    "api_gateway": ("security", "performance"),
    "identity": ("security", "compliance"),
    "iam_policy": ("security", "compliance"),
    "object_storage": ("security", "compliance", "finops"),
    "nat_gateway": ("resilience", "finops"),
    "compute": ("resilience", "performance", "operability", "finops"),
    "service": ("resilience", "performance", "operability"),
    "monitoring": ("operability",),
    "log_group": ("operability", "finops"),
    "logging": ("operability", "security", "compliance"),
    "security_group": ("security",),
    "network": ("security", "resilience"),
    "subnet": ("resilience", "security"),
    "vpn": ("security", "resilience"),
    "external_system": ("resilience", "security", "performance"),
}
_NOTE_KEYWORDS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"(?i)\biam\b|role|policy|permission|credential|secret|encrypt|public|firewall|security group|tls|https|ssh"), ("security", "compliance")),
    (re.compile(r"(?i)multi-?az|replica|failover|backup|redundan|availability|nat|dlq|dead.?letter|disaster"), ("resilience",)),
    (re.compile(r"(?i)cost|instance size|right-?siz|lifecycle|retention|autoscal"), ("finops",)),
    (re.compile(r"(?i)cach|latency|throughput|performance|connection pool|load test"), ("performance",)),
    (re.compile(r"(?i)log|monitor|alarm|alert|trac|probe|runbook|deploy|rollback"), ("operability", "security", "compliance")),
    (re.compile(r"(?i)gdpr|compliance|audit|residency|retention policy"), ("compliance",)),
]
_IGNORED_KEYS = {"resource_type", "diagram_labels", "diagram_group"}


def _comparable(cfg: dict[str, Any]) -> dict[str, str]:
    return {k: repr(v) for k, v in cfg.items() if k not in _IGNORED_KEYS}


def diff_models(old: ArchitectureModel | None, new: ArchitectureModel) -> dict[str, Any]:
    old_c = {c.component_id: c for c in (old.components if old else [])}
    new_c = {c.component_id: c for c in new.components}
    added = sorted(set(new_c) - set(old_c))
    removed = sorted(set(old_c) - set(new_c))
    changed: list[str] = []
    props: list[str] = []
    for cid in sorted(set(new_c) & set(old_c)):
        a, b = _comparable(old_c[cid].configuration), _comparable(new_c[cid].configuration)
        keys = sorted({k for k in set(a) | set(b) if a.get(k) != b.get(k)})
        if old_c[cid].type != new_c[cid].type:
            keys.append("type")
        if keys:
            changed.append(cid)
            props += [f"{cid}:{k}" for k in keys]
    functional = lambda r: r.relationship_type not in ("network_membership", "uses_identity", "uses_encryption_key", "monitors")  # noqa: E731
    old_rel = {(r.source, r.target, r.relationship_type, bool(r.synchronous)) for r in (old.relationships if old else []) if functional(r)}
    new_rel = {(r.source, r.target, r.relationship_type, bool(r.synchronous)) for r in new.relationships if functional(r)}
    return {"added": added, "removed": removed, "changed": changed, "properties": props,
            "relationships_added": sorted(new_rel - old_rel), "relationships_removed": sorted(old_rel - new_rel)}


def compute_impact(
    *, revision: int, old_model: ArchitectureModel | None, new_model: ArchitectureModel,
    old_docs: list[tuple[ArchitectureArtifact, ParsedDocument]], new_docs: list[tuple[ArchitectureArtifact, ParsedDocument]],
    old_tools: list[ToolResult], new_tools: list[ToolResult], change_notes: str, enabled_agents: list[str],
) -> ChangeImpact:
    old_by_name = {a.filename.lower(): a for a, _ in old_docs}
    new_by_name = {a.filename.lower(): (a, d) for a, d in new_docs}
    changed_art, added_art, removed_art, same_art = [], [], [], []
    unstructured_changes: list[str] = []
    for name, (art, doc) in new_by_name.items():
        prev = old_by_name.get(name)
        if prev is None:
            added_art.append(art.filename)
            if not (doc.resources or doc.nodes):
                unstructured_changes.append(art.filename)
        elif prev.sha256 != art.sha256:
            changed_art.append(art.filename)
            if not (doc.resources or doc.nodes):
                unstructured_changes.append(art.filename)
        else:
            same_art.append(art.filename)
    removed_art = [a.filename for n, a in old_by_name.items() if n not in new_by_name]
    diff = diff_models(old_model, new_model)
    reasons: dict[str, list[str]] = {}

    def add(agents: tuple[str, ...] | list[str], why: str) -> None:
        for a in agents:
            reasons.setdefault(a, [])
            if why not in reasons[a]:
                reasons[a].append(why)

    for entry in diff["properties"]:
        cid, key = entry.split(":", 1)
        for pattern, agents, why in PROPERTY_DOMAINS:
            if pattern.search(key.lower()):
                add(agents, f"{cid}: {key} ({why})")
    comp_types = {c.component_id: c.type for c in new_model.components}
    comp_types.update({c.component_id: c.type for c in (old_model.components if old_model else [])})
    for cid in diff["added"]:
        add(TYPE_DOMAINS.get(comp_types.get(cid, ""), ()), f"{cid} added")
    for cid in diff["removed"]:
        add(TYPE_DOMAINS.get(comp_types.get(cid, ""), ()), f"{cid} removed")
    changed_rels = list(diff["relationships_added"]) + list(diff["relationships_removed"])
    if changed_rels:
        add(("resilience", "operability"), "dependency relationships changed")
    if any(rel[3] for rel in changed_rels):
        add(("performance",), "synchronous call path changed")
    # tool-observation delta: a rule that appears/disappears makes its owning agents relevant
    before = {(t.rule_id, tuple(sorted(t.affected_component_ids))) for t in old_tools}
    after = {(t.rule_id, tuple(sorted(t.affected_component_ids))) for t in new_tools}
    for rule_id, comps in sorted(before ^ after):
        owners = [a for a, rules in AGENT_RULES.items() if rule_id in rules]
        add(owners, f"deterministic check {rule_id} changed result for {', '.join(comps[:2])}")
    for pattern, agents in _NOTE_KEYWORDS:
        if change_notes and pattern.search(change_notes):
            add(agents, "mentioned in change notes")
    graph = DependencyGraph(new_model)
    impacted = graph.impacted_after_revision(diff["added"] + diff["changed"])
    confidence = "HIGH"
    broadened = False
    summary_bits: list[str] = []
    if unstructured_changes and not any(diff[k] for k in ("added", "removed", "changed")):
        confidence = "LOW"
        summary_bits.append(f"Changed material has no structured diff ({', '.join(unstructured_changes[:3])}); impact cannot be determined confidently.")
    if not reasons and (changed_art or added_art or removed_art):
        confidence = "LOW"
        summary_bits.append("Artifacts changed but no component-level difference was detected; broadening the review.")
    affected = [a for a in ALL_AGENT_IDS if a in reasons and a in enabled_agents]
    if confidence == "LOW":
        affected = list(enabled_agents)
        broadened = True
        for a in affected:
            reasons.setdefault(a, ["impact undetermined - broadened rather than skipped"])
    if not (changed_art or added_art or removed_art) and not change_notes.strip():
        summary_bits.append("No artifact changes detected.")
    if not summary_bits:
        summary_bits.append(
            f"{len(diff['added'])} component(s) added, {len(diff['removed'])} removed, {len(diff['changed'])} changed; "
            f"{len(impacted)} component(s) impacted through dependencies. Agents to re-run: {', '.join(affected) or 'none'}.")
    return ChangeImpact(
        revision=revision, changed_artifacts=changed_art, added_artifacts=added_art, removed_artifacts=removed_art,
        unchanged_artifacts=same_art, changed_components=diff["changed"], added_components=diff["added"],
        removed_components=diff["removed"], changed_properties=diff["properties"], impacted_components=impacted,
        affected_agents=affected, confidence=confidence, broadened=broadened,
        reasons={a: reasons[a] for a in affected if a in reasons}, summary=" ".join(summary_bits),
    )


class RevisionService:
    """Creates architecture revisions (new artifact set + model + impact) for a review."""

    def __init__(self, container: Any) -> None:
        self.c = container

    def create_revision(
        self, review_id: str, change_notes: str, files: list[tuple[str, bytes, str | None]], *, source: str = "upload",
    ) -> ArchitectureRevision:
        c = self.c
        review: Review = c.reviews.get(review_id)
        if review.status != ReviewStatus.AWAITING_HUMAN:
            raise ConflictError(f"Revisions can only be submitted while the review is AWAITING_HUMAN (currently {review.status.value}).")
        if not files and not change_notes.strip():
            raise ValidationFailed("Provide at least one revised file or change notes.")
        revisions = c.reviews.revisions(review_id)
        number = (revisions[-1].number if revisions else review.architecture_revision) + 1
        rejected: list[dict[str, str]] = []
        for name, data, role in files:
            try:
                c.ingestion.add_document(review.project_id, name, data, role, revision=number, supersede=True)
            except Exception as exc:
                rejected.append({"filename": name, "error": getattr(exc, "message", str(exc))})
        if rejected and len(rejected) == len(files) and files:
            raise ValidationFailed("None of the supplied files could be accepted.", details={"rejected": rejected})
        arts = [a for a in c.ingestion.list_for_project(review.project_id) if a.parse_status in ("PARSED", "PARTIAL")]
        new_docs = [(a, c.ingestion.parsed(a.artifact_id)) for a in arts]
        new_docs = [(a, d) for a, d in new_docs if d is not None]
        prev_rev = next((r for r in revisions if r.number == review.architecture_revision), None)
        old_docs = []
        for aid in (prev_rev.artifact_ids if prev_rev else []):
            art = c.tables.artifacts.get(aid)
            parsed = c.ingestion.parsed(aid) if art else None
            if art and parsed:
                old_docs.append((art, parsed))
        old_model = c.reviews.get_model(review_id, review.architecture_revision)
        provider = c.provider_for(review)
        new_model = c.architecture.build_model(
            title=review.name, description=review.description, environment_hint=review.cloud_environment, docs=new_docs,
            provider=provider, revision=number, use_llm=review.mode == "live",
        )
        ctx = build_tool_context(review_id, new_model, new_docs)
        new_tools, _ = run_builtin(ctx)
        old_tools = c.reviews.tool_results(review_id, review.architecture_revision)
        impact = compute_impact(
            revision=number, old_model=old_model, new_model=new_model, old_docs=old_docs, new_docs=new_docs,
            old_tools=old_tools, new_tools=new_tools, change_notes=change_notes, enabled_agents=review.enabled_agents,
        )
        c.reviews.save_model(review_id, new_model)
        c.reviews.save_tool_results(review_id, number, new_tools)
        rev = ArchitectureRevision(
            review_id=review_id, number=number, artifact_ids=[a.artifact_id for a, _ in new_docs], change_notes=change_notes,
            source=source, impact=impact,
        )
        c.tables.revisions.put(rev)
        c.reviews.emit(review_id, "revision_received",
                       f"Architecture revision {number} received ({len(files)} file(s)). Affected agents: {', '.join(impact.affected_agents) or 'none'}.",
                       round_no=review.current_round, payload={"revision_id": rev.revision_id, "impact": impact.model_dump(mode="json"),
                                                               "rejected_files": rejected})
        c.snapshots.save_review_snapshot(review_id, f"revision-{number}")
        return rev
