"""Optional Checkov integration.

Runs ``checkov`` as a bounded subprocess against *copies* of the uploaded IaC files placed
in a private temporary directory.  Results are normalised to :class:`ToolResult`.  When
Checkov is not installed the adapter reports ``NOT_EXECUTED`` - it never fabricates output.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..models.domain import EvidenceLevel, Severity, SourceReference, ToolResult, ToolRunSummary
from .base import make_result

TOOL_ID, TOOL_NAME = "checkov", "Checkov"

_SEVERITY_MAP = {"CRITICAL": Severity.CRITICAL, "HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW}
_SCANNABLE = {"terraform": ".tf", "cloudformation": None, "kubernetes": None}


def checkov_available() -> str | None:
    exe = shutil.which("checkov")
    if exe:
        return exe
    scripts = Path(sys.executable).parent
    for name in ("checkov", "checkov.exe"):
        cand = scripts / name
        if cand.exists():
            return str(cand)
    return None


def run_checkov(
    files: list[tuple[str, str, str, str]],  # (artifact_id, filename, kind, text)
    timeout_seconds: int = 120,
) -> tuple[list[ToolResult], ToolRunSummary]:
    exe = checkov_available()
    if exe is None:
        return [], ToolRunSummary(
            tool_id=TOOL_ID, tool_name=TOOL_NAME, status="NOT_EXECUTED",
            detail="Checkov is not installed; its coverage was not executed. Built-in checks were used instead.",
        )
    scan_files = [f for f in files if f[2] in _SCANNABLE]
    if not scan_files:
        return [], ToolRunSummary(tool_id=TOOL_ID, tool_name=TOOL_NAME, status="NOT_EXECUTED", detail="No Terraform/CloudFormation/Kubernetes files to scan.")
    results: list[ToolResult] = []
    with tempfile.TemporaryDirectory(prefix="art_checkov_") as tmp:
        tmp_path = Path(tmp)
        mapping: dict[str, tuple[str, str]] = {}
        for idx, (artifact_id, filename, _kind, text) in enumerate(scan_files):
            suffix = Path(filename).suffix if Path(filename).suffix in (".tf", ".yaml", ".yml", ".json") else ".yaml"
            local = tmp_path / f"f{idx}{suffix}"
            local.write_text(text, encoding="utf-8")
            mapping[local.name] = (artifact_id, filename)
        cmd = [exe, "-d", str(tmp_path), "--output", "json", "--quiet", "--compact", "--skip-download"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False, shell=False)
        except subprocess.TimeoutExpired:
            return [], ToolRunSummary(tool_id=TOOL_ID, tool_name=TOOL_NAME, status="ERROR", detail=f"Checkov timed out after {timeout_seconds}s.")
        except OSError as exc:
            return [], ToolRunSummary(tool_id=TOOL_ID, tool_name=TOOL_NAME, status="ERROR", detail=f"Could not start Checkov: {exc}")
        try:
            payload = json.loads(proc.stdout or "null")
        except ValueError:
            return [], ToolRunSummary(tool_id=TOOL_ID, tool_name=TOOL_NAME, status="ERROR",
                                      detail=f"Checkov produced unparsable output (exit {proc.returncode}).")
    reports = payload if isinstance(payload, list) else [payload] if payload else []
    checked = 0
    for report in reports:
        summary = (report or {}).get("summary", {})
        checked += int(summary.get("passed", 0)) + int(summary.get("failed", 0))
        for failed in ((report or {}).get("results") or {}).get("failed_checks", []):
            local_name = Path(failed.get("file_path", "")).name
            artifact_id, filename = mapping.get(local_name, (None, local_name))
            sev = _SEVERITY_MAP.get(str(failed.get("severity") or "MEDIUM").upper(), Severity.MEDIUM)
            lines = failed.get("file_line_range") or [None, None]
            res = make_result(
                tool_id=TOOL_ID, tool_name=TOOL_NAME, rule_id=str(failed.get("check_id", "CKV_UNKNOWN")), domain="security", res=None,
                title=str(failed.get("check_name", "Checkov finding")),
                message=f"Checkov {failed.get('check_id')} failed for {failed.get('resource')}.",
                observed=str(failed.get("resource")), expected=str(failed.get("check_name")),
                level=EvidenceLevel.CONFIRMED, severity=sev, components=[str(failed.get("resource", "")).lstrip("/")],
                qualifier=f"{artifact_id}|{failed.get('resource')}",
            )
            res.source_artifact_id = artifact_id
            res.evidence_reference = SourceReference(
                artifact_id=artifact_id, filename=filename, resource_path=str(failed.get("resource")),
                line_start=lines[0], line_end=lines[1],
            )
            res.raw_result_reference = f"checkov:{failed.get('check_id')}"
            results.append(res)
    return results, ToolRunSummary(
        tool_id=TOOL_ID, tool_name=TOOL_NAME, status="EXECUTED", resources_checked=checked, violations=len(results),
        detail=f"{len(results)} failed check(s) reported by Checkov.",
    )
