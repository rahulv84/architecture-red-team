"""Machine-readable assessment JSON (validated against :class:`AssessmentReport`)."""
from __future__ import annotations

import os
from pathlib import Path

from ..models.schemas import AssessmentReport


def write_json_report(report: AssessmentReport, path: Path) -> int:
    """Write atomically, then re-validate the file against the Pydantic model."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    AssessmentReport.model_validate_json(tmp.read_text(encoding="utf-8"))
    os.replace(tmp, path)
    return path.stat().st_size
