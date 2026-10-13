"""Thin synchronous httpx wrapper over the Architecture Red Team REST API.

The Streamlit UI never touches the database or LangGraph directly; every operation goes through this
module.  Each endpoint has one typed helper.  Failures are raised as :class:`ApiError`, built from the
backend error body ``{"error": {"code", "message", "details"}}`` (or from a transport failure, in which
case ``status`` is ``0`` and ``code`` is ``backend_unavailable``).

Configuration: ``ART_BACKEND_URL`` (default ``http://127.0.0.1:8000/api/v1``).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
BASE_URL = os.environ.get("ART_BACKEND_URL", DEFAULT_BASE_URL).rstrip("/")
DEFAULT_TIMEOUT = 10.0
LONG_TIMEOUT = 120.0
_EVENT_PAGE = 2000

# One pooled client for the whole Streamlit process.  trust_env is disabled so that a system HTTP proxy
# can never intercept traffic to the loopback backend.
_CLIENT = httpx.Client(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT, trust_env=False)

JsonObject = dict[str, Any]
JsonList = list[dict[str, Any]]
UploadItem = tuple[str, bytes, str]  # (filename, content, role)


class ApiError(Exception):
    """A backend or transport failure with the backend's structured error fields."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    @property
    def unavailable(self) -> bool:
        """True when the backend could not be reached at all."""
        return self.status == 0

    def __str__(self) -> str:
        return f"{self.message} [{self.code}]"


def _error_from_response(resp: httpx.Response) -> ApiError:
    """Translate a non-2xx response into an :class:`ApiError`."""
    try:
        body = resp.json()
    except ValueError:
        return ApiError(resp.status_code, "http_error", resp.text[:300] or resp.reason_phrase)
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return ApiError(resp.status_code, str(err.get("code", "http_error")), str(err.get("message", "Request failed.")), err.get("details"))
    detail = body.get("detail") if isinstance(body, dict) else None
    return ApiError(resp.status_code, "http_error", str(detail or "Request failed."), detail)


def _request(
    method: str, path: str, *, params: Mapping[str, Any] | None = None, json: Any = None, data: Mapping[str, Any] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None, timeout: float | None = None,
) -> httpx.Response:
    """Send one request; raise :class:`ApiError` for transport failures and non-2xx responses."""
    clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    try:
        resp = _CLIENT.request(method, path, params=clean or None, json=json, data=data, files=files,
                               timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
    except httpx.TimeoutException as exc:
        raise ApiError(0, "backend_timeout", f"The backend did not answer in time ({exc.__class__.__name__}).") from exc
    except httpx.TransportError as exc:
        raise ApiError(0, "backend_unavailable", f"Cannot reach the backend at {BASE_URL}.") from exc
    if resp.status_code >= 400:
        raise _error_from_response(resp)
    return resp


def _get(path: str, **params: Any) -> Any:
    return _request("GET", path, params=params).json()


def _post(path: str, payload: Any = None, *, timeout: float | None = None, **params: Any) -> Any:
    return _request("POST", path, json=payload, params=params, timeout=timeout).json()


def _multipart(items: Iterable[UploadItem]) -> tuple[list[tuple[str, tuple[str, bytes, str]]], list[str]]:
    """Repeated ``files`` parts plus an aligned ``roles`` list (one role per file)."""
    parts: list[tuple[str, tuple[str, bytes, str]]] = []
    roles: list[str] = []
    for name, content, role in items:
        parts.append(("files", (name, content, "application/octet-stream")))
        roles.append(role)
    return parts, roles


# --------------------------------------------------------------------------- platform
def health() -> JsonObject:
    """GET /health."""
    return _get("/health")


def config() -> JsonObject:
    """GET /config (public settings, tool availability, agent ids, notices)."""
    return _get("/config")


def providers() -> JsonList:
    """GET /providers (catalog with configuration status)."""
    return _get("/providers")


def validate_provider(provider: str, check_connectivity: bool = False) -> JsonObject:
    """POST /providers/validate."""
    return _post("/providers/validate", {"provider": provider, "check_connectivity": check_connectivity}, timeout=LONG_TIMEOUT)


# --------------------------------------------------------------------------- projects & documents
def list_projects() -> JsonList:
    """GET /projects."""
    return _get("/projects")


def create_project(name: str, description: str, cloud_environment: str) -> JsonObject:
    """POST /projects."""
    return _post("/projects", {"name": name, "description": description, "cloud_environment": cloud_environment})


def demo_scenarios() -> JsonList:
    """GET /demo/scenarios."""
    return _get("/demo/scenarios")


def create_demo_project(scenario_id: str) -> JsonObject:
    """POST /demo/scenarios/{id}/project (project preloaded with the scenario files)."""
    return _post(f"/demo/scenarios/{scenario_id}/project", timeout=LONG_TIMEOUT)


def supported_documents() -> JsonObject:
    """GET /documents/supported (extensions and roles)."""
    return _get("/documents/supported")


def upload_documents(project_id: str, items: Iterable[UploadItem]) -> JsonObject:
    """POST /projects/{id}/documents as multipart with aligned ``files`` and ``roles``."""
    parts, roles = _multipart(items)
    resp = _request("POST", f"/projects/{project_id}/documents", data={"roles": roles}, files=parts, timeout=LONG_TIMEOUT)
    return resp.json()


def list_documents(project_id: str, include_superseded: bool = False) -> JsonList:
    """GET /projects/{id}/documents."""
    return _get(f"/projects/{project_id}/documents", include_superseded=include_superseded or None)


def document_content(document_id: str, max_chars: int = 60000) -> JsonObject:
    """GET /documents/{id}/content (parsed text, sections, diagram nodes/edges, IaC resources)."""
    return _get(f"/documents/{document_id}/content", max_chars=max_chars)


# --------------------------------------------------------------------------- reviews
def create_review(payload: Mapping[str, Any]) -> JsonObject:
    """POST /reviews."""
    return _post("/reviews", dict(payload))


def list_reviews(project_id: str | None = None) -> JsonList:
    """GET /reviews (with finding counts, open_by_domain and severity_counts)."""
    return _get("/reviews", project_id=project_id)


def get_review(review_id: str) -> JsonObject:
    """GET /reviews/{id}."""
    return _get(f"/reviews/{review_id}")


def start_review(review_id: str) -> JsonObject:
    """POST /reviews/{id}/start."""
    return _post(f"/reviews/{review_id}/start")


def cancel_review(review_id: str) -> JsonObject:
    """POST /reviews/{id}/cancel."""
    return _post(f"/reviews/{review_id}/cancel")


def review_status(review_id: str) -> JsonObject:
    """GET /reviews/{id}/status."""
    return _get(f"/reviews/{review_id}/status")


def review_events(review_id: str, after: int = 0, limit: int = 500) -> JsonObject:
    """GET /reviews/{id}/events?after= (returns ``events`` and ``last_seq``)."""
    return _get(f"/reviews/{review_id}/events", after=after, limit=limit)


def last_event_seq(review_id: str) -> int:
    """Highest event sequence number of a review (0 when it has none)."""
    after = 0
    while True:
        page = review_events(review_id, after=after, limit=_EVENT_PAGE)
        if len(page["events"]) < _EVENT_PAGE:
            return int(page["last_seq"])
        after = int(page["last_seq"])


def review_agents(review_id: str, round_no: int | None = None) -> JsonObject:
    """GET /reviews/{id}/agents (executions, tool_summaries, tool_results)."""
    return _get(f"/reviews/{review_id}/agents", round=round_no)


def review_graph(review_id: str) -> JsonObject:
    """GET /reviews/{id}/graph (LangGraph topology, next_nodes, recovery_note)."""
    return _get(f"/reviews/{review_id}/graph")


def review_architecture(review_id: str, revision: int | None = None) -> JsonObject:
    """GET /reviews/{id}/architecture."""
    return _get(f"/reviews/{review_id}/architecture", revision=revision)


def review_score(review_id: str) -> JsonObject:
    """GET /reviews/{id}/score."""
    return _get(f"/reviews/{review_id}/score")


def review_evaluation(review_id: str) -> JsonObject:
    """GET /reviews/{id}/evaluation (observed fixture results)."""
    return _get(f"/reviews/{review_id}/evaluation")


def review_history(review_id: str) -> JsonObject:
    """GET /reviews/{id}/history."""
    return _get(f"/reviews/{review_id}/history")


# --------------------------------------------------------------------------- findings & decisions
def list_findings(review_id: str, **filters: Any) -> JsonList:
    """GET /reviews/{id}/findings; supported filters mirror the backend query parameters."""
    return _get(f"/reviews/{review_id}/findings", **filters)


def get_finding(finding_id: str) -> JsonObject:
    """GET /findings/{id} (finding plus decisions, rebuttals, tool results and ADR ids)."""
    return _get(f"/findings/{finding_id}")


def record_decision(review_id: str, payload: Mapping[str, Any]) -> JsonObject:
    """POST /reviews/{id}/decisions."""
    return _post(f"/reviews/{review_id}/decisions", dict(payload))


def list_decisions(review_id: str, pending_only: bool = False) -> JsonList:
    """GET /reviews/{id}/decisions."""
    return _get(f"/reviews/{review_id}/decisions", pending_only=pending_only or None)


def resume_review(review_id: str, action: str, notes: str = "") -> JsonObject:
    """POST /reviews/{id}/resume with ``submit_decisions`` or ``finalize``."""
    return _post(f"/reviews/{review_id}/resume", {"action": action, "notes": notes})


def create_revision(review_id: str, change_notes: str, use_demo_revision: bool, items: Iterable[UploadItem] = ()) -> JsonObject:
    """POST /reviews/{id}/revisions as multipart (change notes, optional demo revision, optional files)."""
    parts, roles = _multipart(items)
    data: dict[str, Any] = {"change_notes": change_notes, "use_demo_revision": "true" if use_demo_revision else "false"}
    if roles:
        data["roles"] = roles
    resp = _request("POST", f"/reviews/{review_id}/revisions", data=data, files=parts or None, timeout=LONG_TIMEOUT)
    return resp.json()


def list_revisions(review_id: str) -> JsonList:
    """GET /reviews/{id}/revisions."""
    return _get(f"/reviews/{review_id}/revisions")


def re_review(review_id: str, revision_id: str | None = None, notes: str = "") -> JsonObject:
    """POST /reviews/{id}/re-review (targeted re-review of the pending revision)."""
    return _post(f"/reviews/{review_id}/re-review", {"revision_id": revision_id, "notes": notes})


# --------------------------------------------------------------------------- reports
def report_preview(review_id: str) -> JsonObject:
    """GET /reviews/{id}/report-preview (structured assessment report, no files written)."""
    return _request("GET", f"/reviews/{review_id}/report-preview", timeout=LONG_TIMEOUT).json()


def list_adrs(review_id: str) -> JsonList:
    """GET /reviews/{id}/adrs."""
    return _get(f"/reviews/{review_id}/adrs")


def list_reports(review_id: str) -> JsonList:
    """GET /reviews/{id}/reports."""
    return _get(f"/reviews/{review_id}/reports")


def generate_reports(review_id: str, formats: str = "json,pdf") -> JsonList:
    """POST /reviews/{id}/reports (writes the PDF and JSON deliverables)."""
    return _post(f"/reviews/{review_id}/reports", timeout=LONG_TIMEOUT, formats=formats)


def download_report(report_id: str) -> bytes:
    """GET /reports/{id}/download (raw file bytes)."""
    return _request("GET", f"/reports/{report_id}/download", timeout=LONG_TIMEOUT).content


# --------------------------------------------------------------------------- settings
def get_settings() -> JsonObject:
    """GET /settings (secrets reduced to ``*_api_key_configured`` flags)."""
    return _get("/settings")


def update_settings(changes: Mapping[str, Any]) -> JsonObject:
    """PUT /settings with only the supplied fields."""
    return _request("PUT", "/settings", json=dict(changes)).json()


def tool_availability() -> JsonObject:
    """GET /settings/tools."""
    return _get("/settings/tools")
