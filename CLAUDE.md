# CLAUDE.md - repository guide for AI coding sessions

## What this project is
Architecture Red Team: a local proof of concept that performs adversarial architecture reviews. Six specialist critics
(security, resilience, finops, compliance, performance, operability) run in parallel through a LangGraph workflow,
a moderator validates/de-duplicates, a human architect accepts/disputes/defers, critics rebut, revisions trigger
targeted re-reviews, and the app produces a scorecard, ADR drafts and PDF/JSON reports.
Read `README.md` (usage) and `docs/system-design.md` (design) first.

## Runtime commands (Windows paths shown; use `.venv/bin/python` elsewhere)
```bash
.venv\Scripts\python src/run.py                                     # start FastAPI (8000) + Streamlit (8501), open the browser
.venv\Scripts\python src/run.py --no-browser
.venv\Scripts\python -m uvicorn --app-dir src backend.main:app --port 8000   # backend only
.venv\Scripts\python -m pytest                                      # full suite (offline, ~1 min)
.venv\Scripts\python -m pytest tests/workflow -k rereview           # subset
.venv\Scripts\python -m ruff check src tests                        # lint (must be clean)
.venv\Scripts\python src/scripts/demo_reset.py                      # wipe data/ + pre-demo checks (app must be stopped)
.venv\Scripts\python src/scripts/package_submission.py              # build the Agentathon upload folder (dist/)
```
Install: `pip install -r requirements.txt` (a `.venv` in the repo root is the expected interpreter).

## Layout and conventions
| Path | Responsibility |
| --- | --- |
| `src/run.py` | launcher only - no business logic |
| `Agent.md` | AI By Design Agentathon submission form (problem, capabilities, value, security) |
| `src/scripts/` | operator tooling (`demo_reset.py`: clean `data/` + pre-demo checks; `package_submission.py`: build the upload folder in `dist/`); reuses backend modules, no business logic |
| `src/backend/main.py` | FastAPI app factory; lifespan builds the `AppContainer` |
| `src/backend/api/routes/*` | thin routers; validation + delegation; no business logic |
| `src/backend/core/` | settings (`config.py`), container + `get_container()` (`lifecycle.py`), exceptions, paths, logging, task pool |
| `src/backend/models/` | `domain.py` (all domain Pydantic models/enums), `architecture.py` (architecture model), `schemas.py` (API + report models), `database.py` (in-memory SQLite + `EntityTable`) |
| `src/backend/services/` | business logic classes (ingestion, architecture extraction, context retrieval, review, decisions, revisions, scoring, reports, snapshots, evaluation, demo, workflow) |
| `src/backend/parsers/` | one parser per format family; `parser_registry.py` dispatches and validates signatures |
| `src/backend/tools/` | deterministic rules registered with `@register`; `AGENT_RULES` in `tools/__init__.py` assigns rules to agents |
| `src/backend/llm/` | provider interface, adapters, `MockProvider` (fixture-driven) |
| `src/backend/agents/` | `BaseAgent` + six specialists, `ModeratorAgent`, `rebuttal.py` |
| `src/backend/graph/` | LangGraph state, nodes, builder, routing, checkpointer, executor |
| `src/backend/reporting/` | ADR markdown, JSON + PDF report writers |
| `src/frontend/` | Streamlit pages; **only** talks to the REST API |
| `src/knowledge/` | standards, control mappings, agent rubrics (YAML/Markdown, versioned content) |
| `src/demo/` | scenario fixtures, seeded findings, expected results |
| `tests/` | `unit/`, `workflow/`, `integration/`, shared fixtures in `conftest.py` |

Conventions:
* Python 3.11, type hints everywhere, Pydantic v2 for every contract. Keep modules small and cohesive.
* Graph state must stay JSON-serialisable (dicts/lists/str). Never put clients, sessions or open files in it.
* Graph nodes reach services through `get_container()`; routes through `Depends(container_dep)`.
* Errors: raise `AppError` subclasses (`src/backend/core/exceptions.py`); never a bare `except:`; log with `logging`, never `print`.
* Never place secrets in logs, reports, snapshots or API responses (`Settings.public_view()` exists for a reason).
* Do not build file paths from user-supplied names (`safe_filename` + generated IDs only).
* Findings must cite evidence IDs supplied in the catalog; never let a provider invent source references.
* Match existing style; run `ruff` before finishing.

## Major services (AppContainer attributes)
`ingestion`, `architecture`, `context`, `reviews`, `decisions`, `revisions`, `scoring`, `reports`, `snapshots`, `evaluation`,
`demo`, `workflow`, `executor` (graph), `tasks`, `review_cache`, plus `tables`/`db`.

## Agent extension pattern
1. Add `src/knowledge/agent_rubrics/<agent>.yaml` (persona, mission, focus_areas, evidence_expectations, severity_guidance, anti_patterns).
2. Add `src/backend/agents/<agent>_agent.py` subclassing `BaseAgent`: set `agent_id`, `display_name`, `relevant_types`, `query_keywords`
   and implement `domain_brief()` (deterministic digest of domain facts).
3. Register it in `src/backend/agents/__init__.py::AGENT_CLASSES`, add `AGENT_NODES` handling (`ALL_AGENT_IDS` in `models/domain.py`,
   labels in `AGENT_LABELS`) and its rules in `src/backend/tools/__init__.py::AGENT_RULES`.
4. Add rules with `@register(rule_id, tool_id, tool_name, domain, title)` in a module under `src/backend/tools/` and import it in `tools/__init__.py`.
5. Extend `PROPERTY_DOMAINS` in `services/revision_service.py` if the agent should be targeted by changes.
6. Add fixture flaws for the agent in `src/demo/seeded_findings/*.json`, update tests and docs.

## Other extension points
* **Parser**: add `src/backend/parsers/<x>.py`, an entry in `EXTENSION_TYPES` + dispatch in `parser_registry.parse_bytes`, tests with generated bytes.
* **Provider**: subclass `LLMProvider` (implement `_complete`, optional `_complete_image`, `capabilities`, `validate_configuration`) and register in `provider_factory._PROVIDERS` and `SUPPORTED_PROVIDERS`.
* **Scenario**: folder `src/demo/fixtures/<id>/` with `scenario.json` (+ `revision-1/`), `src/demo/seeded_findings/<id>.json`, `src/demo/expected_results/<id>.json`; add it to the parametrised e2e test.
* **API route**: new module in `src/backend/api/routes/`, include it in `src/backend/main.py`, document in `docs/api-reference.md`.

## Testing commands and expectations
`python -m pytest` must pass without API keys. Add tests for every behavioural change: unit tests for pure logic,
`tests/workflow` for graph behaviour (use `tests/conftest.py::start_demo_review`), `tests/integration` for API paths.
Use `mock_latency_seconds=0` containers (see `make_container`). Live-provider tests are marked `@pytest.mark.live`.

## Documentation updates required when you change...
* any endpoint -> `docs/api-reference.md`
* graph nodes/state/routing -> `docs/system-design.md`, `docs/agent-workflows.md`, `src/backend/graph/README.md`
* models/persistence -> `docs/data-model.md`, `docs/persistence.md`
* tools/rules -> `src/backend/tools/README.md`, rule table in `docs/agent-workflows.md`
* demo fixtures -> `src/demo/README.md`, `docs/demo-guide.md`
* user-visible behaviour/limits -> `README.md`, `docs/limitations.md`
Every first-party folder keeps a meaningful `README.md`.

## Guardrails
* **No external infrastructure**: no Docker, Kubernetes, Redis, PostgreSQL servers, brokers, Celery, vector databases or cloud services. SQLite (in-memory app DB, file checkpoints), the filesystem and the in-process task pool are the only stores/queues.
* The primary application database stays in memory; durable state goes to snapshots/files.
* Do not silently fall back from a failing live provider to the mock provider.
* Do not auto-approve architectures; the AI never grants final approval.
* Never execute uploaded Terraform/scripts/diagram code. Parse only.
* Keep the service loopback-only and unauthenticated; document that it is not for public exposure.
* Do not fabricate cloud prices or measurements; use qualitative language and list missing inputs.
