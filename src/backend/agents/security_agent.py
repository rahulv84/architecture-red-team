"""Security specialist: STRIDE-oriented review of exposure, identity, secrets and encryption."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ToolResult
from .base_agent import BaseAgent, ScopedView

_STRIDE = {
    "load_balancer": "Spoofing/DoS at the edge; verify TLS, WAF and listener policy",
    "api_gateway": "Spoofing and elevation of privilege; verify authentication, throttling, input validation",
    "database": "Information disclosure and tampering; verify network reachability, encryption, credentials",
    "cache": "Information disclosure; verify network reachability, auth and transit encryption",
    "object_storage": "Information disclosure and tampering; verify public access and bucket policy",
    "queue": "Tampering and repudiation; verify producer/consumer authorisation and encryption",
    "compute": "Elevation of privilege; verify role scope, SSH/admin exposure, patching path",
    "service": "Elevation of privilege and spoofing; verify service-to-service authentication",
    "identity": "Elevation of privilege; verify least-privilege policies and trust relationships",
    "secret_store": "Information disclosure; verify access policy and rotation",
}


class SecurityAgent(BaseAgent):
    agent_id = "security"
    display_name = "Security"
    relevant_types = frozenset({
        "load_balancer", "api_gateway", "gateway", "compute", "service", "function", "container_orchestrator", "database", "cache",
        "object_storage", "file_storage", "queue", "topic", "stream", "security_group", "firewall", "identity", "iam_policy",
        "secret_store", "kms_key", "vpn", "network", "nat_gateway", "external_system", "user", "on_premises", "cdn", "logging",
    })
    query_keywords = "security authentication authorization encryption network exposure public access iam secrets least privilege tls"

    def domain_brief(self, model: ArchitectureModel, view: ScopedView, tool_results: list[ToolResult]) -> str:
        lines: list[str] = []
        entry = [c for c in model.components if c.type in ("load_balancer", "api_gateway", "gateway", "cdn", "vpn")]
        lines.append("Entry points: " + (", ".join(f"{c.name} ({c.type})" for c in entry) or "none identified"))
        boundary = [f for f in model.data_flows if f.crosses_trust_boundary]
        if boundary:
            lines.append("Flows crossing trust boundaries:")
            for f in boundary[:8]:
                enc = {True: "encrypted", False: "NOT encrypted", None: "encryption unknown"}[f.encrypted_in_transit]
                lines.append(f"- {self.comp_label(model, f.source)} -> {self.comp_label(model, f.destination)} [{f.protocol or 'protocol unknown'}, {enc}]")
        lines.append("Declared security controls: " + (", ".join(model.security_controls) or "none stated"))
        lines.append("Authentication mechanisms stated: " + (", ".join(model.auth_mechanisms) or "none stated"))
        lines.append("STRIDE prompts for in-scope component types:")
        for ctype in sorted({c.type for c in view.components}):
            if ctype in _STRIDE:
                lines.append(f"- {ctype}: {_STRIDE[ctype]}")
        graph = self.dep_graph(model)
        crown = [c for c in model.components if c.type in ("database", "object_storage", "secret_store")]
        for c in crown[:4]:
            lines.append(f"Blast radius if {c.name} is compromised: {len(graph.failure_propagation(c.component_id))} dependent component(s)")
        n_tools = len([t for t in tool_results if t.outcome == "VIOLATION"])
        lines.append(f"Deterministic tools reported {n_tools} violation(s); interpret them, do not re-derive them.")
        return "\n".join(lines)
