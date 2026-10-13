"""Resilience specialist: failure domains, redundancy, recovery objectives and dependency failure."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ToolResult
from .base_agent import BaseAgent, ScopedView


class ResilienceAgent(BaseAgent):
    agent_id = "resilience"
    display_name = "Resilience"
    relevant_types = frozenset({
        "load_balancer", "api_gateway", "gateway", "compute", "service", "function", "container_orchestrator", "database", "cache",
        "object_storage", "file_storage", "queue", "topic", "stream", "nat_gateway", "vpn", "external_system", "on_premises",
        "batch_job", "dns", "cdn", "subnet",
    })
    query_keywords = "availability redundancy failover multi-az backup restore recovery rto rpo single point of failure replica dead letter timeout"

    def domain_brief(self, model: ArchitectureModel, view: ScopedView, tool_results: list[ToolResult]) -> str:
        graph = self.dep_graph(model)
        lines: list[str] = []
        targets = [n for n in model.nfrs if n.category in ("availability", "rto", "rpo")]
        lines.append("Stated recovery/availability objectives: " + ("; ".join(f"{n.category}={n.value}" for n in targets) or "NONE STATED"))
        lines.append("Declared DR/backup statements: " + (" | ".join(s[:120] for s in model.availability_dr[:3]) or "none found"))
        lines.append("Component failure-impact table (deterministic):")
        rows = []
        for c in view.components:
            if c.type in ("subnet",):
                continue
            impact = graph.failure_propagation(c.component_id)
            zone_info = c.configuration.get("multi_az", c.configuration.get("replicas", "unknown"))
            rows.append((len(impact), f"- {c.name} ({c.type}): redundancy-hint={zone_info}; if it fails, {len(impact)} component(s) are affected"))
        lines += [r for _, r in sorted(rows, key=lambda x: -x[0])[:14]]
        chain = graph.longest_sync_chain()
        if len(chain) >= 3:
            lines.append("Longest synchronous path: " + " -> ".join(self.comp_label(model, c) for c in chain))
        ext = [c for c in model.components if c.type == "external_system"]
        if ext:
            lines.append("External dependencies: " + ", ".join(c.name for c in ext))
        lines.append("Regions/zones stated: " + (", ".join(model.deployment_regions) or "none"))
        lines.append("Differentiate 'verified single-zone' (explicit setting) from 'redundancy not described'.")
        return "\n".join(lines)
