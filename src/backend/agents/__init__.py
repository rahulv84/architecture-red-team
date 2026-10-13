"""Specialist agents and the moderator."""
from .base_agent import BaseAgent
from .compliance_agent import ComplianceAgent
from .finops_agent import FinOpsAgent
from .moderator_agent import ModeratorAgent
from .operability_agent import OperabilityAgent
from .performance_agent import PerformanceAgent
from .resilience_agent import ResilienceAgent
from .security_agent import SecurityAgent

AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "security": SecurityAgent,
    "resilience": ResilienceAgent,
    "finops": FinOpsAgent,
    "compliance": ComplianceAgent,
    "performance": PerformanceAgent,
    "operability": OperabilityAgent,
}

__all__ = ["AGENT_CLASSES", "BaseAgent", "ModeratorAgent"]
