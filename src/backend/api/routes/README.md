# backend/api/routes

One FastAPI `APIRouter` per resource area. All routers are mounted with prefix `/api/v1` by
`backend/main.py::create_app` in this order: `health, projects, documents, reviews, findings,
decisions, reports, settings`.

## Purpose and responsibility

Translate HTTP calls into service calls on the `AppContainer` and shape the responses. Business rules
(state checks, validation of decisions, parsing) live in the services; routes only add transport
validation (roles, size limits, query filters).

## Modules and endpoints

| Module | Endpoints |
| --- | --- |
| `health.py` | `GET /health`, `GET /config`, `GET /providers`, `POST /providers/validate` |
| `projects.py` | `POST /projects`, `GET /projects`, `GET /projects/{project_id}`, `GET /demo/scenarios`, `POST /demo/scenarios/{scenario_id}/project` |
| `documents.py` | `POST /projects/{project_id}/documents` (multipart `files`, optional `roles`), `GET /projects/{project_id}/documents`, `GET /projects/{project_id}/manifest`, `GET /documents/supported`, `GET /documents/{document_id}`, `GET /documents/{document_id}/content` |
| `reviews.py` | `POST /reviews`, `GET /reviews`, `GET /reviews/{id}`, `POST /reviews/{id}/start`, `POST /reviews/{id}/cancel`, `GET /reviews/{id}/status`, `/events`, `/agents`, `/architecture`, `/score`, `/evaluation`, `/graph` |
| `findings.py` | `GET /reviews/{id}/findings` (filters: severity, agent, status, evidence_level, component, revision, moderator_status, include_rejected, q), `GET /findings/{finding_id}`, `GET /reviews/{id}/tool-results` |
| `decisions.py` | `POST/GET /reviews/{id}/decisions`, `POST /reviews/{id}/resume`, `POST/GET /reviews/{id}/revisions`, `POST /reviews/{id}/re-review`, `GET /reviews/{id}/history` |
| `reports.py` | `POST /reviews/{id}/reports?formats=json,pdf`, `GET /reviews/{id}/reports`, `GET /reviews/{id}/report-preview`, `GET /reports/{report_id}/download`, `GET /reviews/{id}/adrs` |
| `settings.py` | `GET /settings`, `PUT /settings`, `GET /settings/tools` |

Every path above is relative to `/api/v1`.

## Primary functions (selected)

| Function | Delegates to |
| --- | --- |
| `reviews.create_review` | `ReviewService.create_review` |
| `reviews.start_review` | `WorkflowService.start` (HTTP 202) |
| `reviews.review_status` | `ReviewService.status_view` |
| `reviews.review_graph` | `graph_topology(c.executor.graph)` + `GraphExecutor.state_values/snapshot/is_interrupted` |
| `decisions.record_decision` | `DecisionService.record` |
| `decisions.resume_review` | `WorkflowService.resume` (HTTP 202) |
| `decisions.create_revision` | `DemoService.revision_files` (optional) + `RevisionService.create_revision` |
| `decisions.re_review` | `WorkflowService.re_review` (HTTP 202) |
| `documents.upload_documents` | `read_upload` + `IngestionService.add_documents` |
| `reports.generate_reports` | `ReportService.generate` |
| `reports.download_report` | `FileResponse` from `ReportArtifact.path` |
| `settings.update_settings` | `SettingsStore.update`, then `LIMITER.resize(max_concurrent_agents)` |

## Inputs and outputs

* Request models: `ProjectCreate`, `ReviewCreate`, `DecisionCreate`, `ResumeRequest`,
  `ReReviewRequest`, `ProviderValidateRequest`, `SettingsUpdate` (`models/schemas.py`).
* Response models: `Project`, `Review`, `Finding`, `HumanDecision`, `ReportArtifact`,
  `ReviewStatusView`, `FindingView`, `AgentExecutionsView`, `HistoryView`, `Scorecard`,
  `UploadResponse`; several endpoints return plain dicts built from `model_dump(mode="json")`.
* Document upload: unknown `roles` values are rejected with 422 (`valid_roles` in `details`);
  per-file failures are returned in `rejected` rather than failing the whole upload.

## Dependencies

`..deps` (`Container`, `read_upload`), `...models.*`, `...core.exceptions`, `...graph.builder`,
`...llm.provider_factory`, `...llm.base_provider.LIMITER`, `...parsers.parser_registry`,
`...tools.checkov_adapter`.

## Role in application execution

These routes are the only entry points the UI uses. The review life cycle is:
`POST /reviews` -> `POST /reviews/{id}/start` -> poll `/status` and `/events` ->
`AWAITING_HUMAN` -> `POST /decisions` (repeat) -> `POST /resume` or `POST /revisions` +
`POST /re-review` -> ... -> `POST /resume {"action": "finalize"}` -> `POST /reports`.
`GET /reviews/{id}/evaluation` compares findings with `demo/expected_results/<scenario>.json`.

## Extension points

1. Create `backend/api/routes/<name>.py` with `router = APIRouter(tags=["<name>"])`.
2. Take `c: Container` as a parameter and call services; raise `AppError` subclasses
   (`NotFoundError`, `ValidationFailed`, `ConflictError`, ...) for expected failures.
3. Add the module to the import and the `for module in (...)` loop in `backend/main.py`.
4. If the route needs new data, add a service method rather than touching tables from the route
   (some existing routes read `c.tables.*` directly; prefer services for new code).

## Limitations

* `POST /reviews/{id}/revisions` with `use_demo_revision` only works for reviews with a
  `scenario_id` and always uses the single predefined `revision-1` of that scenario.
* `GET /reviews/{id}/graph` swallows state-read errors and returns empty values before the first run.
* `POST /reviews/{id}/reports` silently drops unknown format names and falls back to `json,pdf`
  only when no valid format remains.
* Pagination is limited to `/reviews/{id}/events` (`after`, `limit`); list endpoints return full sets.
