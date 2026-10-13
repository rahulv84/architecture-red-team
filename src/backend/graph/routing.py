"""Conditional routing functions for the review graph."""
from __future__ import annotations

from langgraph.types import Send

from ..models.domain import ALL_AGENT_IDS
from .state import ReviewState

AGENT_NODES = {a: f"{a}_agent" for a in ALL_AGENT_IDS}


def entry_router(state: ReviewState) -> str:
    """Normal runs start at validation; reconstructed runs re-enter the human boundary directly."""
    return "human_review" if state.get("reconstructed") else "validate_input"


def fan_out(state: ReviewState) -> list[Send] | str:
    """Parallel fan-out: one ``Send`` per selected critic (LangGraph runs them concurrently)."""
    agents = [a for a in ALL_AGENT_IDS if a in (state.get("rerun_agents") or state.get("enabled_agents") or [])]
    if not agents:
        return "moderator"
    return [Send(AGENT_NODES[a], {**state, "agent_id": a}) for a in agents]


def route_after_human(state: ReviewState) -> str:
    step = state.get("next_step", "human_review")
    return step if step in ("rebuttal", "apply_revision", "finalize", "human_review") else "human_review"
