"""Findings and tool-result persistence queries (including the Findings Explorer filters)."""
from __future__ import annotations

from ..core.exceptions import NotFoundError
from ..models.database import Tables, _StoredToolResult
from ..models.domain import SEVERITY_ORDER, Finding, ToolResult


class FindingsRepository:
    def __init__(self, tables: Tables) -> None:
        self.tables = tables

    # ---- findings --------------------------------------------------------------
    def list(self, review_id: str) -> list[Finding]:
        return self.tables.findings.list(review_id=review_id)

    def get(self, finding_id: str) -> Finding | None:
        return self.tables.findings.get(finding_id)

    def require(self, finding_id: str) -> Finding:
        finding = self.get(finding_id)
        if finding is None:
            raise NotFoundError(f"Finding '{finding_id}' not found.")
        return finding

    def save(self, finding: Finding) -> Finding:
        return self.tables.findings.put(finding)

    def replace_all(self, review_id: str, findings: list[Finding]) -> None:
        """Make the stored set equal ``findings`` (findings no longer present are removed)."""
        keep = {f.finding_id for f in findings}
        for old in self.tables.findings.list(review_id=review_id):
            if old.finding_id not in keep:
                self.tables.findings.delete(old.finding_id)
        self.tables.findings.put_many(findings)

    def filter(
        self, review_id: str, *, severity: str | None = None, agent: str | None = None, status: str | None = None,
        evidence_level: str | None = None, component: str | None = None, revision: int | None = None,
        moderator_status: str | None = None, include_rejected: bool = True, text: str | None = None,
    ) -> list[Finding]:
        out: list[Finding] = []
        for f in self.list(review_id):
            if severity and f.severity.value != severity.upper():
                continue
            if agent and agent not in f.contributing_agents and f.agent_id != agent:
                continue
            if status and f.status.value != status.upper():
                continue
            if evidence_level and f.evidence_level.value != evidence_level.upper():
                continue
            if component and not any(component.lower() in c.lower() for c in f.affected_components):
                continue
            if revision is not None and not (f.first_seen_revision <= revision <= f.last_seen_revision):
                continue
            if moderator_status and f.moderator_status.value != moderator_status.upper():
                continue
            if not include_rejected and f.moderator_status.value == "REJECTED":
                continue
            if text and text.lower() not in f"{f.title} {f.description}".lower():
                continue
            out.append(f)
        out.sort(key=lambda f: (-SEVERITY_ORDER[f.severity.value], f.title))
        return out

    # ---- tool results -------------------------------------------------------------
    def save_tool_results(self, review_id: str, revision: int, results: list[ToolResult]) -> None:
        self.tables.tool_results.put_many([
            _StoredToolResult(key=f"{review_id}:{revision}:{r.tool_result_id}", review_id=review_id, revision=revision, result=r)
            for r in results
        ])

    def tool_results(self, review_id: str, revision: int | None = None) -> list[ToolResult]:
        rows = self.tables.tool_results.list(review_id=review_id)
        if revision is not None:
            rows = [r for r in rows if r.revision == revision]
        latest: dict[str, ToolResult] = {}
        for r in sorted(rows, key=lambda x: x.revision):
            latest[r.result.tool_result_id] = r.result
        return list(latest.values())
