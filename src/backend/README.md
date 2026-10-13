# backend

The FastAPI application, the LangGraph review workflow and everything underneath them. The Streamlit
frontend (`frontend/`) talks to this package only through the REST API under `/api/v1`.

## Purpose and responsibility

* Accept architecture documents, parse them and build a typed architecture model.
* Run deterministic checks and six specialist critics (security, resilience, finops, compliance,
  performance, operability) plus a moderator, orchestrated as a LangGraph graph.
* Hold the human-in-the-loop boundary (accept / dispute / defer, rebuttals, revisions, re-review).
* Persist state (in-memory SQLite + JSON snapshots + a file-backed LangGraph checkpoint) and produce
  JSON / PDF reports and ADR drafts.

## Layout

| Folder | Role |
| --- | --- |
| `main.py` | `create_app()` factory, exception handlers, router registration; module-level `app = create_app()`. |
| `api/` | HTTP layer: `deps.py` and `routes/*`. See `api/README.md`. |
| `core/` | Settings, paths, logging, exceptions, task manager and the `AppContainer` composition root. |
| `models/` | Pydantic domain/architecture/API schemas and the in-memory SQLite persistence layer. |
| `repositories/` | Reserved; currently empty (see its README). |
| `services/` | Application services (ingestion, extraction, review state, decisions, scoring, reports, ...). |
| `parsers/` | File-type detection and parsers producing `ParsedDocument`. |
| `tools/` | Deterministic rule registry (38 rules), Kubernetes helpers, dependency graph, Checkov adapter. |
| `llm/` | Provider-neutral `LLMProvider`, concrete adapters and the offline `MockProvider`. |
| `agents/` | `BaseAgent`, six specialists, `ModeratorAgent`, rebuttal request. |
| `graph/` | LangGraph state, nodes, routing, builder, checkpointer and `GraphExecutor`. |
| `reporting/` | JSON, PDF (ReportLab) and ADR Markdown generators. |

## Dependency direction

```
models, core(config/exceptions/paths/logging/tasks)      <- leaves
parsers -> tools -> llm -> services -> agents -> graph -> core.lifecycle (AppContainer) -> api -> main
```

`core/lifecycle.py` is the composition root and therefore imports from services, agents and llm even
though it lives in `core/`. LangGraph nodes reach services through `get_container()` so that graph
state stays JSON-serialisable (no clients or sessions inside checkpoints).

## How the backend participates in execution

1. `python src/run.py` starts `python -m uvicorn backend.main:app` and `streamlit run frontend/app.py`
   as child processes (logs in `data/logs/`).
2. Uvicorn imports `backend.main`; the lifespan hook builds `AppContainer(start_logging=True)`,
   calls `startup()` (restore `data/snapshots/state.json`, `GraphExecutor.recover_on_startup()`,
   enable debounced autosave) and registers it with `set_container()`.
3. A request such as `POST /api/v1/reviews/{id}/start` goes
   `routes/reviews.py` -> `WorkflowService.start` -> `GraphExecutor.start` -> `TaskManager.submit`
   (worker thread) -> `GraphExecutor.run` -> LangGraph `invoke` -> nodes -> services/agents/tools.
4. The graph pauses at `human_review` (`interrupt()`); the review becomes `AWAITING_HUMAN`.
   Decisions, revisions and `resume` / `re-review` calls continue the same thread.
5. `finalize` writes ADR files and JSON/PDF reports under `data/reports/<review_id>/`.

## Configuration

`core/config.py::Settings` (pydantic-settings; `.env` and process environment, case-insensitive).
Fields editable at runtime from the Settings page are listed in `EDITABLE_FIELDS` and persisted to
`data/settings.json`; API keys are accepted by `PUT /settings` but live in process memory only.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `mock` | `mock`, `anthropic`, `openai`, `bedrock` |
| `DEMO_MODE` | `true` | Default review mode (demo reviews always use `MockProvider`) |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | - / `claude-sonnet-5-5` | Anthropic adapter |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | - / `gpt-4o` | OpenAI adapter |
| `AWS_REGION` / `AWS_PROFILE` / `BEDROCK_MODEL_ID` | `us-east-1` / - / Claude 3.5 Sonnet v2 id | Bedrock adapter |
| `AWS_BEARER_TOKEN_BEDROCK` | - | Bedrock API key; used instead of the credential chain when set |
| `MAX_CONCURRENT_AGENTS` | 6 | Parallel critics and provider-call semaphore |
| `MAX_REVIEW_ROUNDS` | 3 | Upper bound of review rounds (1-10) |
| `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `MAX_FILES_PER_PROJECT` | 25, 200, 60 | Ingestion limits |
| `ENABLE_CHECKOV`, `CHECKOV_TIMEOUT_SECONDS` | true, 120 | Optional scanner |
| `LOCAL_DATA_DIR` | `data` | Durable data root (relative to the repository root) |
| `MOCK_LATENCY_SECONDS` | 1.0 | Artificial delay in `MockProvider` (tests set 0) |
| `SNAPSHOT_DEBOUNCE_SECONDS` | 0.4 | Delay before a committed write is snapshotted |

## Extension points

* New endpoint: `api/routes/` (see `api/routes/README.md`).
* New rule, parser, agent or provider: see the READMEs of `tools/`, `parsers/`, `agents/`, `llm/`.
* New service: add a class under `services/`, construct it in `AppContainer.__init__`, expose it on
  the container, and inject it into routes via the `Container` dependency.

## Limitations

* Single process, single user, no authentication; binds to loopback by default.
* Background work runs on an in-process thread pool (`TaskManager`, 4 workers); nothing is queued
  durably and tasks are not restarted automatically after a crash.
* The primary database is volatile in-memory SQLite; durability comes from debounced JSON snapshots
  (a crash inside the debounce window loses the latest writes).
* `max_input_tokens` bounds both the LLM-assisted extraction prompt and each specialist's evidence catalog
  (about 1.8 characters per token, minimum 8000 characters); there is no tokenizer, so this is an approximation.
