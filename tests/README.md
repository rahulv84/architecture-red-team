# tests

pytest suite for the backend (98 tests collected by default, 1 more deselected: the `live` smoke
test). Everything runs offline: the in-memory database, a temporary data directory and the
`MockProvider` with zero latency. No network access or API keys are needed.

## Running

```powershell
.venv\Scripts\python -m pytest                      # default: -q -m "not live"
.venv\Scripts\python -m pytest tests\unit           # one layer
.venv\Scripts\python -m pytest tests\workflow -k interrupt
.venv\Scripts\python -m pytest -m live              # opt in to tests that call a real provider
```

Configuration is in `pyproject.toml` (`[tool.pytest.ini_options]`): `testpaths = ["tests"]`,
`addopts = "-q -m 'not live'"`, marker `live` ("call a real LLM provider; require API keys;
deselected by default"). Development dependencies: `pip install pytest ruff` (the `dev` extra in `pyproject.toml`).

## Layout

```
tests/
  conftest.py                       fixtures and helpers shared by all layers
  unit/
    test_ingestion_and_parsers.py
    test_extraction_and_tools.py
    test_llm_and_agents.py
    test_bedrock_provider.py
    test_demo_reset.py
    test_package_submission.py
    test_database_scoring_reports.py
  workflow/
    test_review_workflow.py
  integration/
    test_api_and_scenarios.py
```

### `conftest.py`

| Name | Purpose |
| --- | --- |
| `make_container(tmp_path, **overrides)` | Builds an `AppContainer` with `Settings(local_data_dir=<tmp>/data, mock_latency_seconds=0)`, `persist_settings=False`, `autosave=False`, registers it with `set_container` and calls `startup()` |
| `container` fixture | A container for one test; tears down the task pool and database |
| `client` fixture | `(TestClient, AppContainer)` using `create_app(container)` |
| `start_demo_review(c, scenario, agents, max_rounds)` | Creates a demo project from `src/demo/fixtures`, a review, starts it and waits until it reaches the human-review interrupt |
| `wait_idle(c, review_id, timeout)` | Waits for the review's background task |
| `finding_by_title(c, review_id, fragment)` | Finds a finding by title substring |

Each test gets its own data directory under pytest's `tmp_path`; nothing touches the repository's
`data/` folder.

## What each module covers

| Module | Covers |
| --- | --- |
| `unit/test_ingestion_and_parsers.py` | `safe_filename`, `detect_type` and signature mismatch, ingestion hash/role/idempotency, size and empty-file rejection, corrupt file stored as `FAILED`, encrypted PDF, markdown section line ranges, binary text rejection, PDF text/pages and no-text-layer flag, DOCX headings/tables, PPTX slides/notes, zip-bomb guard, raster image not interpreted, Draw.io plain and compressed, XML entity bomb, Mermaid, PlantUML, SVG labels, Terraform lines, CloudFormation short-form tags and Kubernetes multi-document, YAML alias attack, OpenAPI/JSON |
| `unit/test_extraction_and_tools.py` | Architecture model from the three fixtures (components, relationships, NFRs, source references, K8s selectors, regions, unresolved items); every registered rule is assigned to an agent; expected rules fire on fixtures; hardened revisions resolve rules; unresolved variables not flagged; open web ports vs SSH; keyword absence never creates a finding; deterministic dependency graph; Checkov adapter reports `NOT_EXECUTED` when missing; tool-result schema fields |
| `unit/test_llm_and_agents.py` | Provider factory and catalog, configuration validation, secrets never exposed or persisted, `extract_json`, retry/backoff and schema-repair loop, bounded retries; each specialist's structured output, distinct tool/scope/brief per agent, FinOps brief has no prices, compliance caveat, principle origin derived not trusted, prompt-injection text stays inside the untrusted block; moderator merge/provenance, no merge of distinct rules, rejection and validation, carry-over and resolution on re-run, failed agent does not resolve, adjudication outcomes |
| `unit/test_bedrock_provider.py` | Bedrock adapter: retry without `toolChoice`/`temperature` for models that reject them (learned per model), API-key (bearer) Converse REST calls, image base64 encoding, HTTP error mapping, region validation, key never exposed |
| `unit/test_gemini_provider.py` | Gemini adapter (faked SDK client): mid-tier default and top-tier override, JSON mode + system instruction, thought parts excluded and thinking tokens counted, repair attempt, `MAX_TOKENS` truncation and safety blocks fail, error mapping (invalid key, 403, 429, 503, timeouts), retry, inline image bytes, client timeout with SDK retries disabled, key never exposed |
| `unit/test_demo_reset.py` | `src/scripts/demo_reset.py`: clean-up keeps `.gitkeep`/README, `--keep-settings`, refuses unsafe data dirs, fixture parse/stray-file checks, settings-override and mock-provider warnings (temporary directories only) |
| `unit/test_package_submission.py` | `src/scripts/package_submission.py`: file selection excludes `.env`/`data/`/caches, values from the real `.env` and real key formats block packaging (never logged), test placeholders and fake demo passwords do not, folder + zip layout, name convention |
| `unit/test_database_scoring_reports.py` | `Database` isolation, shared serialised session across threads, monotonic event sequence, snapshot round trip and corrupt-snapshot fallback to `.bak`, scorecard separation of confirmed vs unverified and rounding, JSON report validation and readable PDF |
| `workflow/test_review_workflow.py` | Six agents in parallel and aggregated; real interrupt and `Command` resume; decision validation and status gating; accept/dispute/defer with rebuttal and ADR; critic concession; targeted re-review and finding history; unknown impact broadens instead of skipping; max rounds ends `COMPLETED_WITH_OPEN_RISKS`; one failing specialist keeps others' results; all failing -> `FAILED`; live mode without credentials fails and never falls back to mock; cancel; checkpoint survives restart; missing checkpoint reconstructed and reported; interrupted run marked `FAILED` with guidance |
| `integration/test_api_and_scenarios.py` | Health/config/providers; settings never return secrets; upload and review error paths; full demo flow through the HTTP API; each scenario runs offline with all six agents (minimum findings 20 / 18 / 18) and the hardened revision resolves findings; `live` smoke tests (skipped without `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`) |

## Conventions

* Prefer the `container` fixture for service-level tests and `client` for HTTP behaviour.
* Use `start_demo_review` + `wait_idle` rather than sleeping.
* Fixtures for parsers and tools are loaded from `src/demo/fixtures` (`load_docs` in
  `test_extraction_and_tools.py`); parser edge cases build bytes inline.
* Workflow tests that need a restart construct a second container over the same `tmp_path`.

## Inputs, outputs, dependencies

Inputs: `src/demo/` fixtures, `src/knowledge/`, the backend package. Outputs: pytest results only
(`.pytest_cache/`, temporary directories). The tests import from `backend.*` and from
`tests.conftest`; `conftest.py` inserts the repository root into `sys.path`.

## Role in application execution

Not part of runtime. The suite exercises the same path as production: container -> services ->
LangGraph graph with a SQLite checkpoint -> nodes -> agents/tools, with only the LLM provider
replaced by `MockProvider`.

## Extension points

* New parser test: add to `unit/test_ingestion_and_parsers.py`.
* New rule: add to `unit/test_extraction_and_tools.py` (build resources with `IacResource`/fixtures).
* New workflow behaviour: add to `workflow/test_review_workflow.py` using `start_demo_review`.
* New scenario: extend the `parametrize` list in `integration/test_api_and_scenarios.py`.
* Live-provider tests: mark with `@pytest.mark.live` so they stay deselected by default.

## Limitations

* No frontend (Streamlit) tests.
* Live providers (Anthropic, OpenAI, Gemini, Bedrock) are covered only by configuration/translation logic
  and a skipped smoke test; request/response behaviour against real services is untested here.
* Timing-sensitive workflow tests wait up to 90 s for background tasks; on a heavily loaded machine
  they may need a larger timeout.
* Checkov is not installed by default, so only its `NOT_EXECUTED` path is tested.
