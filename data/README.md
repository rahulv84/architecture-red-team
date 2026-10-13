# data

Runtime state of the application. Everything the backend writes lives here (the location is
`LOCAL_DATA_DIR`, default `data`, resolved relative to the repository root). Contents are ignored by
git except the `.gitkeep` placeholders; `src/run.py` and `AppContainer` create the subfolders on start.

## Layout

```
data/
  settings.json                  non-secret runtime settings overrides (written by PUT /settings)
  uploads/
    <project_id>/
      <artifact_id>/
        <safe_filename>          the uploaded file, byte-for-byte
        parsed.json              serialised ParsedDocument (written when parsing succeeded)
        images/                  images extracted from PDFs (page<N>_<idx>.<ext>, max 12)
  reports/
    <review_id>/
      architecture-review-<review_id>-rev<N>-<YYYYmmddTHHMMSS>.json
      architecture-review-<review_id>-rev<N>-<YYYYmmddTHHMMSS>.pdf
      adrs/ADR-<nnn>-<slug>.md   ADR drafts
  snapshots/
    state.json                   latest snapshot of the whole in-memory database
    state.json.bak               previous snapshot (fallback on restore)
    <review_id>/<UTC stamp>-<label>.json
                                 per-review audit snapshots (labels: moderated-r<N>,
                                 rebuttal-r<N>, revision-<N>, final)
  checkpoints/
    langgraph.sqlite             LangGraph checkpoint store (SQLite WAL; may show -wal / -shm files)
  logs/
    backend.log                  rotating application log (2 MB x 3 backups, secrets redacted)
    backend.out.log              uvicorn stdout/stderr when started by src/run.py
    streamlit.out.log            Streamlit stdout/stderr when started by src/run.py
```

Temporary `*.part` / `*.tmp` files appear while atomic writes are in flight and are replaced with
`os.replace`.

## Who writes what

| Path | Writer | Reader |
| --- | --- | --- |
| `uploads/...` | `IngestionService.add_document` | `IngestionService.parsed/raw_bytes`, `ReviewCache.docs`, vision pass |
| `reports/...` | `ReportService.generate`, `DecisionService.ensure_adr` | `GET /reports/{id}/download`, users |
| `snapshots/state.json` | `SnapshotService.save_now` (debounced, 0.4 s) | `SnapshotService.restore` at startup |
| `snapshots/<review_id>/` | `SnapshotService.save_review_snapshot` (stage boundaries) | audit only; never restored automatically |
| `checkpoints/langgraph.sqlite` | LangGraph `SqliteSaver` via `GraphExecutor` | `GraphExecutor` on resume/recovery |
| `settings.json` | `SettingsStore._save_overrides` (editable fields only) | `SettingsStore` at startup |
| `logs/` | `core.logging.setup_logging`, `src/run.py` | users |

## Persistence model

The primary application database is **in-memory SQLite** (`sqlite://`, `StaticPool`). It is not a
file. Durability is provided by `state.json`, a JSON dump of 12 entity tables plus the event log
(`schema_version`, `saved_at`, `tables`, `events`). Every committed write schedules a snapshot after
a short debounce; `shutdown()` flushes a final one. At startup `restore()` loads `state.json`, falls
back to `state.json.bak` on error and otherwise starts empty.

| Survives a restart | Does not survive a restart |
| --- | --- |
| Projects, documents (rows from `state.json`, bytes from `uploads/`) | The in-memory database itself (rebuilt from the snapshot) |
| Reviews, findings, decisions, rebuttals, ADR records, architecture models, tool results, events, report records | API keys entered through the Settings page (memory only; use `.env`) |
| Report and ADR files | Background tasks and cancel flags (`TaskManager`, `ReviewService._cancel`) |
| LangGraph checkpoints (`checkpoints/langgraph.sqlite`) | `ReviewCache` (parsed documents, TF-IDF index, Checkov results; rebuilt on demand) |
| Settings overrides (`settings.json`) | `ContextService` and `MockProvider` caches |

Restart semantics (`GraphExecutor.recover_on_startup`):

* A review that was `EXTRACTING`/`ANALYZING`/`MODERATING`/`REBUTTING`/`RE_REVIEWING` becomes
  `FAILED` with an explanatory error; nothing is restarted automatically. Pressing Start continues
  from the last checkpoint when one exists (`invoke(None)`), otherwise runs from the beginning.
* A review that was `AWAITING_HUMAN` stays so. If its checkpoint is intact, resume continues the
  graph; otherwise `recovery_note` is set and the thread is re-seeded at the human-review step from
  persisted findings and decisions when you resume.

## Inputs, outputs, dependencies

Written and read only by the backend (`core/paths.py::DataPaths` is the single source of path
construction; user-supplied names are reduced with `safe_filename` and never used as paths). The
Streamlit frontend does not read this directory; it downloads reports through the API.

## Role in application execution

`src/run.py` creates `uploads`, `reports`, `snapshots`, `checkpoints`, `logs` and points both
processes at the same root. `AppContainer.__init__` builds `DataPaths(settings.data_dir)` and calls
`ensure()`.

## Extension points

* New durable artefact type: add a property to `DataPaths`, include it in `ensure()`, and (if it
  must be listed in the UI) record it in a table in `models/database.py`.
* Alternative location: set `LOCAL_DATA_DIR` (absolute or repository-relative).

## Limitations

* No cleanup or retention: uploads, reports, snapshots and checkpoint threads accumulate until you
  delete them. To reset the application, stop it and remove the contents of the subfolders
  (keep `.gitkeep`).
* A crash can lose writes made in the last debounce window (about 0.4 s) because the snapshot is
  written after the commit.
* If `state.json` and `state.json.bak` are both unusable the database starts empty and the files in
  `uploads/` and `reports/` become orphans (they are not re-imported).
* Uploaded documents and reports are stored unencrypted on local disk.
* A single snapshot file holds all projects; very large histories make saves slower.
