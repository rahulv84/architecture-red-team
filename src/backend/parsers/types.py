"""Shared parser output types and limits."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ParserLimits(BaseModel):
    max_bytes: int = 25 * 1024 * 1024
    max_pdf_pages: int = 200
    max_zip_uncompressed: int = 150 * 1024 * 1024
    max_zip_entries: int = 5000
    max_text_chars: int = 2_000_000
    max_yaml_aliases: int = 100
    max_nodes: int = 5000


class DocSection(BaseModel):
    heading: str | None = None
    text: str
    page: int | None = None
    slide: int | None = None
    line_start: int | None = None
    line_end: int | None = None


class DiagramNode(BaseModel):
    node_id: str
    label: str
    shape: str | None = None
    group: str | None = None
    line: int | None = None
    extracted_by: str = "structure"  # structure | vision


class DiagramEdge(BaseModel):
    source: str
    target: str
    label: str = ""
    line: int | None = None
    extracted_by: str = "structure"


class IacResource(BaseModel):
    """A declared infrastructure resource from Terraform / CloudFormation / Kubernetes."""

    framework: str  # terraform | cloudformation | kubernetes
    resource_type: str
    name: str
    address: str
    properties: dict[str, Any] = Field(default_factory=dict)
    line: int | None = None
    artifact_id: str | None = None
    filename: str | None = None
    namespace: str | None = None


class ParsedDocument(BaseModel):
    artifact_id: str = ""
    filename: str = ""
    kind: str = "text"
    text: str = ""
    sections: list[DocSection] = Field(default_factory=list)
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    resources: list[IacResource] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    page_count: int | None = None
    requires_vision: bool = False
    image_paths: list[str] = Field(default_factory=list)
