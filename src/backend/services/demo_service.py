"""Offline demonstration scenarios (fixtures under ``demo/``)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.config import SettingsStore
from ..core.exceptions import NotFoundError
from ..models.domain import Project


class DemoService:
    def __init__(self, settings_store: SettingsStore) -> None:
        self.settings_store = settings_store

    @property
    def root(self) -> Path:
        return self.settings_store.get().demo_dir

    def _meta(self, scenario_id: str) -> dict[str, Any]:
        path = self.root / "fixtures" / scenario_id / "scenario.json"
        if not path.exists():
            raise NotFoundError(f"Demo scenario '{scenario_id}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_scenarios(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        base = self.root / "fixtures"
        for meta_path in sorted(base.glob("*/scenario.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            out.append({
                "scenario_id": meta["scenario_id"], "name": meta["name"], "description": meta["description"],
                "cloud_environment": meta.get("cloud_environment", "Unknown"),
                "files": [f["filename"] for f in meta.get("files", [])],
                "seeded_flaws": len(self.expected(meta["scenario_id"]).get("expected_flaws", [])),
                "revision": {"title": meta.get("revision", {}).get("title"), "change_notes": meta.get("revision", {}).get("change_notes"),
                             "files": [f["filename"] for f in meta.get("revision", {}).get("files", [])]},
                "simulated": True,
            })
        return out

    def scenario_files(self, scenario_id: str) -> tuple[dict[str, Any], list[tuple[str, bytes, str]]]:
        meta = self._meta(scenario_id)
        folder = self.root / "fixtures" / scenario_id
        files = [(f["filename"], (folder / f["filename"]).read_bytes(), f["role"]) for f in meta["files"]]
        return meta, files

    def revision_files(self, scenario_id: str) -> tuple[str, list[tuple[str, bytes, str | None]]]:
        meta = self._meta(scenario_id)
        rev = meta.get("revision")
        if not rev:
            raise NotFoundError(f"Scenario '{scenario_id}' defines no predefined revision.")
        folder = self.root / "fixtures" / scenario_id / "revision-1"
        files = [(f["filename"], (folder / f["filename"]).read_bytes(), f.get("role")) for f in rev["files"]]
        return rev.get("change_notes", ""), files

    def expected(self, scenario_id: str) -> dict[str, Any]:
        path = self.root / "expected_results" / f"{scenario_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def new_project(self, scenario_id: str) -> Project:
        meta = self._meta(scenario_id)
        return Project(name=f"DEMO: {meta['name']}", description=meta["description"],
                       cloud_environment=meta.get("cloud_environment", "Unknown"), scenario_id=scenario_id)
