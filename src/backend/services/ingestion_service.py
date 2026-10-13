"""Document ingestion: validation, safe storage, hashing, parsing and the ingestion manifest."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import SettingsStore
from ..core.exceptions import FileTooLargeError, NotFoundError, ParseError, UnsupportedFileError
from ..core.paths import DataPaths
from ..models.database import Tables
from ..models.domain import ArchitectureArtifact, DocumentRole
from ..parsers.parser_registry import default_role, detect_type, parse_bytes, safe_filename
from ..parsers.types import ParsedDocument, ParserLimits

log = logging.getLogger(__name__)


@dataclass
class UploadResult:
    accepted: list[ArchitectureArtifact]
    rejected: list[dict[str, str]]


class IngestionService:
    def __init__(self, tables: Tables, paths: DataPaths, settings_store: SettingsStore) -> None:
        self.tables = tables
        self.paths = paths
        self.settings_store = settings_store

    # ---- limits -------------------------------------------------------------
    def limits(self) -> ParserLimits:
        s = self.settings_store.get()
        return ParserLimits(max_bytes=s.max_upload_mb * 1024 * 1024, max_pdf_pages=s.max_pdf_pages)

    # ---- ingestion ------------------------------------------------------------
    def add_document(
        self,
        project_id: str,
        filename: str,
        data: bytes,
        role: str | None = None,
        *,
        revision: int = 1,
        supersede: bool = True,
    ) -> ArchitectureArtifact:
        """Validate, store and parse one file.  Raises for unsupported/oversized files."""
        limits = self.limits()
        if len(data) > limits.max_bytes:
            raise FileTooLargeError(f"'{filename}' is {len(data) // (1024 * 1024)} MB; the limit is {limits.max_bytes // (1024 * 1024)} MB.")
        if not data:
            raise UnsupportedFileError(f"'{filename}' is empty.")
        safe_name = safe_filename(filename)
        detected = detect_type(safe_name, data)
        sha = hashlib.sha256(data).hexdigest()

        existing = [a for a in self.tables.artifacts.list(project_id=project_id) if a.active]
        if len(existing) >= self.settings_store.get().max_files_per_project:
            raise UnsupportedFileError("Project file limit reached.")
        for art in existing:
            if art.filename.lower() == safe_name.lower() and art.sha256 == sha:
                return art  # idempotent re-upload
        resolved_role = DocumentRole(role) if role else DocumentRole(default_role(detected))
        artifact = ArchitectureArtifact(
            project_id=project_id, filename=safe_name, stored_path="", sha256=sha, size_bytes=len(data),
            detected_type=detected, role=resolved_role, revision_added=revision,
        )
        adir = self.paths.artifact_dir(project_id, artifact.artifact_id)
        adir.mkdir(parents=True, exist_ok=True)
        stored = adir / safe_name
        tmp = adir / (safe_name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, stored)
        artifact.stored_path = str(stored)

        try:
            parsed = parse_bytes(data, safe_name, detected, limits, image_dir=adir / "images")
            parsed.artifact_id = artifact.artifact_id
            parsed.filename = safe_name
            for res in parsed.resources:
                res.artifact_id, res.filename = artifact.artifact_id, safe_name
            artifact.parse_status = "PARTIAL" if parsed.warnings else "PARSED"
            artifact.parse_warnings = parsed.warnings
            artifact.page_count = parsed.page_count
            artifact.requires_vision = parsed.requires_vision
            (adir / "parsed.json").write_text(parsed.model_dump_json(), encoding="utf-8")
        except ParseError as exc:
            artifact.parse_status = "FAILED"
            artifact.parse_error = exc.message
        self.tables.artifacts.put(artifact)

        if supersede:
            for art in existing:
                if art.filename.lower() == safe_name.lower() and art.artifact_id != artifact.artifact_id:
                    art.active = False
                    art.superseded_by = artifact.artifact_id
                    self.tables.artifacts.put(art)
        return artifact

    def add_documents(
        self, project_id: str, items: list[tuple[str, bytes, str | None]], *, revision: int = 1
    ) -> UploadResult:
        accepted: list[ArchitectureArtifact] = []
        rejected: list[dict[str, str]] = []
        for filename, data, role in items:
            try:
                accepted.append(self.add_document(project_id, filename, data, role, revision=revision))
            except (UnsupportedFileError, FileTooLargeError) as exc:
                rejected.append({"filename": filename, "error": exc.message, "code": exc.code})
        return UploadResult(accepted, rejected)

    # ---- reads -------------------------------------------------------------------
    def get(self, artifact_id: str) -> ArchitectureArtifact:
        art = self.tables.artifacts.get(artifact_id)
        if art is None:
            raise NotFoundError(f"Document '{artifact_id}' not found.")
        return art

    def list_for_project(self, project_id: str, active_only: bool = True) -> list[ArchitectureArtifact]:
        arts = self.tables.artifacts.list(project_id=project_id)
        if active_only:
            arts = [a for a in arts if a.active]
        return sorted(arts, key=lambda a: (a.created_at, a.filename))

    def parsed(self, artifact_id: str) -> ParsedDocument | None:
        art = self.tables.artifacts.get(artifact_id)
        if art is None:
            return None
        pj = Path(art.stored_path).parent / "parsed.json"
        if not pj.exists():
            return None
        return ParsedDocument.model_validate_json(pj.read_text(encoding="utf-8"))

    def raw_bytes(self, artifact_id: str) -> bytes:
        art = self.get(artifact_id)
        return Path(art.stored_path).read_bytes()

    def content_view(self, artifact_id: str, max_chars: int = 60_000) -> dict:
        art = self.get(artifact_id)
        parsed = self.parsed(artifact_id)
        return {
            "artifact_id": art.artifact_id,
            "filename": art.filename,
            "detected_type": art.detected_type,
            "kind": parsed.kind if parsed else None,
            "parse_status": art.parse_status,
            "parse_error": art.parse_error,
            "warnings": art.parse_warnings,
            "text": (parsed.text[:max_chars] if parsed else ""),
            "truncated": bool(parsed and len(parsed.text) > max_chars),
            "sections": [s.model_dump() for s in (parsed.sections[:200] if parsed else [])],
            "nodes": [n.model_dump() for n in (parsed.nodes if parsed else [])],
            "edges": [e.model_dump() for e in (parsed.edges if parsed else [])],
            "resources": [
                {"address": r.address, "type": r.resource_type, "framework": r.framework, "line": r.line}
                for r in (parsed.resources if parsed else [])
            ],
        }

    def manifest(self, project_id: str) -> list[dict]:
        return [
            {
                "artifact_id": a.artifact_id, "filename": a.filename, "sha256": a.sha256, "size_bytes": a.size_bytes,
                "detected_type": a.detected_type, "role": a.role.value, "parse_status": a.parse_status,
                "stored_path": a.stored_path, "active": a.active,
            }
            for a in self.list_for_project(project_id, active_only=False)
        ]
