"""Review Execution: live progress of the multi-agent workflow.

Driven only by GET /reviews/{id}/status, /events?after=, /agents and /graph, polled with a Streamlit
fragment (about every two seconds while work is running).  Actions: POST /start, POST /cancel.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import graphs, ui  # noqa: E402

POLL_SECONDS = 2
START_GRACE_SECONDS = 30
EVENT_PAGE = 500
MAX_EVENTS_KEPT = 1500


def _started_recently(review_id: str) -> bool:
    ts = st.session_state.get(f"started_{review_id}")
    return isinstance(ts, (int, float)) and time.time() - ts < START_GRACE_SECONDS


def _is_active(status: dict, review_id: str) -> bool:
    """True while the workflow is running (or was just started and has not reported progress yet)."""
    return status["status"] in ui.RUNNING_STATUSES or (status["status"] == "CREATED" and _started_recently(review_id))


def _sync_events(review_id: str) -> list[dict]:
    """Incrementally fetch events (GET /events?after=) into session state and return all known events."""
    store = st.session_state.setdefault(f"events_{review_id}", {"events": [], "last": 0})
    while True:
        page = ui.fetch(api.review_events, review_id, after=store["last"], limit=EVENT_PAGE)
        store["events"].extend(page["events"])
        store["last"] = page["last_seq"]
        if len(page["events"]) < EVENT_PAGE:
            break
    store["events"] = store["events"][-MAX_EVENTS_KEPT:]
    return store["events"]


def _summary_row(status: dict) -> None:
    ui.metric_cards([
        ("Phase", status["phase"], ""), ("Round", f"{status['current_round']} / {status['max_rounds']}", "review rounds used"),
        ("Architecture revision", status["architecture_revision"], ""),
        ("Mode", "Demo (simulated)" if status["simulated"] else "Live", f"{status['provider']} · {status['model_id']}"),
        ("Findings", status["finding_counts"].get("total", 0), f"{status['open_critical_high']} open Critical/High"),
        ("Pending decisions", status["pending_decisions"], "not yet submitted"),
    ])


def _controls(review_id: str, status: dict) -> None:
    state = status["status"]
    if state == "AWAITING_HUMAN":
        st.success(f"Specialist analysis is complete. {status['finding_counts'].get('total', 0)} finding(s) await architect decisions "
                   f"({status['open_critical_high']} open Critical/High).", icon=":material/how_to_reg:")
        if st.button("Open the Findings Explorer", type="primary", icon=":material/rule:", key="goto_findings"):
            ui.goto("findings")
    elif state in ui.COMPLETED_STATUSES:
        st.success("The review is complete. This is not an approval of the architecture.", icon=":material/task_alt:")
        if st.button("Open the report", type="primary", icon=":material/description:", key="goto_reports"):
            ui.goto("reports")
    if state in ("CREATED", "FAILED"):
        text = "Start review" if state == "CREATED" else "Start again (resumes from the last checkpoint when one exists)"
        if st.button(text, type="primary", icon=":material/play_arrow:", key="start_review"):
            if ui.attempt(api.start_review, review_id) is not None:
                st.session_state[f"started_{review_id}"] = time.time()
                st.rerun()
    if state in ui.RUNNING_STATUSES:
        if st.button("Cancel review", icon=":material/cancel:", key="cancel_review"):
            if ui.attempt(api.cancel_review, review_id) is not None:
                st.toast("Cancellation requested; the run stops at the next checkpoint.")
                st.rerun()


def _alerts(status: dict) -> None:
    if status.get("error"):
        st.error(status["error"], icon=":material/error:")
    if status.get("recovery_note"):
        st.warning(status["recovery_note"], icon=":material/restart_alt:")
    for message in dict.fromkeys(status.get("warnings") or []):
        st.warning(message, icon=":material/warning:")


def _agents_tab(status: dict) -> None:
    agents = status["agents"]
    for start in range(0, len(agents), 3):
        cols = st.columns(3)
        for col, agent in zip(cols, agents[start:start + 3]):
            with col:
                ui.agent_card(agent)
    moderator = status.get("moderator") or {}
    st.html(f'<div class="art-card"><div class="t">Moderator {ui.status_badge(moderator.get("status", "PENDING"))}</div>'
            f'<div class="meta">{ui.esc(moderator.get("summary") or "The moderator merges, validates and challenges proposed findings after the specialists finish.")}'
            f'</div></div>')
    counts = status["finding_counts"]
    chips = "".join(ui.severity_chip(s) + f'<span style="margin-right:10px">{counts.get(s, 0)}</span>' for s in ui.SEVERITIES)
    st.html(f'<div class="art-section">Findings by severity</div>{chips}')


def _tools_tab(review_id: str) -> None:
    data = ui.fetch(api.review_agents, review_id)
    summaries = data.get("tool_summaries") or []
    if not summaries:
        st.caption("No deterministic tool runs have been recorded yet.")
        return
    st.dataframe(ui.style_frame(ui.tool_summary_frame(summaries), status=["Status"]), hide_index=True, width="stretch")
    skipped = [s for s in summaries if s.get("status") == "NOT_EXECUTED"]
    if skipped:
        st.info("Tools that did not run are reported as NOT_EXECUTED so that missing coverage is visible: "
                + "; ".join(f"{s['tool_name']} ({s.get('detail') or 'no reason given'})" for s in skipped), icon=":material/info:")
    results = data.get("tool_results") or []
    if results:
        with st.expander(f"Tool results ({len(results)})"):
            frame = pd.DataFrame([{
                "Rule": r["rule_id"], "Tool": r["tool_name"], "Domain": r["domain"], "Outcome": r["outcome"],
                "Evidence": r["evidence_level"], "Title": r["title"], "Components": ui.join_or_dash(r.get("affected_component_ids") or []),
            } for r in results])
            st.dataframe(ui.style_frame(frame, status=["Outcome", "Evidence"]), hide_index=True, width="stretch")


def _graph_tab(review_id: str) -> None:
    topology = ui.fetch(api.review_graph, review_id)
    st.graphviz_chart(graphs.workflow_dot(topology), width="stretch")
    ui.legend([("Specialist agent", "#dbeafe", "#1d4ed8"), ("Moderator", "#ede9fe", "#6d28d9"), ("Human review", "#fef3c7", "#a16207"),
               ("Next to execute", "#fde68a", "#b45309")])
    nxt = topology.get("next_nodes") or []
    st.caption(f"Next node(s): {', '.join(nxt) if nxt else 'none (the workflow is finished or has not started)'} · "
               f"workflow status: {topology.get('workflow_status') or 'N/A'} · "
               f"interrupted for a human: {'yes' if topology.get('interrupted') else 'no'}. Dashed edges are conditional.")
    if topology.get("recovery_note"):
        st.warning(topology["recovery_note"], icon=":material/restart_alt:")


def _render(review_id: str, status: dict) -> None:
    ui.emit_html(f'{ui.status_badge(status["status"])}'
                 f'{ui.simulated_chip() if status["simulated"] else ""}'
                 f'<span class="art-chip" style="color:#334155;background:#e2e8f0">revision {ui.esc(status["architecture_revision"])}</span>')
    done, total = ui.agent_progress(status["agents"])
    st.progress(done / total if total else 0.0, text=f"{done} of {total} specialist agents finished (completed, skipped or failed)")
    _summary_row(status)
    _alerts(status)
    _controls(review_id, status)
    events = _sync_events(review_id)
    tab_agents, tab_timeline, tab_tools, tab_graph = st.tabs(["Agents", "Execution timeline", "Tools", "LangGraph workflow"])
    with tab_agents:
        _agents_tab(status)
    with tab_timeline:
        ui.event_timeline(events)
    with tab_tools:
        _tools_tab(review_id)
    with tab_graph:
        _graph_tab(review_id)


ui.ensure_shell("review_progress")
ui.page_header("Review Execution", "Live status of the specialist agents, the moderator and the LangGraph workflow.")
review = ui.require_review()
review_id = review["review_id"]
ui.simulated_banner(review)
st.markdown(f"**{ui.md_text(review['name'])}**  ·  project {ui.md_text(review.get('project_name') or review['project_id'])}  ·  `{review_id}`")

initial = ui.fetch(api.review_status, review_id)
polling = _is_active(initial, review_id)


@st.fragment(run_every=POLL_SECONDS if polling else None)
def live_view() -> None:
    """Re-fetches status, events, agents and graph on every tick; reruns the app once the work stops."""
    status = ui.fetch(api.review_status, review_id)
    _render(review_id, status)
    if polling and not _is_active(status, review_id):
        st.session_state.pop(f"started_{review_id}", None)
        st.rerun()


live_view()
