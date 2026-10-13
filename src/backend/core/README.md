# backend/core

Cross-cutting infrastructure: settings, data-directory layout, logging, error types, the background
task manager and the application container (service wiring and process lifecycle).

## Purpose and responsibility

* Define and edit configuration (`config.py`).
* Own the filesystem layout of durable data (`paths.py`).
* Provide a stable error vocabulary mapped to HTTP statuses (`exceptions.py`).
* Redact secrets from logs (`logging.py`).
* Run background work in-process (`tasks.py`).
* Construct and expose every runtime service (`lifecycle.py`).

## Modules

| File | Primary classes / functions |
| --- | --- |
| `config.py` | `Settings` (pydantic-settings), `SettingsStore`, `EDITABLE_FIELDS`, `SECRET_FIELDS`, `SUPPORTED_PROVIDERS`, `ROOT_DIR` |
| `paths.py` | `DataPaths`: `uploads`, `reports`, `snapshots`, `checkpoints`, `logs`, `checkpoint_db`, `ensure()`, `artifact_dir(project_id, artifact_id)`, `review_report_dir(review_id)`, `review_snapshot_dir(review_id)` |
| `exceptions.py` | `AppError` and subclasses: `NotFoundError` 404, `ValidationFailed` 422, `ConflictError` 409, `UnsupportedFileError` 415, `FileTooLargeError` 413, `ParseError` 422, `ProviderError` 502, `ProviderAuthError` 401, `ProviderRateLimitError` 429, `ProviderTimeoutError` 504, `ProviderConfigError` 400, `StructuredOutputError` 502, `ReviewCancelled` 409 |
| `logging.py` | `setup_logging(log_dir)`, `RedactingFilter`, `redact(text)` |
| `tasks.py` | `TaskManager.submit(key, fn)`, `is_active`, `wait`, `shutdown` |
| `lifecycle.py` | `AppContainer`, `ReviewCache`, `get_container()`, `set_container()` |

## Key behaviour

* **`SettingsStore`** is thread-safe (RLock). On construction it reads `<data_dir>/settings.json`
  and overlays only keys in `EDITABLE_FIELDS`. `update(changes)` raises `KeyError` for unknown keys
  and `ValueError` for an unsupported provider; secrets (`anthropic_api_key`, `openai_api_key`)
  are never written to disk and `Settings.public_view()` reports only `*_configured` flags.
* **`TaskManager.submit`** is keyed (the key is the review id): a second submit while the first is
  active returns `False`, which the executor turns into HTTP 409.
* **`AppContainer`** builds, in order: `SettingsStore`, `DataPaths`, `Database`/`Tables`,
  `IngestionService`, `ContextService`, `ArchitectureService`, `ReviewService`, `DecisionService`,
  `ScoringService`, `DemoService`, `EvaluationService`, `ReportService`, `SnapshotService`,
  `RevisionService`, `WorkflowService`, `TaskManager`, `ReviewCache`. `executor` (the
  `GraphExecutor`) is created lazily because it imports LangGraph.
  * `provider_for(review)` returns `MockProvider` for `review.mode == "demo"` and never falls back to
    mock for live reviews; `agent_for(review_id, agent_id)` instantiates `AGENT_CLASSES[agent_id]`.
  * `startup()`: `SnapshotService.restore()`, `GraphExecutor.recover_on_startup()`,
    `SnapshotService.enable_autosave()`. `shutdown()`: final snapshot flush, task pool shutdown,
    checkpoint connection close, `Database.dispose()`.
* **`ReviewCache`** caches parsed documents, the per-review `TfidfIndex` and Checkov results keyed by
  `(review_id, revision)`. It is rebuildable and never authoritative; `invalidate(review_id)` is
  called when a revision is applied.
* **Logging**: `setup_logging` attaches a console handler and a `RotatingFileHandler`
  (`data/logs/backend.log`, 2 MB x 3) to the root logger once; both pass through `RedactingFilter`
  (API-key patterns, `api_key|secret|password|token` assignments).

## Inputs and outputs

* Inputs: environment variables / `.env`, `data/settings.json`, constructor arguments (tests pass a
  `Settings` and `persist_settings=False, autosave=False`).
* Outputs: the container singleton, directories created under the data root, log files.

## Dependencies

`config`, `paths`, `exceptions`, `logging` and `tasks` depend only on the standard library and
pydantic. `lifecycle.py` imports `agents`, `llm`, `models` and every service, and the graph executor
lazily; it is the only module in `core/` that sits above the other layers.

## Role in application execution

`backend/main.py` creates `AppContainer(start_logging=True)` in the FastAPI lifespan, calls
`startup()` and stores it with `set_container`. Routes receive it via `Depends(container_dep)`;
LangGraph nodes call `get_container()`. `python src/run.py` independently creates the data
subdirectories and reads `.env` before spawning uvicorn.

## Extension points

* New setting: add a field to `Settings`; add its name to `EDITABLE_FIELDS` and to
  `models/schemas.py::SettingsUpdate` if it should be editable at runtime.
* New error type: subclass `AppError` with `status_code` and `code`.
* New service: construct it in `AppContainer.__init__` (mind ordering: services take other services
  in their constructors).
* New persisted directory: add a property and include it in `DataPaths.ensure()`.

## Limitations

* The container is a process-wide singleton; there is no per-request scoping.
* `TaskManager` is a thread pool (4 workers) with no persistence, priorities or cancellation of
  running futures; cancellation is cooperative through `ReviewService.request_cancel`.
* Settings overrides in `data/settings.json` win over `.env` for editable fields.
* `SnapshotService.save_now` / `restore` loads full tables; there is no incremental persistence.
