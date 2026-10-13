# Persistence, restart behaviour and recovery

## Principle
The **primary application database is SQLite in memory** (`create_engine("sqlite://", poolclass=StaticPool)`).
Important user work must nevertheless survive a restart, so durable copies are kept as files. LangGraph checkpoints
use a *separate* file-backed SQLite database.

## What is stored where
| Data | Location | Written | Restored by |
| --- | --- | --- | --- |
| Uploaded files | `data/uploads/<project>/<artifact>/<safe name>` | at upload (atomic `.part` + `os.replace`) | files are referenced by DB rows |
| Parsed documents | `data/uploads/<project>/<artifact>/parsed.json` | at upload | read on demand |
| Application entities and events | `data/snapshots/state.json` (+ `.json.bak`) | debounced after every committed write; flushed on shutdown | `SnapshotService.restore()` at startup |
| Stage snapshots | `data/snapshots/<review>/<ts>-<label>.json` | after moderation, rebuttals, revisions and finalization | audit only |
| Reports | `data/reports/<review>/architecture-review-*.{pdf,json}` | on finalize or `POST /reports` | listed via `reports` rows |
| ADR drafts | `data/reports/<review>/adrs/ADR-nnn-*.md` | when drafted | `adrs` rows reference paths |
| LangGraph checkpoints | `data/checkpoints/langgraph.sqlite` | every graph super-step | `SqliteSaver` |
| Settings overrides | `data/settings.json` | on `PUT /settings` (never secrets) | `SettingsStore` |

Snapshot writes are atomic: the JSON is written to a temporary file, the previous snapshot is copied to `.bak`, and
`os.replace` swaps the new file in. `restore()` falls back to `.bak` when the main file is corrupt and checks
`schema_version`.

## Restart scenarios
| State at shutdown | After restart |
| --- | --- |
| `AWAITING_HUMAN` with checkpoint | exact continuation: `POST /resume` resumes the same LangGraph thread; pending decisions are preserved |
| `AWAITING_HUMAN`, checkpoint missing/corrupt | a **new** thread is seeded at the human-review node from persisted findings and decisions; the review carries a `recovery_note` and a `checkpoint_reconstructed` event. Exact graph continuation is *not* claimed |
| mid-run (`EXTRACTING`..`RE_REVIEWING`) | marked `FAILED` ("backend restarted..."); `POST /start` continues from the last checkpoint if one exists, otherwise restarts from the beginning. Nothing is re-run automatically (no surprise LLM calls) |
| `COMPLETED*` | read-only; reports remain downloadable |

Tests: `tests/workflow/test_review_workflow.py::test_checkpoint_survives_restart_and_review_resumes`,
`::test_missing_checkpoint_is_reconstructed_and_reported_honestly`, `::test_review_interrupted_mid_run_is_marked_failed_with_guidance`.

## In-process background execution (limitations)
Reviews run on a `ThreadPoolExecutor` inside the backend process (`src/backend/core/tasks.py`): no external queue. Consequences:
* stopping the backend loses in-flight work (checkpoints allow continuing; the task is not restarted automatically);
* one active task per review (duplicate starts return 409);
* cancellation is cooperative - it takes effect at the next node boundary or before the next LLM call.

## Concurrency
`Database.session()` serialises transactions with a re-entrant lock on the single shared connection, so FastAPI
threads and graph worker threads always see the same in-memory database and never interleave transactions.
Each `Database` instance is isolated (tests create one per test).

## Data handling
Everything is local. The only outbound traffic is the selected LLM provider in live mode (relevant document
content is sent to it). No telemetry, vector databases or external storage are used.
