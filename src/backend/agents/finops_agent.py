"""FinOps specialist: structural cost risk without invented prices."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ToolResult
from .base_agent import BaseAgent, ScopedView

_DRIVERS = {
    "nat_gateway": "per-hour charge + per-GB data processing",
    "database": "instance class, storage size/IOPS, multi-AZ replicas, backup storage",
    "compute": "instance hours at provisioned capacity, scaling headroom",
    "container_orchestrator": "control plane + node capacity",
    "object_storage": "storage volume by tier, request counts, lifecycle",
    "log_group": "ingestion volume and retention",
    "cdn": "egress by region",
    "queue": "request count, retention",
    "cache": "node type and replica count",
    "vpn": "connection hours + data transfer",
}


class FinOpsAgent(BaseAgent):
    agent_id = "finops"
    display_name = "FinOps"
    relevant_types = frozenset({
        "compute", "service", "function", "container_orchestrator", "database", "cache", "object_storage", "file_storage", "queue",
        "topic", "stream", "nat_gateway", "vpn", "cdn", "log_group", "monitoring", "load_balancer", "batch_job", "logging",
    })
    query_keywords = "cost optimization autoscaling idle capacity lifecycle retention data transfer egress nat right-sizing tags budget"

    def domain_brief(self, model: ArchitectureModel, view: ScopedView, tool_results: list[ToolResult]) -> str:
        lines = ["Cost-driver inventory (qualitative; no prices supplied - do NOT state monetary amounts):"]
        for c in view.components:
            if c.type in _DRIVERS:
                size = next((str(c.configuration[k]) for k in ("instance_class", "instance_type", "node_type") if k in c.configuration), "size not stated")
                lines.append(f"- {c.name} ({c.type}, {size}): drivers = {_DRIVERS[c.type]}")
        flows = [f for f in model.data_flows if f.crosses_trust_boundary]
        lines.append(f"Boundary-crossing data flows (egress/transfer candidates): {len(flows)}")
        lines.append("Regions: " + (", ".join(model.deployment_regions) or "not stated"))
        lines.append("Expected workload (for sizing): " + (" | ".join(model.expected_workload[:3]) or "NOT STATED"))
        lines.append("For each concern, list the data needed to estimate real spend (utilisation, volumes, region, commitment).")
        return "\n".join(lines)
