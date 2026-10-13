# backend/services

Application services. Each service is constructed once by `AppContainer` and holds the business
logic that routes and LangGraph nodes call.

## Purpose and responsibility

Ingest documents, build the architecture model, manage review state and human decisions, compute
scores and revision impact, assemble reports and keep state durable.

## Modules and primary classes

| File | Class / functions | Responsibility |
| --- | --- | --- |
| `ingestion_service.py` | `IngestionService.add_document / add_documents / get / list_for_project / parsed / raw_bytes / content_view / manifest`, `UploadResult` | Validate size and type, SHA-256, store `uploads/<project>/<artifact>/<safe name>`, parse to `parsed.json`, supersede same-name files; re-uploading an identical file (same name and hash) returns the existing artifact |
| `architecture_service.py` | `ArchitectureService.build_model(...)`; helpers `guess_type`, `guess_provider`, `name_tokens`; `TF_TYPE_MAP`, `CFN_TYPE_MAP` | Deterministic extraction from IaC, diagrams and text; optional vision pass; LLM augmentation only for non-simulated providers, merged and flagged `INFERRED` |
| `tool_service.py` | `build_tool_context`, `run_builtin`, `run_checkov_for`, `ALL_RULE_IDS` | Glue between parsed documents and `backend/tools` |
| `context_service.py` | `ContextService` (`base_chunks`, `rubric`, `scoring_rubric`, `control_mappings`, `controls_for_rules`, `chunks_from_documents`, `build_index`), `TfidfIndex`, `KnowledgeChunk`, `chunk_markdown` | Offline lexical retrieval over `knowledge/` and uploaded STANDARD/ADR/INCIDENT documents |
| `review_service.py` | `ReviewService` | Review lifecycle (`create_review`, `set_status`, `ensure_startable`), event log (`emit`, `events`), cooperative cancel flags, agent executions (`begin_agent`, `mark_skipped`, `update_execution`), findings/tool results/models/revisions, `status_view` |
| `decision_service.py` | `DecisionService`, `apply_decisions(findings, decisions, max_disputes)` | Record and validate architect decisions, lifecycle updates, ADR drafting (`ensure_adr`, `ensure_review_adrs`) |
| `revision_service.py` | `RevisionService.create_revision`, `diff_models`, `compute_impact`, `PROPERTY_DOMAINS`, `TYPE_DOMAINS` | New artifact set + model + change impact (which agents must re-run) |
| `scoring_service.py` | `ScoringService.scorecard` | Explainable per-domain score from `scoring.yaml` |
| `evaluation_service.py` | `EvaluationService.evaluate` | Compare findings with `demo/expected_results/<scenario>.json` (precision/recall on fixtures) |
| `report_service.py` | `ReportService.build / generate / list / get` | Assemble `AssessmentReport`, write JSON and PDF, register `ReportArtifact` |
| `snapshot_service.py` | `SnapshotService` (`enable_autosave`, `schedule_save`, `flush`, `save_now`, `restore`, `save_review_snapshot`) | Debounced atomic snapshots and restore |
| `demo_service.py` | `DemoService` (`list_scenarios`, `scenario_files`, `revision_files`, `expected`, `new_project`) | Reads `demo/` fixtures |
| `workflow_service.py` | `WorkflowService.start / resume / re_review / cancel` | API-level commands over `GraphExecutor`; builds resume payloads from pending decisions |

## Behaviour worth knowing

* **Decision rules** (`DecisionService.record`): only while the review is `AWAITING_HUMAN`; a DISPUTE
  or DEFER needs a justification of at least 10 characters; disputes per finding are capped by
  `max_disputes_per_finding` (default 2); REJECTED/VERIFIED or moderator-rejected findings accept no
  decision. A DEFER immediately drafts an ADR; accepted CRITICAL/HIGH/MEDIUM findings and deferred
  findings get ADRs at finalization (`ensure_review_adrs`, idempotent).
* **`apply_decisions`** is pure and idempotent: a `decision_id` already present in a finding's
  history is skipped. It is used both by `DecisionService.record` and by the `human_review` node.
* **Scoring**: penalty = severity weight x evidence multiplier x status factor; confirmed and
  `NEEDS_VALIDATION` penalties are reported separately; the score is rounded to 5; missing agent
  coverage becomes `coverage` (`FULL` / `PARTIAL` / `NONE`) rather than a lower score.
* **Revision impact** (`compute_impact`): property-name regexes and component-type tables map
  changes to agents, plus tool-result deltas and change-note keywords. If no structured diff exists
  the impact is `LOW` confidence and **all enabled agents are re-run** (`broadened`).
* **Snapshots**: every committed write schedules `save_now` after `snapshot_debounce_seconds`;
  `state.json` is replaced atomically and the previous file is copied to `state.json.bak`.
  `restore` tries `state.json` then `state.json.bak` and requires `schema_version == 1`.

## Inputs and outputs

* In: uploaded bytes, `ReviewCreate`/`DecisionCreate` payloads, `Tables`, `DataPaths`,
  `SettingsStore`, LLM providers (for extraction).
* Out: rows in `Tables`; files under `data/uploads/`, `data/reports/`, `data/snapshots/`;
  `ArchitectureModel`, `Scorecard`, `AssessmentReport` objects.

## Dependencies

`models`, `parsers` (ingestion, context), `tools` (tool_service, revision, architecture),
`llm` (architecture), `reporting` (decision, report), `core` (config, paths, exceptions).
`ArchitectureService` imports `tools.base.references_in` and the private
`tools.configuration_checks._cfn_walk_refs`.

## Role in application execution

Routes call services directly; graph nodes call them via `get_container()`:
`validate_input`/`parse_documents` use `ReviewService`/`IngestionService`, `extract_architecture`
uses `ArchitectureService` + `tool_service`, `moderator`/`human_review`/`rebuttal`/`finalize` use
`ReviewService`, `DecisionService`, `ReportService` and `SnapshotService`.

## Extension points

* New service: write the class, construct it in `AppContainer.__init__`, expose it in routes.
* Extraction: add provider types to `TF_TYPE_MAP` / `CFN_TYPE_MAP`, vocabulary to `_TYPE_LEXICON`,
  `_NFR_PATTERNS`, `_SECURITY_CONTROLS`, `_OPS_TOOLING` in `architecture_service.py`.
* Revision impact: extend `PROPERTY_DOMAINS`, `TYPE_DOMAINS`, `_NOTE_KEYWORDS`.
* Scoring weights, bands and status factors: edit `knowledge/agent_rubrics/scoring.yaml`
  (no code change).
* Report content: `ReportService.build`; rendering lives in `backend/reporting`.

## Limitations

* The text extractor is regex- and lexicon-based; free-text architecture descriptions yield
  lower-confidence components. Unsupported Terraform/CloudFormation resource types are skipped.
* Raster images are only interpreted by vision-capable live providers; in demo mode they produce a
  `LIMITATION` note.
* `ContextService` caches (`lru_cache`/`cache`) are per process; edits to `knowledge/` need a restart.
* `ReviewService` cancel flags are in memory only.
* `EvaluationService` precision/recall are meaningful only for scenarios that have expected results.
