"""Graph construction: the main review StateGraph and the reusable specialist subgraph."""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..models.domain import ALL_AGENT_IDS
from . import nodes
from .routing import AGENT_NODES, entry_router, fan_out, route_after_human
from .state import ReviewState, SpecialistState


def build_specialist_subgraph() -> Any:
    """The process every critic follows: rules -> tools -> scope -> retrieve -> analyze -> validate."""
    g = StateGraph(SpecialistState)
    g.add_node("select_rules", nodes.sp_select_rules)
    g.add_node("run_tools", nodes.sp_run_tools)
    g.add_node("scope_context", nodes.sp_scope)
    g.add_node("retrieve_evidence", nodes.sp_retrieve)
    g.add_node("llm_analysis", nodes.sp_analyze)
    g.add_node("validate_findings", nodes.sp_validate)
    g.add_edge(START, "select_rules")
    g.add_edge("select_rules", "run_tools")
    g.add_edge("run_tools", "scope_context")
    g.add_edge("scope_context", "retrieve_evidence")
    g.add_edge("retrieve_evidence", "llm_analysis")
    g.add_edge("llm_analysis", "validate_findings")
    g.add_edge("validate_findings", END)
    return g.compile()


def build_review_graph(checkpointer: Any) -> Any:
    """Compile the Architecture Red Team workflow (see docs/agent-workflows.md)."""
    specialist = build_specialist_subgraph()
    g = StateGraph(ReviewState)
    g.add_node("validate_input", nodes.validate_input)
    g.add_node("parse_documents", nodes.parse_documents)
    g.add_node("extract_architecture", nodes.extract_architecture)
    g.add_node("retrieve_context", nodes.retrieve_context)
    g.add_node("select_critics", nodes.select_critics)
    for agent_id in ALL_AGENT_IDS:
        g.add_node(AGENT_NODES[agent_id], nodes.make_agent_node(agent_id, specialist))
    g.add_node("moderator", nodes.moderator)
    g.add_node("human_review", nodes.human_review)
    g.add_node("rebuttal", nodes.rebuttal)
    g.add_node("apply_revision", nodes.apply_revision)
    g.add_node("finalize", nodes.finalize)

    g.add_conditional_edges(START, entry_router, {"validate_input": "validate_input", "human_review": "human_review"})
    g.add_edge("validate_input", "parse_documents")
    g.add_edge("parse_documents", "extract_architecture")
    g.add_edge("extract_architecture", "retrieve_context")
    g.add_edge("retrieve_context", "select_critics")
    g.add_conditional_edges("select_critics", fan_out, [*AGENT_NODES.values(), "moderator"])
    for node_name in AGENT_NODES.values():
        g.add_edge(node_name, "moderator")
    g.add_edge("moderator", "human_review")
    g.add_conditional_edges(
        "human_review", route_after_human,
        {"rebuttal": "rebuttal", "apply_revision": "apply_revision", "finalize": "finalize", "human_review": "human_review"},
    )
    g.add_edge("rebuttal", "human_review")
    g.add_edge("apply_revision", "retrieve_context")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def graph_topology(compiled: Any) -> dict[str, Any]:
    """Nodes and edges for documentation / the UI (derived from the compiled graph)."""
    drawable = compiled.get_graph()
    return {
        "nodes": sorted(drawable.nodes),
        "edges": [{"source": e.source, "target": e.target, "conditional": bool(e.conditional)} for e in drawable.edges],
    }
