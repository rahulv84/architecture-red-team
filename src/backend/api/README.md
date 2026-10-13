# backend/api

The HTTP boundary. This package contains no business logic: route functions validate input, call a
service on the `AppContainer` and return Pydantic models or JSON-ready dicts.

## Purpose and responsibility

* Provide the FastAPI dependencies shared by all routers.
* Enforce transport-level concerns: upload size bounds, request validation, status codes.
* Everything domain-specific is delegated to `backend/services/*` via the container.

Route modules are documented in `routes/README.md`.

## Modules

| File | Contents |
| --- | --- |
| `routes/` | One router module per resource area (8 modules, mounted by `backend/main.py` under `/api/v1`) |

## Primary functions

| Name | Behaviour |
| --- | --- |
| `container_dep() -> AppContainer` | Returns `core.lifecycle.get_container()`; raises `RuntimeError` if the app has not started. |
| `Container` | `Annotated[AppContainer, Depends(container_dep)]`; used as `c: Container` in every route. |
| `read_upload(file, limit_bytes)` | Reads an `UploadFile` in 1 MiB chunks and raises `FileTooLargeError` (HTTP 413) once the running total exceeds `limit_bytes`. It never trusts `Content-Length`. |

## Inputs and outputs

* Input: HTTP requests (JSON bodies, multipart uploads, query parameters).
* Output: JSON using models from `backend/models/schemas.py` and `backend/models/domain.py`, or a
  `FileResponse` for report downloads. Errors are rendered by handlers registered in
  `backend/main.py`:
  * `AppError` subclasses -> `{"error": {"code", "message", "details"}}` with the exception's status.
  * `RequestValidationError` -> HTTP 422 with `code: request_validation_failed`.

## Dependencies

`core.lifecycle` (container), `core.exceptions`, `models.*`, and through the container every service.
Route modules also import `graph.builder.graph_topology` (reviews), `llm.provider_factory`
(health) and `tools.checkov_adapter.checkov_available` (health, settings) directly.

## Role in application execution

`python src/run.py` -> `uvicorn backend.main:app` -> `create_app()` includes the routers with prefix
`/api/v1`. The Streamlit UI (`frontend/api_client.py`) issues requests here. Long-running work
(`/start`, `/resume`, `/re-review`) returns HTTP 202 immediately; the actual graph run executes in a
`TaskManager` worker thread, and the UI polls `/reviews/{id}/status` and `/reviews/{id}/events`.
Interactive documentation is served by FastAPI at `/docs`.

## Extension points

* Add a route: see `routes/README.md`.
* Add a shared dependency (for example an auth check): define it in `deps.py` and add it to the
  routers' `dependencies=[...]` or to `create_app()`.

## Limitations

* No authentication, authorisation, CORS configuration or rate limiting.
* Route handlers are synchronous (`def`) except upload endpoints; they run in FastAPI's thread pool
  and rely on `Database.session`'s global lock for consistency.
* Some routes assemble views inline (for example `GET /reviews` computes counts per review), which
  is O(reviews x findings) per call.
