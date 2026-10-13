"""Pure builders for Graphviz DOT strings (no Streamlit calls, no I/O).

``workflow_dot`` draws the LangGraph review workflow; ``dependency_dot`` draws the extracted
architecture dependency graph.  The output is rendered client-side by ``st.graphviz_chart`` so no
system Graphviz installation is needed.
"""
from __future__ import annotations

from collections import deque
from typing import Any

TYPE_GROUPS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # group -> (fill colour, border colour, component types)
    "Compute and services": ("#dbeafe", "#1d4ed8", ("service", "compute", "container_orchestrator", "function", "batch_job")),
    "Data stores": ("#dcfce7", "#15803d", ("database", "cache", "object_storage", "file_storage")),
    "Messaging": ("#ede9fe", "#6d28d9", ("queue", "topic", "stream")),
    "Network and edge": ("#fef3c7", "#a16207", ("load_balancer", "api_gateway", "network", "subnet", "firewall", "security_group",
                                                  "nat_gateway", "vpn", "dns", "cdn", "gateway")),
    "Identity and secrets": ("#fee2e2", "#b91c1c", ("identity", "iam_policy", "secret_store", "kms_key")),
    "Observability": ("#ccfbf1", "#0f766e", ("monitoring", "log_group", "logging")),
    "External": ("#e2e8f0", "#475569", ("external_system", "user", "on_premises")),
}
_DEFAULT_STYLE = ("#f8fafc", "#94a3b8")
_TYPE_STYLE = {t: (fill, border) for fill, border, types in TYPE_GROUPS.values() for t in types}
HIDDEN_BY_DEFAULT = ("network_membership", "uses_identity")

_AGENT_NODES = {"security_agent", "resilience_agent", "finops_agent", "compliance_agent", "performance_agent", "operability_agent"}


def quote(value: str) -> str:
    """Quote ``value`` as a DOT string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    return f'"{escaped}"'


def component_style(component_type: str) -> tuple[str, str]:
    """(fill, border) colours for a component type."""
    return _TYPE_STYLE.get(component_type, _DEFAULT_STYLE)


def _pretty(node: str) -> str:
    return {"__start__": "START", "__end__": "END"}.get(node, node.replace("_agent", " agent").replace("_", " "))


def workflow_dot(topology: dict[str, Any]) -> str:
    """DOT for the LangGraph workflow; nodes in ``next_nodes`` are highlighted as the next to execute."""
    highlighted = set(topology.get("next_nodes") or [])
    lines = ["digraph workflow {", '  rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.45;',
             '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, fillcolor="#f1f5f9", color="#94a3b8"];',
             '  edge [color="#64748b", arrowsize=0.7, fontname="Helvetica", fontsize=9];']
    for node in topology.get("nodes", []):
        attrs = [f"label={quote(_pretty(node))}"]
        if node in ("__start__", "__end__"):
            attrs += ["shape=circle", 'fillcolor="#0f172a"', 'fontcolor="#ffffff"', "width=0.5"]
        elif node in _AGENT_NODES:
            attrs += ['fillcolor="#dbeafe"', 'color="#1d4ed8"']
        elif node == "human_review":
            attrs += ["shape=diamond", 'fillcolor="#fef3c7"', 'color="#a16207"']
        elif node == "moderator":
            attrs += ['fillcolor="#ede9fe"', 'color="#6d28d9"']
        if node in highlighted:
            attrs += ['fillcolor="#fde68a"', 'color="#b45309"', "penwidth=3"]
        lines.append(f"  {quote(node)} [{', '.join(attrs)}];")
    for edge in topology.get("edges", []):
        style = ' [style=dashed]' if edge.get("conditional") else ""
        lines.append(f"  {quote(edge['source'])} -> {quote(edge['target'])}{style};")
    lines.append("}")
    return "\n".join(lines)


def _neighbourhood(edges: list[tuple[str, str]], focus: str, hops: int) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    seen = {focus}
    queue: deque[tuple[str, int]] = deque([(focus, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= hops:
            continue
        for nxt in adjacency.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, depth + 1))
    return seen


def dependency_dot(
    model: dict[str, Any], hidden_edge_types: tuple[str, ...] = HIDDEN_BY_DEFAULT, focus: str | None = None, hops: int = 1,
) -> tuple[str, dict[str, int]]:
    """DOT for the architecture dependency graph plus ``{"nodes", "edges", "hidden_edges", "isolated"}`` counts.

    Edges whose relationship type is in ``hidden_edge_types`` are dropped; components left without any
    visible edge are omitted (and counted as ``isolated``) so the graph stays readable.
    """
    components = {c["component_id"]: c for c in model.get("components", [])}
    visible: list[dict[str, Any]] = []
    hidden = 0
    for rel in model.get("relationships", []):
        if rel.get("relationship_type") in hidden_edge_types:
            hidden += 1
        else:
            visible.append(rel)
    pairs = [(r["source"], r["target"]) for r in visible]
    keep: set[str] = {n for pair in pairs for n in pair}
    if focus and focus in components:
        keep = _neighbourhood(pairs, focus, hops) | {focus}
        visible = [r for r in visible if r["source"] in keep and r["target"] in keep]
    isolated = len([cid for cid in components if cid not in keep])
    lines = ["digraph architecture {", '  rankdir=LR; bgcolor="transparent"; nodesep=0.3; ranksep=0.6;',
             '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
             '  edge [color="#64748b", arrowsize=0.7, fontname="Helvetica", fontsize=9];']
    for cid in sorted(keep):
        comp = components.get(cid)
        if comp is None:
            lines.append(f'  {quote(cid)} [label={quote(cid)}, fillcolor="#f8fafc", color="#94a3b8", style="rounded,filled,dashed"];')
            continue
        fill, border = component_style(comp.get("type", "other"))
        label = f"{comp.get('name') or cid}\n({comp.get('type', 'other')})"
        pen = ", penwidth=3" if cid == focus else ""
        lines.append(f'  {quote(cid)} [label={quote(label)}, fillcolor="{fill}", color="{border}"{pen}];')
    for rel in visible:
        text = rel.get("label") or rel.get("relationship_type", "")
        extra = ", style=dashed" if rel.get("provenance") == "INFERRED" else ""
        lines.append(f"  {quote(rel['source'])} -> {quote(rel['target'])} [label={quote(str(text))}{extra}];")
    lines.append("}")
    return "\n".join(lines), {"nodes": len(keep), "edges": len(visible), "hidden_edges": hidden, "isolated": isolated}


def diagram_dot(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    """DOT for a parsed diagram document (its nodes and connections as extracted by the parser)."""
    lines = ["digraph diagram {", '  rankdir=LR; bgcolor="transparent";',
             '  node [shape=box, style="rounded,filled", fillcolor="#f1f5f9", color="#94a3b8", fontname="Helvetica", fontsize=10];',
             '  edge [color="#64748b", arrowsize=0.7, fontname="Helvetica", fontsize=9];']
    known = {n["node_id"] for n in nodes}
    for node in nodes:
        lines.append(f"  {quote(node['node_id'])} [label={quote(node.get('label') or node['node_id'])}];")
    for edge in edges:
        for end in (edge["source"], edge["target"]):
            if end not in known:
                known.add(end)
                lines.append(f"  {quote(end)} [label={quote(end)}];")
        label = f" [label={quote(edge['label'])}]" if edge.get("label") else ""
        lines.append(f"  {quote(edge['source'])} -> {quote(edge['target'])}{label};")
    lines.append("}")
    return "\n".join(lines)
