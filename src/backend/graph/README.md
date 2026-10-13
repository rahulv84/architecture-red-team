# backend/graph

The LangGraph workflow: state, nodes, routing, graph construction, the file-backed checkpointer and
the executor that runs, resumes and recovers reviews.

## Purpose and responsibility

* Express the review process as a compiled `StateGraph` with a real human-in-the-loop `interrupt()`.
* Run the six critics in parallel (`Send` fan-out) through one reusable specialist subgraph.
* Persist graph progress in `data/checkpoints/langgraph.sqlite` so a review can continue after a
  restart.
* Keep state JSON-serialisable: nodes receive only dicts/lists/strings and fetch services with
  `get_container()`.

## Modules

| File | Contents |
| --- | --- |
| `state.py` | `ReviewState`, `SpecialistState` (TypedDicts); list fields written by parallel branches use the `operator.add` reducer (`agent_results`, `candidate_findings`, `tool_results`, `human_decisions`, `rebuttals`, `execution_events`, `errors`) |
| `routing.py` | `AGENT_NODES` (`"<agent>_agent"`), `entry_router`, `fan_out`, `route_after_human` |
| `nodes.py` | Main-graph nodes and the specialist subgraph nodes |
| `builder.py` | `build_specialist_subgraph()`, `build_review_graph(checkpointer)`, `graph_topology(compiled)` |
| `checkpoint.py` | `create_checkpointer(db_path)` (SQLite WAL, `SqliteSaver`), `has_checkpoint(saver, thread_id)` |
| `execution.py` | `GraphExecutor` |

## Graph topology

```
START -> entry_router -> validate_input | human_review (reconstructed runs)
validate_input -> parse_documents -> extract_architecture -> retrieve_context -> select_critics
select_critics -> fan_out -> {security,resilience,finops,compliance,performance,operability}_agent (parallel Send)
each *_agent -> moderator -> human_review
human_review -> route_after_human -> rebuttal | apply_revision | finalize | human_review (loop)
rebuttal -> human_review ; apply_revision -> retrieve_context ; finalize -> END
```

Each `*_agent` node wraps the specialist subgraph:
`select_rules -> run_tools -> scope_context -> retrieve_evidence -> llm_analysis -> validate_findings`.

## Nodes

| Node | Behaviour |
| --- | --- |
| `validate_input` | Clears cancel flag, status `EXTRACTING`, emits `review_started`, requires parsable documents and enabled agents, validates live provider configuration |
| `parse_documents` | Loads `parsed.json` through `ReviewCache`; emits one `document_parsed` per file |
| `extract_architecture` | `ArchitectureService.build_model`, saves the model, runs all built-in rules once as the revision baseline |
| `retrieve_context` | Builds the review's `TfidfIndex`; per-agent context chunks into `contextual_evidence` |
| `select_critics` | Chooses `rerun_agents` (all enabled in round 1, affected agents later); marks others `SKIPPED` |
| `<agent>_agent` | `begin_agent`, run subgraph, `update_execution`; a failing critic returns a `FAILED` result without aborting others (cancel is re-raised) |
| `moderator` | Fails the run only if *all* critics failed; otherwise `ModeratorAgent.moderate`, `replace_findings`, status `AWAITING_HUMAN`, per-round review snapshot |
| `human_review` | Calls `interrupt(payload)`; on resume applies new decisions (`apply_decisions`), marks them submitted and sets `next_step` |
| `rebuttal` | For each pending dispute: `request_rebuttal` -> `adjudicate`; updates finding, may create a dispute-resolution ADR |
| `apply_revision` | Loads the stored revision model, sets `rerun_agents` from the impact, increments the round, invalidates `ReviewCache` |
| `finalize` | Status `COMPLETED` or `COMPLETED_WITH_OPEN_RISKS`, ADRs, JSON+PDF reports, final snapshot |

Routing rules in `human_review`: pending disputes -> `rebuttal`; `action == "re_review"` ->
`apply_revision` (requires `revision_id` and `round < maximum_rounds`, otherwise a `resume_rejected`
event and the node loops); `action == "finalize"` -> `finalize`; anything else loops back to
`human_review` and interrupts again. Code before `interrupt()` is side-effect free because LangGraph
re-runs the node on resume.

## `GraphExecutor`

| Method | Behaviour |
| --- | --- |
| `__init__(container)` | Creates the checkpointer and compiles the graph |
| `start(review_id)` | `ensure_startable` (CREATED or FAILED), rejects duplicates, submits `run` to the `TaskManager` |
| `run(review_id, resume_payload=None)` | Blocking: `invoke(Command(resume=payload))` for resumes; `invoke(None)` when a checkpoint with pending `next` exists (restart continuation); otherwise `invoke(initial_state)`. Handles `ReviewCancelled` (-> FAILED "Cancelled") and any other exception (-> FAILED with `_safe_error` text); always schedules a snapshot |
| `resume(review_id, payload)` | Requires `AWAITING_HUMAN`; if the thread is not at an interrupt it calls `_reconstruct` first |
| `_after_invoke` | Sets `AWAITING_HUMAN` if the thread is interrupted, or the final workflow status when the graph ended |
| `recover_on_startup()` | Reviews that were running become `FAILED` with guidance (never auto-restarted); `AWAITING_HUMAN` reviews without a usable checkpoint get a `recovery_note` |
| `snapshot`, `state_values`, `is_interrupted`, `has_checkpoint` | Read helpers used by routes and recovery |

Config per run: `thread_id = Review.thread_id` (`thread-<review_id>`), `max_concurrency =
max_concurrent_agents`, `recursion_limit = 120`.

## Inputs and outputs

* In: `review_id`; resume payload `{"action": "submit_decisions" | "re_review" | "finalize",
  "decisions": [...], "notes": str, "revision_id": str | None}` built by `WorkflowService`.
* Out: `ReviewState` checkpoints in SQLite; side effects through services (findings, executions,
  events, reports, snapshots). Progress is observable via `ExecutionEvent` rows.

## Dependencies

`agents` (`AGENT_CLASSES`, `ModeratorAgent`, `request_rebuttal`), `services` (`apply_decisions`,
`tool_service`), `core.lifecycle.get_container`, `core.exceptions`, `models.domain`,
`langgraph`, `langgraph-checkpoint-sqlite`.

## Role in application execution

`WorkflowService.start` -> `GraphExecutor.start` -> background thread -> `GraphExecutor.run` ->
`graph.invoke` -> nodes above. `GET /reviews/{id}/graph` returns `graph_topology` plus the current
`next` nodes and interrupt status.

## Extension points

* New node: write it in `nodes.py` (use `get_container()`, return a partial state dict), add the
  field to `ReviewState`, register in `build_review_graph` and wire edges/routing.
* New resume action: extend `human_review` and `route_after_human`, and
  `WorkflowService.resume` validation.
* Specialist subgraph steps: edit `build_specialist_subgraph` and `SpecialistState`.

## Limitations

* The checkpoint store is a single SQLite file shared by all reviews; there is no cleanup of old
  threads.
* Reconstructed threads are named `<thread-id>-recon<hex>` so repeated reconstructions never share a thread.
* The graph does not run in a separate process; a backend crash mid-node loses that node's work and
  the review is marked `FAILED` on next startup (press Start to continue from the last checkpoint).
* Cancellation is cooperative and only checked at node/step boundaries.
