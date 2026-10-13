"""Deterministic tools: importing this package registers every built-in rule."""
from . import (  # noqa: F401  (imports register rules)
    configuration_checks,
    cost_checks,
    dependency_analyzer,
    resilience_checks,
    security_checks,
)
from .base import REGISTRY, Rule, ToolContext, dedupe_results, run_rules

# Which rules each specialist executes.  Rules may be shared across agents; results have
# deterministic IDs so overlapping findings can be deduplicated by the moderator.
AGENT_RULES: dict[str, list[str]] = {
    "security": ["SEC-NET-001", "SEC-NET-002", "SEC-STO-001", "SEC-ENC-001", "SEC-ENC-002", "SEC-SEC-001",
                 "SEC-IAM-001", "SEC-K8S-001", "SEC-K8S-002", "SEC-API-001"],
    "resilience": ["RES-HA-001", "RES-HA-002", "RES-HA-003", "RES-HA-004", "RES-BAK-001", "RES-NAT-001",
                   "RES-MSG-001", "RES-DEL-001", "RES-SPOF-001", "CFG-NFR-001", "CFG-REF-001"],
    "finops": ["COST-SIZE-001", "COST-LOG-001", "COST-S3-001", "COST-NAT-001", "COST-SCALE-001", "COST-XFER-001", "COST-TAG-001"],
    "compliance": ["SEC-ENC-001", "SEC-NET-002", "SEC-STO-001", "SEC-SEC-001", "COMP-LOG-001", "COMP-NET-001",
                   "COMP-DATA-001", "COST-LOG-001", "SEC-IAM-001"],
    "performance": ["PERF-CHAIN-001", "PERF-DB-001", "PERF-CACHE-001", "RES-HA-002", "COST-SCALE-001"],
    "operability": ["RES-HA-003", "RES-MSG-001", "CFG-K8S-001", "CFG-K8S-002", "CFG-REF-001", "OPS-ALARM-001",
                    "OPS-LOG-001", "COST-LOG-001"],
}

# Agents that also consume Checkov output (security-relevant scanner).
CHECKOV_AGENTS = ("security", "compliance")

__all__ = ["AGENT_RULES", "CHECKOV_AGENTS", "REGISTRY", "Rule", "ToolContext", "dedupe_results", "run_rules"]
