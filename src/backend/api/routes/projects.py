"""Projects and offline demo scenarios."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...models.domain import Project
from ...models.schemas import ProjectCreate
from ..deps import Container

router = APIRouter(tags=["projects"])


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, c: Container) -> Project:
    project = Project(name=payload.name.strip(), description=payload.description, cloud_environment=payload.cloud_environment)
    return c.reviews.projects_repo.add(project)


@router.get("/projects")
def list_projects(c: Container) -> list[dict[str, Any]]:
    out = []
    for p in c.reviews.projects_repo.list():
        reviews = c.reviews.list(p.project_id)
        out.append({**p.model_dump(mode="json"), "document_count": len(c.ingestion.list_for_project(p.project_id)), "review_count": len(reviews)})
    return out


@router.get("/projects/{project_id}")
def get_project(project_id: str, c: Container) -> dict[str, Any]:
    p = c.reviews.projects_repo.require(project_id)
    return {
        **p.model_dump(mode="json"),
        "documents": [a.model_dump(mode="json") for a in c.ingestion.list_for_project(project_id)],
        "reviews": [r.model_dump(mode="json") for r in c.reviews.list(project_id)],
    }


@router.get("/demo/scenarios")
def demo_scenarios(c: Container) -> list[dict[str, Any]]:
    return c.demo.list_scenarios()


@router.post("/demo/scenarios/{scenario_id}/project", status_code=201)
def create_demo_project(scenario_id: str, c: Container) -> dict[str, Any]:
    """Create a project preloaded with the scenario's sample architecture files (SIMULATED DEMO)."""
    meta, files = c.demo.scenario_files(scenario_id)
    project = c.reviews.projects_repo.add(c.demo.new_project(scenario_id))
    result = c.ingestion.add_documents(project.project_id, files)
    return {"project": project.model_dump(mode="json"), "scenario": {k: meta[k] for k in ("scenario_id", "name", "description")},
            "documents": [a.model_dump(mode="json") for a in result.accepted], "rejected": result.rejected}
