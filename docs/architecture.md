# Architecture guide

The full design (diagrams, data flow, trade-offs) is in [system-design.md](system-design.md). This page is the
quick module map and request-flow cheat sheet.

## Layers and where they live
| Layer | Package | Main entry points |
| --- | --- | --- |
| Launcher | `src/run.py` | `main()` |
| API | `src/backend/api/routes` | routers included by `src/backend/main.py::create_app` |
| Services | `src/backend/services` | classes wired in `src/backend/core/lifecycle.py::AppContainer` |
| Orchestration | `src/backend/graph` | `builder.build_review_graph`, `execution.GraphExecutor`, `nodes.*` |
| Agents | `src/backend/agents` | `BaseAgent`, `AGENT_CLASSES`, `ModeratorAgent` |
| Tools | `src/backend/tools` | `@register`, `AGENT_RULES`, `run_rules` |
| LLM | `src/backend/llm` | `LLMProvider`, `create_provider`, `MockProvider` |
| Persistence | `src/backend/models/database.py`, `services/snapshot_service.py` | `Database`, `EntityTable`, `Tables`, `SnapshotService` |
| UI | `src/frontend/` | `app.py` + `pages/*` |

## Request flow: start a review
1. `POST /reviews` -> `ReviewService.create_review` (stores `Review`, revision 1 with the active artifact IDs).
2. `POST /reviews/{id}/start` -> `WorkflowService.start` -> `GraphExecutor.start` submits `run()` to `TaskManager` and returns 202.
3. `GraphExecutor.run` invokes the compiled graph with `thread_id = review.thread_id` and the SQLite checkpointer.
4. Nodes update the DB through services (status, events, agent executions, findings) so the UI can poll `GET /reviews/{id}/status`.
5. `human_review` interrupts; `_after_invoke` sets `AWAITING_HUMAN`.

## Request flow: decisions and resume
1. `POST /decisions` -> `DecisionService.record` stores a `HumanDecision`, updates the finding status immediately, creates an ADR for DEFER.
2. `POST /resume` -> `WorkflowService.resume` bundles pending decisions -> `GraphExecutor.resume` -> `Command(resume=payload)`.
3. `human_review` applies decisions (`apply_decisions`), routes to `rebuttal`, `apply_revision` or `finalize`.

## Where to look when something misbehaves
* Review stuck/failed: `GET /reviews/{id}/events` and `data/logs/backend.log`.
* Wrong extraction: `GET /reviews/{id}/architecture` (components, notes) and `GET /documents/{id}/content`.
* Missing finding: check `GET /reviews/{id}/tool-results` first, then the agent execution `information_gaps` and moderator diagnostics in the `moderator_completed` event payload.
