# backend/models

Domain vocabulary, the structured architecture model, API contracts and the persistence layer.

## Purpose and responsibility

* `domain.py`, `architecture.py`, `schemas.py`: Pydantic v2 models shared by services, agents, the
  graph, snapshots and the API.
* `database.py`: the primary application database (in-memory SQLite via SQLAlchemy) and a typed
  document-table layer (`EntityTable`, `Tables`). This is the persistence layer of the application.

## Modules

| File | Contents |
| --- | --- |
| `domain.py` | Enums `Severity`, `EvidenceLevel`, `FindingStatus`, `ModeratorStatus`, `ReviewStatus`, `AgentStatus`, `AgentId`, `DocumentRole`, `DecisionType`, `PrincipleOrigin`, `Provenance`; entities `Project`, `ArchitectureArtifact`, `ToolResult`, `FindingDraft`, `AgentReviewOutput`, `Finding`, `Review`, `AgentExecution`, `ExecutionEvent`, `HumanDecision`, `Rebuttal`, `ArchitectureDecisionRecord`, `ReportArtifact`, `ArchitectureRevision`, `ChangeImpact`, `Scorecard`, `DomainScore`; helpers `new_id(prefix)`, `utcnow()`, `ALL_AGENT_IDS`, `AGENT_LABELS`, `CLOSED_STATUSES`, `TERMINAL_REVIEW_STATUSES`, `RUNNING_REVIEW_STATUSES` |
| `architecture.py` | `ArchitectureModel`, `ArchitectureComponent`, `ArchitectureRelationship`, `DataFlow`, `NonFunctionalRequirement`, `ArchitectureConstraint`, `ArchitectureAssumption`, `ExtractionNote`; `COMPONENT_TYPES`, `slugify()` |
| `schemas.py` | Requests (`ProjectCreate`, `ReviewCreate`, `DecisionCreate`, `ResumeRequest`, `ReReviewRequest`, `ProviderValidateRequest`, `SettingsUpdate`) and responses (`UploadResponse`, `ReviewStatusView`, `AgentStatusView`, `FindingView`, `HistoryView`, `AgentExecutionsView`, `AssessmentReport`, `ReportFinding`) |
| `database.py` | `Database`, `EntityTable[M]`, `Tables`, the row classes (`ProjectRow`, `FindingRow`, ... `EventRow`), `_StoredModel`, `_StoredToolResult` |

## How persistence works (`database.py`)

* `Database` creates `sqlite://` with `StaticPool` and `check_same_thread=False`, so every thread
  shares one in-memory connection. One `threading.RLock` serialises all transactions.
* `Database.session(write=True)` is a re-entrant context manager: it opens a `Session`
  (`expire_on_commit=False`), commits on success, rolls back on error and, when the outermost
  write session closes, calls the registered `on_commit` hooks (this is how the debounced snapshot
  is triggered). Read-only sessions (`write=False`) never fire hooks.
* Each entity is one row: `id` (primary key), a few indexed key columns and the full Pydantic
  payload in a JSON column `data`.
* `EntityTable.put(model)` upserts (looks up by id, sets all columns); `put_many` does the same in
  one transaction; `get(id)`, `list(**filters)` (equality on key columns), `delete`. Reads re-validate the payload into the model class.
* `Tables` exposes `projects`, `artifacts`, `reviews`, `revisions`, `models`, `executions`,
  `findings`, `tool_results`, `decisions`, `rebuttals`, `adrs`, `reports`, plus event helpers
  `add_event` (autoincrement `seq`), `list_events(review_id, after, limit)`, `dump_events`,
  `restore_events`, and `entity_tables()` (name -> table, used by `SnapshotService`).
* `Database.reset()` drops and recreates all tables (used when a snapshot candidate is unusable).

## Inputs and outputs

* Inputs: Pydantic model instances from services and nodes.
* Outputs: validated model instances; snapshot payloads (`SnapshotService`) are produced by
  dumping every table with `model_dump(mode="json")`.

## Dependencies

`pydantic`, `sqlalchemy`. `database.py` imports `domain` and `architecture`; `schemas.py` imports
`domain`. Nothing in `models/` imports services, agents or the graph.

## Role in application execution

The container creates one `Database` and one `Tables`. Services read and write through `Tables`;
the graph never touches it directly (nodes go through `ReviewService`, `DecisionService`, ...).
On startup the tables are populated from `data/snapshots/state.json`. Nothing in the database
survives a process exit unless it was snapshotted.

## Extension points

* New entity: add the Pydantic model in `domain.py`, a row class in `database.py`, an
  `EntityTable` in `Tables.__init__`, and an entry in `Tables.entity_tables()` so it is snapshotted.
  Bump `snapshot_service.SCHEMA_VERSION` only if old snapshots become unreadable.
* New domain enum value: add it in `domain.py`; check `CLOSED_STATUSES`, scoring rubric
  (`knowledge/agent_rubrics/scoring.yaml::status_treatment`) and report text.
* New agent id: extend `AgentId` and `AGENT_LABELS` (`ALL_AGENT_IDS` is derived).

## Limitations

* Everything is serialised behind one lock; throughput is bounded by a single connection.
* `EntityTable.list` loads and validates every matching row (no pagination or projection).
* Only equality filters on declared key columns are supported.
* Snapshot restore uses `put_many` and requires models to remain backward compatible with stored JSON.
* `_StoredModel` and `_StoredToolResult` are module-private but imported by `ReviewService`.
