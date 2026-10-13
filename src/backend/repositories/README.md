# backend/repositories

Query-oriented persistence helpers built on top of the in-memory `Tables` layer
(`backend/models/database.py`). Services and routes use them so SQL-ish filtering and ordering live in one place
instead of being repeated in business logic.

## Modules

| Module | Class | Responsibility |
| --- | --- | --- |
| `project_repository.py` | `ProjectRepository` | `add`, `get`, `require` (raises `NotFoundError`), `list` (newest first), `artifacts(project_id, active_only)`, `usable_artifacts` (PARSED/PARTIAL) |
| `review_repository.py` | `ReviewRepository` | reviews (`add`, `save`, `get`, `require`, `list`), revisions (`add_revision`, `revision`, `revisions`), architecture models per revision (`save_model`, `get_model`), agent executions (`executions`, `executions_for`, `save_execution`), events (`add_event`, `events`) |
| `findings_repository.py` | `FindingsRepository` | findings (`list`, `get`, `require`, `save`, `replace_all`, `filter` with the Findings Explorer criteria) and tool results per revision (`save_tool_results`, `tool_results`) |

## Inputs, outputs, dependencies

* Input: a `Tables` instance; domain models from `backend/models/domain.py`.
* Output: validated Pydantic models (never ORM rows).
* Depends on `backend.models.database` (including its private `_StoredModel` / `_StoredToolResult` row wrappers),
  `backend.core.exceptions.NotFoundError`.

## Participation in execution

`ReviewService` (`backend/services/review_service.py`) owns one instance of each repository
(`repo`, `findings_repo`, `projects_repo`) and delegates its persistence methods to them. The REST routes
`/projects` and `/reviews/{id}/findings` call the repositories through the service. All access still funnels
through `Database.session()`, so the single-lock guarantees of the shared in-memory database apply.

## Extension points

* Add a repository class here, construct it in the service that needs it, and expose only the queries that
  service uses. Keep write paths going through `EntityTable.put` so snapshots (`Tables.entity_tables()`) stay complete.
* A different storage backend only needs to honour the same method names as `EntityTable`/`Tables`.

## Limitations

* `EntityTable.list` loads and validates every matching row; `FindingsRepository.filter` filters in Python.
  This is adequate for review-sized data sets, not for large multi-tenant volumes.
* Some code paths (graph nodes, report service) still use `Tables` directly for simple lookups.
