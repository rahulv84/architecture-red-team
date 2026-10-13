"""Operability specialist: observability, deployment safety, runbooks and ownership."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ToolResult
from .base_agent import BaseAgent, ScopedView


class OperabilityAgent(BaseAgent):
    agent_id = "operability"
    display_name = "Operability"
    relevant_types = frozenset({
        "compute", "service", "function", "container_orchestrator", "database", "cache", "queue", "topic", "stream", "batch_job",
        "load_balancer", "api_gateway", "gateway", "monitoring", "log_group", "logging", "object_storage", "external_system",
        "on_premises",
    })
    query_keywords = "monitoring logging tracing alerting slo runbook deployment rollback probes health incident on-call operations dead letter"

    def domain_brief(self, model: ArchitectureModel, view: ScopedView, tool_results: list[ToolResult]) -> str:
        lines: list[str] = []
        lines.append("Operational tooling mentioned: " + (", ".join(model.operational_tooling) or "NONE"))
        lines.append("Monitoring/logging components modelled: " + (", ".join(c.name for c in model.components_of_type("monitoring", "log_group", "logging")) or "none"))
        workloads = [c for c in model.components if "readiness_probe" in c.configuration]
        if workloads:
            lines.append("Probe coverage (kubernetes workloads):")
            for c in workloads[:12]:
                lines.append(f"- {c.name}: readiness={c.configuration.get('readiness_probe')}, liveness={c.configuration.get('liveness_probe')}, "
                             f"limits={c.configuration.get('resource_limits')}")
        async_paths = [c for c in model.components if c.type in ("queue", "topic", "stream")]
        lines.append("Asynchronous components needing failure handling: " + (", ".join(c.name for c in async_paths) or "none"))
        incidents = [a for a in model.notes if "incident" in a.message.lower()]
        if incidents:
            lines.append("Incident context was supplied; consider recurrence risk.")
        lines.append("SLO/SLI statements: " + ("; ".join(n.statement[:90] for n in model.nfrs if n.category in ("availability", "latency")) or "none"))
        lines.append("Differentiate 'no documented evidence of a capability' from 'confirmed absent'.")
        return "\n".join(lines)
