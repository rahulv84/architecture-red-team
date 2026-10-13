# frontend - Streamlit user interface

The browser UI of Architecture Red Team: an architecture governance dashboard for submitting architecture material,
watching the multi-agent review run, deciding on findings, exploring the extracted architecture and downloading reports.

The frontend is a **pure REST client**. It never imports from `backend`, never opens the database and never calls
LangGraph. Every read and write goes through `api_client.py` to the FastAPI backend.

## Layout

| Path | Role |
| --- | --- |
| `app.py` | Entry point: `st.set_page_config`, global CSS, `st.navigation` over the seven pages, sidebar review picker and footer. |
| `api_client.py` | Synchronous `httpx` wrapper: one typed helper per endpoint, `ApiError`, timeouts, multipart uploads. |
| `components/ui.py` | Shared presentation layer: chips, banners, formatting, active-review handling, error handling, renderers. |
| `components/graphs.py` | Pure Graphviz DOT builders (workflow graph, dependency graph, diagram graph). |
| `pages/*.py` | One script per page (see `pages/README.md`). |

## Running

The normal way is the one-command launcher from the repository root, which starts the API and this UI and sets
`ART_BACKEND_URL` and `PYTHONPATH`:

```
python src/run.py
```

To run the UI alone against an already running backend:

```
set ART_BACKEND_URL=http://127.0.0.1:8000/api/v1
streamlit run frontend/app.py
```

`app.py` inserts the repository root into `sys.path` so `frontend.*` imports resolve however Streamlit was launched. The
page scripts do the same, which allows running one page in isolation with `streamlit.testing.v1.AppTest.from_file`.

## Inputs and outputs

* **Input:** environment variable `ART_BACKEND_URL` (default `http://127.0.0.1:8000/api/v1`), user interaction in the
  browser, and an optional `?review_id=` query parameter that selects the active review.
* **Output:** HTML rendered by Streamlit; REST calls to the backend (never to a model provider directly).
* **State:** the active review lives in `st.session_state["review_id"]` and is mirrored to the `review_id` query
  parameter. Everything else (page flows such as "prepared project" or "waiting for the workflow") is kept in
  session state keys prefixed by the page (`nr_`, `wait_`, `events_`, `started_`). Nothing live is cached; only static
  lookups (demo scenarios, supported file types) use `st.cache_data` with a TTL.

## Key mechanisms

* **Backend errors.** `api_client.ApiError(status, code, message, details)` is built from the backend error body
  `{"error": {"code", "message", "details"}}`. Transport failures become `status == 0` (`backend_unavailable` /
  `backend_timeout`). `ui.fetch` shows the error and stops the script run; `ui.attempt` shows it and returns `None`
  for user actions. Every page therefore degrades to a friendly message when the backend is down.
* **Live progress.** Review Execution and the Findings "waiting" panel use `st.fragment(run_every=2)` to poll
  `GET /status`, `/events?after=`, `/agents` and `/graph`. Progress is derived from real agent statuses, never a timer.
  When the workflow stops, the fragment calls `st.rerun()` once so the rest of the app (sidebar badge, polling
  interval) catches up.
* **Resuming the workflow.** Submitting decisions, finalizing and re-review all call `POST /resume` or `/re-review`
  and then wait for a `graph_interrupted` event newer than the sequence number captured *before* the call (the status
  alone is racy because the backend flips it asynchronously).
* **Simulated demo marker.** `ui.simulated_banner(review)` renders the red "SIMULATED DEMO MODE" banner on every page
  that shows review data whenever the review has `simulated: true`; findings and agents also carry a SIMULATED chip.
* **Escaping.** Every backend-provided string is HTML-escaped (`ui.esc`) before it reaches `st.html`; markdown text is
  passed through `ui.md_text`.

## Dependencies

`streamlit` (1.37+, developed against 1.65), `httpx`, `pandas` and `altair` (both installed with Streamlit). No
system Graphviz is needed: DOT strings are rendered in the browser by `st.graphviz_chart`.

## Extension points

* **New page:** add a script under `pages/`, register it in `ui.PAGES` (path, title, icon, URL path). `app.py` builds
  the navigation from that registry and `ui.goto` uses it for programmatic navigation.
* **New endpoint:** add one typed helper to `api_client.py`; keep error handling in `_request`.
* **New status or severity colour:** extend `ui.STATUS_STYLES` / `ui.SEVERITY_COLORS` (they are the single source for
  chips and table styling).
* **Theme:** global CSS lives in `ui._CSS`; colours for the Streamlit theme are in `.streamlit/config.toml`.

## Limitations

* Polling, not push: updates arrive at the poll interval (about two seconds) and only while a fragment is running.
* The sidebar status badge is refreshed on full reruns, so it can lag behind the live fragment by one transition.
* The review timeline keeps the 1,500 most recent events per session.
* The selected finding and component are remembered per browser session; there is no deep link to one finding.
* In offline demo mode, findings exist only for the bundled demo scenarios. Uploaded files produce tool observations but
  no findings (the page says so); live mode requires a configured provider.
* Timestamps are converted to the local time zone of the machine running Streamlit.
* There is no authentication or multi-user separation: the app is intended for loopback use only.
