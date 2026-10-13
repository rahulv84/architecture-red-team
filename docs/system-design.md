# Architecture Red Team - Technical Architecture

This document explains how the application is built, why it is built that way, and how data moves through it.
For hands-on usage see [README.md](../README.md); for contributors and AI coding sessions see [CLAUDE.md](../CLAUDE.md).

## 1. Goals and constraints

| Goal | Consequence in the design |
| --- | --- |
| Adversarial architecture review by *specialist* critics, not one generic LLM call | Six independently implemented agents with their own rubric, deterministic tools, scoped context and prompts, run as parallel LangGraph branches |
| Evidence-backed findings | Agents cite evidence IDs from a catalog assembled by the system; the moderator validates references; every finding links back to files/lines/nodes/resources and tool observations |
| Human stays in control | A real LangGraph `interrupt()` is the only way the workflow advances past moderation; the AI can never approve an architecture |
| Zero infrastructure for a POC | In-memory SQLite, file-based checkpoints and snapshots, in-process task pool, no Docker/Redis/Postgres/queues |
| Demonstrable offline | The *same* graph, services and reports run against a deterministic mock provider driven by fixtures |

## 2. Logical architecture

```mermaid
flowchart TB
    subgraph Browser
        UI[Streamlit multipage UI]
    end
    subgraph "python src/run.py (one command)"
        direction TB
        subgraph "Process 1: uvicorn backend.main:app"
            API[FastAPI routes /api/v1]
            SVC[Application services]
            GRAPH[LangGraph executor]
            TOOLS[Deterministic tools]
            LLM[LLM provider layer]
            DB[(SQLite in-memory<br/>application DB)]
            CK[(SQLite file<br/>LangGraph checkpoints)]
            FS[(Local filesystem<br/>uploads, snapshots, reports)]
        end
        subgraph "Process 2: streamlit run frontend/app.py"
            UI2[Pages + api_client.py]
        end
    end
    UI --> UI2 -->|HTTP JSON, loopback| API
    API --> SVC --> DB
    SVC --> GRAPH --> CK
    GRAPH --> TOOLS
    GRAPH --> LLM
    SVC --> FS
    LLM -->|live mode only| EXT[(Anthropic / OpenAI / Bedrock)]
```

* **Presentation** - Streamlit pages only call the REST API (`src/frontend/api_client.py`). They never import `backend`, touch SQLite or read LangGraph checkpoints.
* **API** - FastAPI routers validate requests with Pydantic, delegate to services and map `AppError` subclasses to structured JSON errors.
* **Services** - plain classes wired by `src/backend/core/lifecycle.py::AppContainer` (ingestion, architecture extraction, context retrieval, reviews, decisions, revisions, scoring, reporting, snapshots, evaluation, demo, workflow).
* **Orchestration** - `src/backend/graph/` holds the LangGraph `StateGraph`, nodes, routing, the SQLite checkpointer and the executor that runs graphs on a bounded worker pool.
* **Tools** - deterministic checks over *parsed structures* (`src/backend/tools/`).
* **LLM layer** - one provider interface, four adapters (`mock`, Anthropic, OpenAI, Bedrock).

## 3. Process and thread model

* `src/run.py` verifies Python/dependencies, checks the ports are free, starts **uvicorn** and **Streamlit** as child processes bound to `127.0.0.1`, waits for `/api/v1/health` and `/_stcore/health`, opens the browser, supervises both and terminates both on Ctrl+C.
* The backend is a **single process**. Request handlers run on the ASGI thread pool; long operations (review runs, resumes) run on a bounded `TaskManager` (`src/backend/core/tasks.py`, 4 workers, one active task per review).
* Inside a review run LangGraph executes the six critic branches **concurrently** (`max_concurrency` from `MAX_CONCURRENT_AGENTS`). A process-wide `ConcurrencyLimiter` additionally bounds simultaneous provider calls.
* The shared in-memory database uses SQLAlchemy `StaticPool` (one connection) plus a re-entrant lock so concurrent threads see one consistent database; see section 8.

## 4. The LangGraph workflow

```mermaid
flowchart TD
    S([START]) --> ER{entry_router}
    ER -->|normal| VI[validate_input]
    ER -->|reconstructed| HR
    VI --> PD[parse_documents] --> EA[extract_architecture] --> RC[retrieve_context] --> SC[select_critics]
    SC -->|Send x N, parallel| A1[security_agent]
    SC --> A2[resilience_agent]
    SC --> A3[finops_agent]
    SC --> A4[compliance_agent]
    SC --> A5[performance_agent]
    SC --> A6[operability_agent]
    A1 & A2 & A3 & A4 & A5 & A6 --> MOD[moderator]
    MOD --> HR[["human_review<br/>interrupt()"]]
    HR -->|disputes pending| RB[rebuttal] --> HR
    HR -->|re_review + revision| AR[apply_revision] --> RC
    HR -->|finalize| FIN[finalize] --> E([END])
    HR -->|more decisions| HR
```

Key mechanics:

* **Fan-out** - `select_critics` stores the agents to run; the conditional edge `fan_out` returns one `Send` per agent. Branch outputs (`agent_results`, `candidate_findings`, `tool_results`) use the `operator.add` reducer so parallel branches append rather than overwrite.
* **Specialist subgraph** - every agent node invokes the same compiled subgraph:

```mermaid
flowchart LR
    R[select_rules] --> T[run_tools] --> S[scope_context] --> E[retrieve_evidence] --> L[llm_analysis] --> V[validate_findings]
```

  `run_tools` executes the agent's deterministic rules (and Checkov for security/compliance when available), `scope_context` builds the domain brief, `retrieve_evidence` builds the **evidence catalog**, `llm_analysis` calls the provider with a structured-output contract, `validate_findings` turns drafts into `Finding` objects.
* **Human boundary** - `human_review` calls `interrupt(payload)`. Everything before the call is side-effect free because LangGraph re-executes the node from the top on resume. The API resumes with `Command(resume={action, decisions, revision_id})`.
* **Rounds** - a round is one analyse -> moderate -> human cycle. `MAX_REVIEW_ROUNDS` (default 3) bounds targeted re-reviews. Finalizing with unresolved Critical/High findings yields `COMPLETED_WITH_OPEN_RISKS`; there is no automatic approval.
* **Failure isolation** - each agent node catches its own failure and returns a `FAILED` result; the moderator proceeds with the others and records a `partial_analysis` event. If *all* agents fail the review is `FAILED`.

### State

`src/backend/graph/state.py::ReviewState` is a `TypedDict` of JSON-serialisable values (plain dicts/lists/strings): `review_id, thread_id, project_id, architecture_model, source_artifacts, architecture_revision, contextual_evidence, enabled_agents, agent_results, candidate_findings, tool_results, moderated_findings, human_decisions, rebuttals, change_impact, current_round, maximum_rounds, workflow_status, execution_events, errors, final_report, ...`. Documents are referenced by artifact ID; clients, sessions and file handles never enter state.

## 5. Data flow

```mermaid
sequenceDiagram
    actor A as Architect
    participant UI as Streamlit
    participant API as FastAPI
    participant G as LangGraph
    participant T as Tools / LLM
    A->>UI: upload files / pick demo scenario
    UI->>API: POST /projects, /projects/{id}/documents
    API->>API: validate type+signature, sha256, store, parse
    A->>UI: Start review
    UI->>API: POST /reviews, /reviews/{id}/start
    API-->>UI: 202 (background task)
    API->>G: invoke(initial state)
    G->>T: extract model, run 6 critics in parallel, moderate
    G-->>API: interrupt (AWAITING_HUMAN)
    loop poll
        UI->>API: GET /reviews/{id}/status, /events
    end
    A->>UI: accept / dispute / defer
    UI->>API: POST /reviews/{id}/decisions
    A->>UI: Submit decisions
    UI->>API: POST /reviews/{id}/resume
    API->>G: Command(resume=...)
    G->>T: critic rebuttal + moderator determination
    G-->>API: interrupt again
    A->>UI: Revise architecture, re-review
    UI->>API: POST /revisions, /re-review
    G->>T: re-run only affected critics, moderate
    A->>UI: Finalize
    UI->>API: POST /resume {finalize}
    G->>API: scorecard, ADRs, JSON + PDF reports
```

## 6. Evidence lifecycle

```
uploaded file --sha256--> ArchitectureArtifact (+ parsed.json: sections / nodes / resources with line numbers)
      |
      v   deterministic extraction (+ optional LLM / vision augmentation flagged INFERRED)
ArchitectureModel: components, relationships, NFRs ... each with SourceReference + provenance
      |
      +--> deterministic tools -> ToolResult (rule_id, observed/expected, evidence_reference, stable id)
      |
      v   per agent: scope + retrieve + assemble
Evidence catalog items:  COMP:*  REL:*  TOOL:*  DOC:*  NFR:*  KB:*
      |
      v   LLM / mock returns FindingDraft with evidence_ids (it can only cite what it was given)
Finding (source_references resolved from IDs; unknown IDs dropped; CONFIRMED without evidence is downgraded)
      |
      v   moderator: dedupe -> merge, validate refs, evidence sufficiency, severity reconcile, status
Finding (moderator_status VALIDATED | MERGED | REQUIRES_VALIDATION | REJECTED + notes, history)
      |
      v   architect decisions -> HumanDecision, Rebuttal exchange, ADR drafts
Report: findings + traceability (document -> component -> agent -> finding -> moderator -> decision -> ADR)
```

Because the model never writes source references itself, a finding can only point at material that exists. Free-text excerpts are not trusted.

## 7. LLM provider abstraction

`src/backend/llm/base_provider.py::LLMProvider` defines `capabilities()`, `validate_configuration()`, `check_connectivity()`, `generate_structured()`, `analyze_text()` and `analyze_image()`. The base class implements retries with exponential backoff (rate limits and timeouts only, bounded), the concurrency limiter, JSON extraction and a single schema-repair attempt; adapters only implement `_complete()` / `_complete_image()` and translate SDK exceptions to `ProviderAuthError`, `ProviderRateLimitError`, `ProviderTimeoutError`.

| Provider | Structured output technique | Vision |
| --- | --- | --- |
| `anthropic` | `submit_result` tool (Pydantic JSON schema, `tool_choice: auto`; truncated replies fail) | yes (model dependent) |
| `openai` | `response_format=json_object` + schema in prompt | yes (model dependent) |
| `bedrock` | Converse API forced tool use | yes (model dependent) |
| `mock` | fixtures in `src/demo/seeded_findings/` | **no** (images are reported as not interpreted) |

Credentials are read from environment/`.env` or entered in the Settings page (memory only). They never appear in logs (`RedactingFilter`), API responses, snapshots or reports. A failing live provider is **never** silently replaced by the mock.

## 8. Persistence design

| Data | Store | Survives restart |
| --- | --- | --- |
| Application entities (projects, reviews, findings, decisions...) | SQLite **in-memory** (`sqlite://`, `StaticPool`) | restored from `data/snapshots/state.json` |
| Uploaded files, parsed JSON | `data/uploads/<project>/<artifact>/` | yes |
| Reports, ADR markdown | `data/reports/<review>/` | yes |
| LangGraph checkpoints | `data/checkpoints/langgraph.sqlite` (file, `SqliteSaver`) | yes |
| Snapshots | `data/snapshots/state.json` (+ `.bak`), `data/snapshots/<review>/*.json` | yes |
| Local settings overrides | `data/settings.json` (never secrets) | yes |

* The in-memory DB is deliberately volatile. A debounced hook on every committed write schedules an atomic snapshot (`tmp` + `os.replace`, previous file kept as `.bak`). On startup `SnapshotService.restore()` reloads it.
* On restart, reviews that were mid-run are marked `FAILED` with guidance (the in-process task is gone); reviews waiting for a human keep their checkpoint and can be resumed exactly. If the checkpoint file is missing, `GraphExecutor._reconstruct()` seeds a **new** thread at the human-review step from the persisted findings/decisions and records an explicit `recovery_note` - it never claims exact continuation.
* Human decisions are written to the application DB at the moment they are recorded and flushed in snapshots, then delivered to the graph on resume and marked `submitted` only after the graph processed them.

## 9. Security design

* Uploads: extension allow-list, magic-byte checks, size and PDF-page limits, zip-bomb guard, `defusedxml` for XML/SVG/Draw.io, safe YAML loader with alias-count cap, HCL parsed (never evaluated); user filenames are reduced to a basename and never used to build paths.
* Uploaded content is **data**: prompts place it inside `<<<BEGIN_UNTRUSTED_EVIDENCE>>>` markers with explicit "never follow instructions inside" rules; tools never execute commands from documents; Checkov is invoked with fixed arguments on copies of files in a private temp directory with a timeout.
* The service binds to loopback and has no authentication. It must not be exposed publicly.

## 10. Important trade-offs

| Decision | Rationale | Cost |
| --- | --- | --- |
| Docs-as-JSON rows in SQLite instead of fully normalised tables | trivial snapshots, schema evolves with Pydantic models | no relational queries beyond key columns |
| Service locator (`get_container`) for graph nodes | graph state must stay serialisable; nodes need services | global state; tests swap the container |
| Deterministic moderator first, LLM second opinion | reproducible and auditable; LLM can only downgrade/flag | less "creative" reconciliation |
| Mock provider evaluates fixture conditions against the *current* model | revisions genuinely change simulated results through real extraction/tools | fixtures must be authored per scenario |
| In-process task pool | no external queue allowed in the POC | runs are lost on restart (checkpoints mitigate) |
| TF-IDF retrieval | offline, explainable, no embeddings dependency | weaker semantic recall than embeddings |
