"""Compliance specialist: architecture-level control mapping (never a legal determination)."""
from __future__ import annotations

from ..models.architecture import ArchitectureModel
from ..models.domain import ToolResult
from .base_agent import BaseAgent, ScopedView


class ComplianceAgent(BaseAgent):
    agent_id = "compliance"
    display_name = "Compliance"
    relevant_types = frozenset({
        "database", "cache", "object_storage", "file_storage", "queue", "topic", "stream", "log_group", "logging", "monitoring",
        "identity", "iam_policy", "secret_store", "kms_key", "external_system", "on_premises", "vpn", "network", "load_balancer",
        "api_gateway", "gateway", "user", "batch_job",
    })
    query_keywords = "gdpr iso 27001 pci dss personal data residency retention deletion audit logging encryption access control classification"

    def domain_brief(self, model: ArchitectureModel, view: ScopedView, tool_results: list[ToolResult]) -> str:
        lines: list[str] = []
        obligations = [n for n in model.nfrs if n.category in ("compliance", "retention")]
        lines.append("Stated compliance/retention obligations: " + ("; ".join(n.statement[:110] for n in obligations) or "NONE STATED"))
        personal = [c for c in model.components if any(
            k in (c.name + " " + str(c.configuration.get("tags", ""))).lower() for k in ("pii", "personal", "customer", "gdpr", "cardholder"))]
        lines.append("Components that appear to hold personal data (by name/tags): " + (", ".join(c.name for c in personal) or "none identified"))
        lines.append("Regions/data residency: " + (", ".join(model.deployment_regions) or "not stated"))
        rule_ids = {t.rule_id for t in tool_results}
        controls = self.context.controls_for_rules(rule_ids)
        if controls:
            lines.append("Control themes related to tool observations (architecture-level, not determinations):")
            seen: set[str] = set()
            for c in controls:
                key = f"{c['framework']} {c['control_id']}"
                if key not in seen:
                    seen.add(key)
                    lines.append(f"- {key}: {c['topic']}")
        lines.append("Audit logging evidence: " + ("present" if model.components_of_type("logging", "log_group") else "not shown"))
        lines.append("Use wording such as 'possible control gap relative to <control>'. Never say the organisation violates or complies.")
        return "\n".join(lines)
