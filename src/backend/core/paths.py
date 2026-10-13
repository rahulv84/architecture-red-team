"""Filesystem layout for durable local data.  Never builds paths from user-supplied names."""
from __future__ import annotations

from pathlib import Path


class DataPaths:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def checkpoint_db(self) -> Path:
        return self.checkpoints / "langgraph.sqlite"

    def ensure(self) -> None:
        for d in (self.uploads, self.reports, self.snapshots, self.checkpoints, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    def artifact_dir(self, project_id: str, artifact_id: str) -> Path:
        return self.uploads / project_id / artifact_id

    def review_report_dir(self, review_id: str) -> Path:
        return self.reports / review_id

    def review_snapshot_dir(self, review_id: str) -> Path:
        return self.snapshots / review_id
