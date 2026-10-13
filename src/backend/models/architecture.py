"""Structured intermediate architecture model produced by the extraction stage.

Agents never read raw documents wholesale; they receive scoped slices of this model plus
source references that point back to the original artifacts.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .domain import Provenance, SourceReference

COMPONENT_TYPES = (
    "load_balancer", "api_gateway", "service", "compute", "container_orchestrator", "function",
    "database", "cache", "object_storage", "file_storage", "queue", "topic", "stream", "batch_job",
    "network", "subnet", "firewall", "security_group", "nat_gateway", "vpn", "dns", "cdn",
    "identity", "iam_policy", "secret_store", "kms_key", "monitoring", "log_group", "logging",
    "external_system", "user", "on_premises", "gateway", "other",
)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unnamed"


class ArchitectureComponent(BaseModel):
    component_id: str
    name: str
    type: str = "other"
    provider: str = "unknown"
    description: str = ""
    configuration: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict, description="property -> EXPLICIT|INFERRED|UNKNOWN")
    dependencies: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    confidence: float = 0.8
    zone: str | None = None
    tags: list[str] = Field(default_factory=list)

    def prop(self, key: str, default: Any = None) -> Any:
        return self.configuration.get(key, default)

    def prop_provenance(self, key: str) -> str:
        return self.provenance.get(key, Provenance.UNKNOWN.value if key not in self.configuration else Provenance.EXPLICIT.value)


class ArchitectureRelationship(BaseModel):
    relationship_id: str
    source: str
    target: str
    relationship_type: str = "depends_on"  # depends_on | calls | reads | writes | publishes | subscribes | routes_to | hosts
    label: str = ""
    protocol: str | None = None
    synchronous: bool | None = None
    provenance: str = Provenance.EXPLICIT.value
    evidence: list[SourceReference] = Field(default_factory=list)


class DataFlow(BaseModel):
    flow_id: str
    name: str
    source: str
    destination: str
    data_classification: str | None = None
    protocol: str | None = None
    encrypted_in_transit: bool | None = None
    crosses_trust_boundary: bool | None = None
    source_references: list[SourceReference] = Field(default_factory=list)


class ArchitectureConstraint(BaseModel):
    constraint_id: str
    statement: str
    category: str = "general"
    source_references: list[SourceReference] = Field(default_factory=list)


class NonFunctionalRequirement(BaseModel):
    nfr_id: str
    category: str  # availability | rto | rpo | latency | throughput | security | compliance | cost | retention | durability | other
    statement: str
    value: str | None = None
    provenance: str = Provenance.EXPLICIT.value
    source_references: list[SourceReference] = Field(default_factory=list)


class ArchitectureAssumption(BaseModel):
    assumption_id: str
    statement: str
    reason: str = ""
    confidence: float = 0.5


class ExtractionNote(BaseModel):
    artifact_id: str | None = None
    level: str = "INFO"  # INFO | WARNING | LIMITATION
    message: str


class ArchitectureModel(BaseModel):
    title: str = "Untitled architecture"
    purpose: str = ""
    environment: str = "Unknown"
    revision: int = 1
    components: list[ArchitectureComponent] = Field(default_factory=list)
    relationships: list[ArchitectureRelationship] = Field(default_factory=list)
    data_flows: list[DataFlow] = Field(default_factory=list)
    constraints: list[ArchitectureConstraint] = Field(default_factory=list)
    nfrs: list[NonFunctionalRequirement] = Field(default_factory=list)
    assumptions: list[ArchitectureAssumption] = Field(default_factory=list)
    security_controls: list[str] = Field(default_factory=list)
    operational_tooling: list[str] = Field(default_factory=list)
    deployment_regions: list[str] = Field(default_factory=list)
    availability_dr: list[str] = Field(default_factory=list)
    expected_workload: list[str] = Field(default_factory=list)
    auth_mechanisms: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    notes: list[ExtractionNote] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)

    # ---- helpers -----------------------------------------------------------
    def component(self, component_id: str) -> ArchitectureComponent | None:
        for comp in self.components:
            if comp.component_id == component_id:
                return comp
        return None

    def find(self, ref: str) -> ArchitectureComponent | None:
        """Resolve a component by id or (case-insensitive) name."""
        found = self.component(ref)
        if found:
            return found
        lowered = ref.lower()
        for comp in self.components:
            if comp.name.lower() == lowered or slugify(comp.name) == slugify(ref):
                return comp
        return None

    def components_of_type(self, *types: str) -> list[ArchitectureComponent]:
        return [c for c in self.components if c.type in types]

    def component_ids(self) -> set[str]:
        return {c.component_id for c in self.components}

    def summary_dict(self) -> dict[str, Any]:
        return {
            "components": len(self.components),
            "relationships": len(self.relationships),
            "data_flows": len(self.data_flows),
            "nfrs": len(self.nfrs),
            "unresolved": len(self.unresolved),
        }
