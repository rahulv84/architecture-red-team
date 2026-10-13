"""Performance specialist: latency chains, saturation points and caching."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ToolResult
from .base_agent import BaseAgent, ScopedView


class PerformanceAgent(BaseAgent):
    agent_id = "performance"
    display_name = "Performance"
    relevant_types = frozenset({
        "load_balancer", "api_gateway", "gateway", "compute", "service", "function", "container_orchestrator", "database", "cache",
        "queue", "topic", "stream", "cdn", "external_system", "batch_job", "nat_gateway", "object_storage",
    })
    query_keywords = "latency throughput scalability bottleneck caching load balancing concurrency saturation synchronous backpressure autoscaling"

    def domain_brief(self, model: ArchitectureModel, view: ScopedView, tool_results: list[ToolResult]) -> str:
        graph = self.dep_graph(model)
        lines: list[str] = []
        targets = [n for n in model.nfrs if n.category in ("latency", "throughput")]
        lines.append("Stated performance targets: " + ("; ".join(f"{n.category}={n.value}" for n in targets) or "NONE STATED"))
        lines.append("Workload statements: " + (" | ".join(model.expected_workload[:3]) or "none"))
        chain = graph.longest_sync_chain()
        lines.append("Longest synchronous chain (" + str(len(chain)) + " hops): " + (" -> ".join(self.comp_label(model, c) for c in chain) or "n/a"))
        fan_in = sorted(graph.single_dependency_bottlenecks(2), key=lambda x: -len(x[1]))[:5]
        for cid, deps in fan_in:
            lines.append(f"Fan-in hotspot: {self.comp_label(model, cid)} is used by {len(deps)} components")
        lines.append("Caching components modelled: " + (", ".join(c.name for c in model.components_of_type("cache", "cdn")) or "none"))
        lines.append("Autoscaling hints: " + ", ".join(
            f"{c.name}: min={c.configuration.get('min_size', c.configuration.get('replicas', '?'))}/max={c.configuration.get('max_size', '?')}"
            for c in view.components if c.type in ("compute", "service", "container_orchestrator"))[:400])
        lines.append("Missing workload data must be listed in information_gaps; never fabricate measurements.")
        return "\n".join(lines)
