"""LangGraph state definitions.

State holds only JSON-serialisable values (plain dicts/lists/strings).  Documents and large
payloads are referenced by artifact ID; clients, sessions and file handles never enter state.
List-valued fields written by parallel branches use the ``operator.add`` reducer so
concurrent specialists append rather than overwrite.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ReviewState(TypedDict, total=False):
    review_id: str
    thread_id: str
    project_id: str
    mode: str  # demo | live
    provider: str
    scenario_id: str | None

    architecture_model: dict[str, Any]
    source_artifacts: list[dict[str, Any]]
    architecture_revision: int
    contextual_evidence: dict[str, list[dict[str, Any]]]

    enabled_agents: list[str]
    rerun_agents: list[str]
    agent_results: Annotated[list[dict[str, Any]], operator.add]
    candidate_findings: Annotated[list[dict[str, Any]], operator.add]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    moderated_findings: list[dict[str, Any]]

    human_decisions: Annotated[list[dict[str, Any]], operator.add]
    rebuttals: Annotated[list[dict[str, Any]], operator.add]
    change_impact: dict[str, Any]
    revision_id: str | None
    change_notes: str

    current_round: int
    maximum_rounds: int
    workflow_status: str
    next_step: str
    last_action: str
    execution_events: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    moderation: dict[str, Any]
    final_report: dict[str, Any]
    reconstructed: bool


class SpecialistState(TypedDict, total=False):
    """State of the reusable specialist subgraph (one invocation per critic per round)."""

    review_id: str
    agent_id: str
    execution_id: str
    round: int
    revision: int
    mode: str
    provider: str
    scenario_id: str | None
    architecture_model: dict[str, Any]
    source_artifacts: list[dict[str, Any]]
    contextual_evidence: list[dict[str, Any]]
    rule_ids: list[str]
    tool_results: list[dict[str, Any]]
    tool_summaries: list[dict[str, Any]]
    scope: dict[str, Any]
    catalog: list[dict[str, Any]]
    draft: dict[str, Any]
    usage: dict[str, Any]
    findings: list[dict[str, Any]]
    warnings: list[str]
    information_gaps: list[str]
    coverage_summary: str
    prior_findings: list[dict[str, Any]]
    change_notes: str
