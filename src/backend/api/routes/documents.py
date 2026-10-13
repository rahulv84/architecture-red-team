"""Document upload, manifest and content endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, UploadFile

from ...core.exceptions import NotFoundError, ValidationFailed
from ...models.domain import DocumentRole
from ...models.schemas import UploadResponse
from ...parsers.parser_registry import SUPPORTED_EXTENSIONS
from ..deps import Container, read_upload

router = APIRouter(tags=["documents"])


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_documents(
    project_id: str, c: Container, files: list[UploadFile] = File(...), roles: list[str] = Form(default=[]),
) -> UploadResponse:
    if c.tables.projects.get(project_id) is None:
        raise NotFoundError(f"Project '{project_id}' not found.")
    valid_roles = {r.value for r in DocumentRole}
    for r in roles:
        if r and r not in valid_roles:
            raise ValidationFailed(f"Unknown document role '{r}'.", details={"valid_roles": sorted(valid_roles)})
    limit = c.settings_store.get().max_upload_mb * 1024 * 1024
    items: list[tuple[str, bytes, str | None]] = []
    rejected: list[dict[str, str]] = []
    for idx, f in enumerate(files):
        try:
            data = await read_upload(f, limit)
        except Exception as exc:
            rejected.append({"filename": f.filename or f"file-{idx}", "error": getattr(exc, "message", str(exc)), "code": getattr(exc, "code", "error")})
            continue
        items.append((f.filename or f"upload-{idx}", data, (roles[idx] if idx < len(roles) and roles[idx] else None)))
    result = c.ingestion.add_documents(project_id, items)
    return UploadResponse(accepted=[a.model_dump(mode="json") for a in result.accepted], rejected=rejected + result.rejected)


@router.get("/projects/{project_id}/documents")
def list_documents(project_id: str, c: Container, include_superseded: bool = False) -> list[dict[str, Any]]:
    if c.tables.projects.get(project_id) is None:
        raise NotFoundError(f"Project '{project_id}' not found.")
    return [a.model_dump(mode="json") for a in c.ingestion.list_for_project(project_id, active_only=not include_superseded)]


@router.get("/projects/{project_id}/manifest")
def manifest(project_id: str, c: Container) -> list[dict[str, Any]]:
    return c.ingestion.manifest(project_id)


@router.get("/documents/supported")
def supported_types() -> dict[str, Any]:
    return {"extensions": list(SUPPORTED_EXTENSIONS), "roles": [r.value for r in DocumentRole]}


@router.get("/documents/{document_id}")
def get_document(document_id: str, c: Container) -> dict[str, Any]:
    return c.ingestion.get(document_id).model_dump(mode="json")


@router.get("/documents/{document_id}/content")
def get_document_content(document_id: str, c: Container, max_chars: int = 60000) -> dict[str, Any]:
    return c.ingestion.content_view(document_id, max_chars=max(1000, min(max_chars, 500_000)))
