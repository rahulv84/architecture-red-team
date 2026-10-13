"""Deterministic dependency-graph analysis over the architecture model.

All traversals iterate in sorted order so results are reproducible across runs.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from ..models.architecture import ArchitectureModel
from ..models.domain import EvidenceLevel, Severity, ToolResult
from .base import ToolContext, make_result, register

TOOL_ID, TOOL_NAME = "dependency_analyzer", "Architecture dependency analyzer"
CRITICAL_TYPES = {"database", "cache", "queue", "topic", "stream", "api_gateway", "load_balancer", "identity", "secret_store", "gateway"}
OBSERVER_TYPES = {"monitoring", "logging", "log_group"}
NON_FUNCTIONAL_RELATIONS = {"network_membership", "uses_identity", "uses_encryption_key", "monitors"}
STATEFUL_TYPES = {"database", "cache", "queue", "stream", "file_storage", "object_storage"}


class DependencyGraph:
    """Directed graph where an edge ``a -> b`` means *a depends on / invokes b*."""

    def __init__(self, model: ArchitectureModel) -> None:
        self.nodes: set[str] = {c.component_id for c in model.components}
        self.forward: dict[str, set[str]] = {n: set() for n in self.nodes}
        self.reverse: dict[str, set[str]] = {n: set() for n in self.nodes}
        self.sync_edges: set[tuple[str, str]] = set()
        for comp in model.components:
            if comp.type in OBSERVER_TYPES:
                continue
            for dep in comp.dependencies:
                self._add(comp.component_id, dep, True)
        types = {c.component_id: c.type for c in model.components}
        for rel in model.relationships:
            if rel.relationship_type in NON_FUNCTIONAL_RELATIONS or types.get(rel.source) in OBSERVER_TYPES:
                continue  # observation edges are not request paths
            src, dst = (rel.target, rel.source) if rel.relationship_type == "hosts" else (rel.source, rel.target)
            self._add(src, dst, rel.synchronous is not False and rel.relationship_type not in ("publishes", "subscribes"))

    def _add(self, src: str, dst: str, sync: bool) -> None:
        if src not in self.nodes or dst not in self.nodes or src == dst:
            return
        self.forward[src].add(dst)
        self.reverse[dst].add(src)
        if sync:
            self.sync_edges.add((src, dst))

    # ---- queries ---------------------------------------------------------
    def direct_dependencies(self, node: str) -> list[str]:
        return sorted(self.forward.get(node, ()))

    def reverse_dependencies(self, node: str) -> list[str]:
        return sorted(self.reverse.get(node, ()))

    def _bfs(self, start: str, adjacency: dict[str, set[str]]) -> list[str]:
        seen: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in sorted(adjacency.get(cur, ())):
                if nxt not in seen and nxt != start:
                    seen.add(nxt)
                    queue.append(nxt)
        return sorted(seen)

    def transitive_dependencies(self, node: str) -> list[str]:
        return self._bfs(node, self.forward)

    def failure_propagation(self, node: str) -> list[str]:
        """Components that (transitively) depend on ``node`` and are affected if it fails."""
        return self._bfs(node, self.reverse)

    def shared_upstream(self, nodes: list[str]) -> list[str]:
        sets = [set(self.transitive_dependencies(n)) | set(self.direct_dependencies(n)) for n in nodes]
        return sorted(set.intersection(*sets)) if sets else []

    def single_dependency_bottlenecks(self, min_dependents: int = 2) -> list[tuple[str, list[str]]]:
        return [(n, self.reverse_dependencies(n)) for n in sorted(self.nodes) if len(self.reverse[n]) >= min_dependents]

    def impacted_after_revision(self, changed: list[str]) -> list[str]:
        impacted: set[str] = set(c for c in changed if c in self.nodes)
        for c in list(impacted):
            impacted |= set(self.failure_propagation(c))
        return sorted(impacted)

    def longest_sync_chain(self) -> list[str]:
        """Longest simple path along synchronous edges (cycle-safe, deterministic)."""
        best: list[str] = []
        memo: dict[str, list[str]] = {}

        def dfs(node: str, visiting: frozenset[str]) -> list[str]:
            if node in memo and not visiting:
                return memo[node]
            longest = [node]
            for nxt in sorted(self.forward[node]):
                if (node, nxt) in self.sync_edges and nxt not in visiting:
                    cand = [node] + dfs(nxt, visiting | {node})
                    if len(cand) > len(longest):
                        longest = cand
            return longest

        for start in sorted(self.nodes - {n for n in self.nodes if any((p, n) in self.sync_edges for p in self.reverse[n])} or self.nodes):
            path = dfs(start, frozenset())
            if len(path) > len(best):
                best = path
        return best


def _redundancy_known(comp: Any) -> bool:
    cfg = comp.configuration
    keys = {"multi_az", "replicas", "replica_count", "cluster", "high_availability", "redundant", "instances", "node_count"}
    return any(k in cfg for k in keys)


@register("RES-SPOF-001", TOOL_ID, TOOL_NAME, "resilience", "Shared component with no redundancy evidence")
def spof(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    model = ctx.model
    graph = DependencyGraph(model)
    iac_ids = {r.address for r in ctx.resources}
    out: list[ToolResult] = []
    for comp_id, dependents in graph.single_dependency_bottlenecks(2):
        comp = model.component(comp_id)
        if comp is None or comp.type not in CRITICAL_TYPES or comp_id in iac_ids or _redundancy_known(comp):
            continue
        res = make_result(
            tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id="RES-SPOF-001", domain="resilience", res=None,
            title="Shared dependency without documented redundancy",
            message=f"{comp.name} is used by {len(dependents)} components ({', '.join(dependents[:4])}) and the architecture material "
                    "states no redundancy (replicas, multi-AZ, clustering) for it.",
            observed=f"{len(dependents)} dependents; redundancy not described", expected="Documented redundancy / failover for shared critical components",
            level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.MEDIUM, components=[comp_id] + dependents,
            qualifier=comp_id,
        )
        if comp.source_references:
            res.evidence_reference = comp.source_references[0]
        out.append(res)
    return out, len(graph.nodes)


@register("PERF-CHAIN-001", TOOL_ID, TOOL_NAME, "performance", "Long synchronous dependency chain")
def long_sync_chain(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    graph = DependencyGraph(ctx.model)
    chain = graph.longest_sync_chain()
    if len(chain) < 4:
        return [], len(graph.nodes)
    names = [(ctx.model.component(c).name if ctx.model.component(c) else c) for c in chain]
    res = make_result(
        tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id="PERF-CHAIN-001", domain="performance", res=None,
        title="Synchronous call chain spans many tiers",
        message=f"A synchronous request path crosses {len(chain)} components: {' -> '.join(names)}. End-to-end latency and availability multiply across hops.",
        observed=f"chain length {len(chain)}", expected="Short synchronous paths; asynchronous decoupling for non-critical hops",
        level=EvidenceLevel.INFERRED, severity=Severity.MEDIUM, components=chain, qualifier="|".join(chain),
    )
    first = ctx.model.component(chain[0])
    if first and first.source_references:
        res.evidence_reference = first.source_references[0]
    return [res], len(graph.nodes)


@register("PERF-DB-001", TOOL_ID, TOOL_NAME, "performance", "Shared datastore used by many services")
def shared_database(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    graph = DependencyGraph(ctx.model)
    out: list[ToolResult] = []
    for comp_id, dependents in graph.single_dependency_bottlenecks(2):
        comp = ctx.model.component(comp_id)
        if comp is None or comp.type != "database":
            continue
        res = make_result(
            tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id="PERF-DB-001", domain="performance", res=None,
            title="Single database shared by multiple services",
            message=f"{comp.name} is accessed by {len(dependents)} components ({', '.join(dependents[:5])}); contention and coupled scaling are likely under load.",
            observed=f"{len(dependents)} dependents", expected="Per-service data ownership, read replicas or caching for hot paths",
            level=EvidenceLevel.INFERRED, severity=Severity.MEDIUM, components=[comp_id] + dependents, qualifier=comp_id,
        )
        if comp.source_references:
            res.evidence_reference = comp.source_references[0]
        out.append(res)
    return out, len(graph.nodes)


@register("PERF-CACHE-001", TOOL_ID, TOOL_NAME, "performance", "No caching layer in the architecture model")
def no_cache(ctx: ToolContext) -> tuple[list[ToolResult], int]:
    model = ctx.model
    if model.components_of_type("cache", "cdn"):
        return [], 0
    graph = DependencyGraph(model)
    dbs = [(c, graph.reverse_dependencies(c)) for c in sorted(graph.nodes) if (model.component(c) and model.component(c).type == "database")]
    hot = [(c, deps) for c, deps in dbs if len(deps) >= 2]
    if not hot:
        return [], len(graph.nodes)
    comp_id, deps = hot[0]
    comp = model.component(comp_id)
    res = make_result(
        tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id="PERF-CACHE-001", domain="performance", res=None,
        title="No cache or CDN is modelled in front of shared data stores",
        message=f"The extracted model contains no cache/CDN component while {comp.name if comp else comp_id} serves {len(deps)} consumers. "
                "A cache may exist but is not described in the supplied material.",
        observed="0 cache/CDN components", expected="Documented caching strategy for read-heavy paths",
        level=EvidenceLevel.NEEDS_VALIDATION, severity=Severity.LOW, components=[comp_id] + deps, qualifier="nocache",
    )
    if comp and comp.source_references:
        res.evidence_reference = comp.source_references[0]
    return [res], len(graph.nodes)
