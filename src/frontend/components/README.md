# frontend/components

Reusable, page-independent building blocks. Nothing in this package talks to the backend except through
`frontend.api_client`, and nothing imports from `backend`.

## `ui.py`

Presentation, navigation and error-handling helpers shared by all pages.

| Area | Key functions / constants | Notes |
| --- | --- | --- |
| Theme | `inject_css()` | One restrained slate stylesheet injected with `st.html`; called once from `app.py`. |
| Vocabulary | `SEVERITIES`, `SEVERITY_COLORS`, `STATUS_STYLES`, `AGENT_IDS`, `AGENT_LABELS`, `RUNNING_STATUSES`, `COMPLETED_STATUSES` | Severity palette: CRITICAL `#b91c1c`, HIGH `#c2410c`, MEDIUM `#a16207`, LOW `#1d4ed8`, INFORMATIONAL `#475569`. |
| Chips | `severity_chip`, `status_badge`, `simulated_chip`, `origin_label` | `origin_label` renders ORGANIZATIONAL_STANDARD, CONTROL_MAPPING and GENERAL_PRACTICE as visibly different labels. |
| Banners | `simulated_banner(review)`, `local_only_notice()`, `live_mode_warning()`, `not_an_approval_notice()`, `page_header()` | `simulated_banner` shows "SIMULATED DEMO MODE — findings are fixture-generated, not AI-discovered" only for `simulated` reviews. |
| Backend access | `fetch`, `attempt`, `show_api_error`, `cached_scenarios`, `cached_supported_documents` | `fetch` stops the run on error; `attempt` returns `None`. Only static lookups are cached. |
| Active review | `get_review_id`, `set_review_id`, `require_review`, `sidebar_review_picker`, `open_review` | Session key `review_id`, mirrored to the `review_id` query parameter. |
| Navigation | `PAGES`, `PageSpec`, `goto`, `page_for_status` | `PAGES` is the single registry used by `app.py` and `goto`. |
| Tables | `style_frame`, `metric_cards`, `tool_summary_frame`, `decisions_frame`, `tool_results_frame` | `style_frame` colours severity and status columns with a pandas Styler. |
| Renderers | `agent_card`, `event_timeline`, `source_references_html` / `render_source_references`, `finding_card`, `finding_detail`, `rebuttal_view`, `legend` | `finding_detail` is read-only; decision forms live in the Findings page because they trigger API calls and reruns. |
| Formatting | `fmt_dt`, `label`, `md_text`, `esc`, `na`, `pct`, `join_or_dash`, `default_document_role` | `fmt_dt` produces `YYYY-MM-DD HH:MM:SS` in local time. |

Inputs are plain dictionaries exactly as returned by the REST API (no model classes are duplicated here). All
dynamic text is escaped before being placed in HTML.

## `graphs.py`

Pure functions returning Graphviz DOT strings, rendered with `st.graphviz_chart` (client side, no system Graphviz).

* `workflow_dot(topology)` - LangGraph workflow from `GET /reviews/{id}/graph`; nodes in `next_nodes` are highlighted,
  conditional edges are dashed.
* `dependency_dot(model, hidden_edge_types, focus, hops)` - architecture dependency graph; nodes are coloured by
  component type group (`TYPE_GROUPS`), edges are labelled, INFERRED edges are dashed, `network_membership` and
  `uses_identity` edges are hidden by default, isolated components are omitted, and an optional focus component limits
  the graph to its neighbourhood.
* `diagram_dot(nodes, edges)` - nodes and connections the parser extracted from a diagram document.

## Participation in execution

Pages import `ui` and `graphs` at the top and call them while the Streamlit script runs; there is no background
thread. `ui.sidebar_review_picker` and `ui.inject_css` run from `app.py` on every rerun before the active page.

## Extension points

* Add a status/severity: extend `STATUS_STYLES` or `SEVERITY_COLORS`.
* Add a renderer: put HTML construction in a function that escapes with `esc`, and emit it with `emit_html`.
* Add a graph type: add a pure builder to `graphs.py` and keep it free of Streamlit calls so it can be unit tested.

## Limitations

* `st.html` content is sanitised by Streamlit; scripts and arbitrary custom components are not used.
* Chips inside `st.dataframe` cannot be rendered as HTML, so tables use colour styling of the cell text instead.
* The dependency graph is laid out by Graphviz in the browser; very large architectures (hundreds of components) are
  readable only with the focus control.
