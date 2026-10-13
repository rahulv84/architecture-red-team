"""Project and artifact persistence queries."""
from __future__ import annotations

from ..core.exceptions import NotFoundError
from ..models.database import Tables
from ..models.domain import ArchitectureArtifact, Project


class ProjectRepository:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables

    def add(self, project: Project) -> Project:
        return self.tables.projects.put(project)

    def get(self, project_id: str) -> Project | None:
        return self.tables.projects.get(project_id)

    def require(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project is None:
            raise NotFoundError(f"Project '{project_id}' not found.")
        return project

    def list(self) -> list[Project]:
        """Newest first."""
        return sorted(self.tables.projects.list(), key=lambda p: p.created_at, reverse=True)

    def artifacts(self, project_id: str, active_only: bool = True) -> list[ArchitectureArtifact]:
        items = self.tables.artifacts.list(project_id=project_id)
        if active_only:
            items = [a for a in items if a.active]
        return sorted(items, key=lambda a: (a.created_at, a.filename))

    def usable_artifacts(self, project_id: str) -> list[ArchitectureArtifact]:
        """Active artifacts that were parsed (fully or partially)."""
        return [a for a in self.artifacts(project_id) if a.parse_status in ("PARSED", "PARTIAL")]
