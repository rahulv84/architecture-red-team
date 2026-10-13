# frontend/pages

One plain script per page, registered in `components/ui.py::PAGES` and mounted by `st.navigation` in `app.py`. Each
script starts with `ui.page_header(title, subtitle)` and resolves the active review with `ui.require_review()` (except
Dashboard, New Review and Settings). Pages contain no business logic: they call REST endpoints through
`frontend.api_client` and render the answers.

| Page (`url_path`) | Purpose | Endpoints |
| --- | --- | --- |
| `dashboard.py` (`dashboard`) | Portfolio totals, open Critical+High, risk distribution by domain and severity, recent reviews with Open buttons, backend health and default mode. | `GET /health`, `/config`, `/projects`, `/reviews` |
| `new_review.py` (`new-review`) | Review details, demo scenario or multi-file upload with per-file roles, parse results, agents and rounds, double-submit-safe start. | `GET /config`, `/providers`, `/demo/scenarios`, `/documents/supported`, `/projects/{id}/documents`; `POST /demo/scenarios/{id}/project`, `/projects`, `/projects/{id}/documents`, `/reviews`, `/reviews/{id}/start` |
| `review_progress.py` (`execution`) | Live phase, round, six agent cards, moderator, derived progress bar, alerts, timeline, tool summary, LangGraph diagram, Start/Cancel. | `GET /reviews/{id}/status`, `/events?after=`, `/agents`, `/graph`; `POST /start`, `/cancel` |
| `findings.py` (`findings`) | Filters, selectable table, finding detail, Accept/Dispute/Defer forms, submit decisions, finalize, revise architecture, targeted re-review. | `GET /reviews/{id}/findings`, `/findings/{id}`, `/status`, `/events`, `/revisions`, `/adrs`; `POST /decisions`, `/resume`, `/revisions`, `/re-review` |
| `architecture.py` (`architecture`) | Components, relationships, dependency graph, requirements, extraction notes, source document viewer. | `GET /reviews/{id}/architecture?revision=`, `/documents/{id}/content` |
| `reports.py` (`reports`) | Overview, scorecard and chart, findings views, history, ADRs, evaluation harness, report generation and download. | `GET /reviews/{id}/report-preview`, `/score`, `/adrs`, `/reports`, `/evaluation`; `POST /reviews/{id}/reports`; `GET /reports/{id}/download` |
| `settings.py` (`settings`) | Provider/model/limits, write-only API keys, provider validation, tool availability, data directory. | `GET/PUT /settings`, `GET /providers`, `POST /providers/validate`, `GET /settings/tools` |

## Behaviours worth knowing

* **Simulated banner:** every page that shows review data calls `ui.simulated_banner(review)`.
* **Findings decisions** are only offered while the review status is `AWAITING_HUMAN` and the finding is not
  `REJECTED`/`RESOLVED`/`VERIFIED`. Disputes and deferrals require a 10 character justification; the number of remaining
  disputes is `max_disputes_per_finding - dispute_count`.
* **Waiting for the workflow** (submit decisions, re-review, finalize): the page stores `wait_<review_id>` in session
  state and polls until a `graph_interrupted` event newer than the baseline arrives or the review reaches a final
  status; warnings emitted meanwhile (for example `resume_rejected`) are shown afterwards.
* **Re-review button** is disabled with an explicit reason: not awaiting human input, maximum rounds reached, or no
  pending revision.
* **Downloads** use deferred `st.download_button` data so a report file is fetched only when it is clicked.

## Extension points

Add a script here and register it in `ui.PAGES`. Keep page functions small (one function per section) and push anything
reusable into `components/ui.py`.

## Limitations

* Row selection uses `st.dataframe(on_select=...)`, which headless `AppTest` cannot drive; tests select findings by
  setting `st.session_state["finding_id"]`.
* Pages bootstrap `sys.path` with the repository root so they can be tested in isolation; they are otherwise meant to
  run through `app.py`.
* Charts use Altair (bundled with Streamlit) and therefore follow the Streamlit theme fonts.
