"""Atomic, versioned JSON snapshots of the in-memory application database.

The primary database is volatile by design.  Every committed write schedules a debounced
snapshot (``state.json`` + a ``.bak`` of the previous version); on startup the database is
restored from it.  Per-review snapshot files capture stage boundaries for audit.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from ..core.config import SettingsStore
from ..core.paths import DataPaths
from ..models.database import Tables
from ..models.domain import utcnow

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1


class SnapshotService:
    def __init__(self, tables: Tables, paths: DataPaths, settings_store: SettingsStore) -> None:
        self.tables = tables
        self.paths = paths
        self.settings_store = settings_store
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._enabled = False

    @property
    def state_path(self) -> Path:
        return self.paths.snapshots / "state.json"

    # ------------------------------------------------------------------ lifecycle
    def enable_autosave(self) -> None:
        if self._enabled:
            return
        self._enabled = True
        self.tables.db.on_commit(self.schedule_save)

    def schedule_save(self) -> None:
        if not self._enabled:
            return
        delay = self.settings_store.get().snapshot_debounce_seconds
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(delay, self._safe_save)
            self._timer.daemon = True
            self._timer.start()

    def _safe_save(self) -> None:
        try:
            self.save_now()
        except Exception:  # snapshotting must never break request handling; failure is logged loudly
            log.exception("Snapshot save failed")

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self.save_now()

    # ------------------------------------------------------------------ save / restore
    def save_now(self) -> Path:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": utcnow().isoformat(),
            "tables": {name: [m.model_dump(mode="json") for m in table.list()] for name, table in self.tables.entity_tables().items()},
            "events": self.tables.dump_events(),
        }
        self.paths.snapshots.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        if self.state_path.exists():
            shutil.copy2(self.state_path, self.state_path.with_suffix(".json.bak"))
        os.replace(tmp, self.state_path)
        return self.state_path

    def restore(self) -> dict[str, Any]:
        """Load the latest valid snapshot into the (empty) in-memory database."""
        for candidate in (self.state_path, self.state_path.with_suffix(".json.bak")):
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if payload.get("schema_version") != SCHEMA_VERSION:
                    raise ValueError(f"unsupported snapshot schema {payload.get('schema_version')}")
                counts: dict[str, int] = {}
                for name, table in self.tables.entity_tables().items():
                    rows = [table.model_cls.model_validate(d) for d in payload.get("tables", {}).get(name, [])]
                    table.put_many(rows)
                    counts[name] = len(rows)
                self.tables.restore_events(payload.get("events", []))
                return {"restored": True, "source": candidate.name, "saved_at": payload.get("saved_at"), "counts": counts}
            except (ValueError, OSError, KeyError) as exc:
                log.error("Snapshot %s unusable (%s); trying fallback", candidate.name, exc)
                self.tables.db.reset()
        return {"restored": False, "source": None, "saved_at": None, "counts": {}}

    # ------------------------------------------------------------------ per-review audit snapshots
    def save_review_snapshot(self, review_id: str, label: str) -> Path | None:
        review = self.tables.reviews.get(review_id)
        if review is None:
            return None
        model_rows = self.tables.models.list(review_id=review_id)
        payload = {
            "schema_version": SCHEMA_VERSION, "label": label, "saved_at": utcnow().isoformat(),
            "review": review.model_dump(mode="json"),
            "findings": [f.model_dump(mode="json") for f in self.tables.findings.list(review_id=review_id)],
            "decisions": [d.model_dump(mode="json") for d in self.tables.decisions.list(review_id=review_id)],
            "rebuttals": [r.model_dump(mode="json") for r in self.tables.rebuttals.list(review_id=review_id)],
            "adrs": [a.model_dump(mode="json") for a in self.tables.adrs.list(review_id=review_id)],
            "revisions": [r.model_dump(mode="json") for r in self.tables.revisions.list(review_id=review_id)],
            "architecture_models": [m.model_dump(mode="json") for m in model_rows],
        }
        folder = self.paths.review_snapshot_dir(review_id)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%dT%H%M%S%f")
        path = folder / f"{stamp}-{label}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        return path
