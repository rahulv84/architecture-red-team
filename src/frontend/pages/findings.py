"""Findings Explorer: filter findings, inspect evidence, record architect decisions and revise the architecture.

Endpoints: GET /reviews/{id}/status, /findings, /revisions, /adrs; GET /findings/{id}; POST /reviews/{id}/decisions,
/resume (submit_decisions | finalize), /revisions (multipart) and /re-review; GET /reviews/{id}/events (progress while
the workflow runs critic rebuttals or a targeted re-review).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import ui  # noqa: E402

POLL_SECONDS = 2
MIN_JUSTIFICATION = 10
ALL = "All"
FINDING_STATUSES = ("OPEN", "ACCEPTED", "DISPUTED", "REBUTTED", "REJECTED", "DEFERRED", "REMEDIATION_PROPOSED", "RESOLVED", "VERIFIED")
EVIDENCE_LEVELS = ("CONFIRMED", "INFERRED", "NEEDS_VALIDATION")
MODERATOR_STATUSES = ("VALIDATED", "MERGED", "REQUIRES_VALIDATION", "REJECTED")
TERMINAL_WAIT_STATUSES = ui.COMPLETED_STATUSES | {"FAILED"}


# --------------------------------------------------------------------------- waiting for the workflow
def _start_wait(review_id: str, action: str, baseline: int) -> None:
    st.session_state[f"wait_{review_id}"] = {"action": action, "after": baseline}


def _flash(review_id: str) -> None:
    for level, message in st.session_state.pop(f"flash_{review_id}", []):
        (st.error if level == "ERROR" else st.warning if level == "WARNING" else st.success)(message)


def _wait_panel(review_id: str, wait: dict) -> None:
    """Poll status and events until the workflow is back at the human boundary (or has finished)."""

    @st.fragment(run_every=POLL_SECONDS)
    def waiting() -> None:
        status = ui.fetch(api.review_status, review_id)
        events = ui.fetch(api.review_events, review_id, after=wait["after"], limit=500)["events"]
        interrupted = any(e["event_type"] == "graph_interrupted" for e in events)
        if interrupted or status["status"] in TERMINAL_WAIT_STATUSES:
            notes = [(e["level"], e["message"]) for e in events if e["level"] in ("WARNING", "ERROR")]
            outcome = ("Review finalized." if status["status"] in ui.COMPLETED_STATUSES else
                       "The workflow stopped with an error." if status["status"] == "FAILED" else
                       "The workflow is back at the human review step.")
            st.session_state[f"flash_{review_id}"] = [("ERROR" if status["status"] == "FAILED" else "SUCCESS", outcome), *notes]
            st.session_state.pop(f"wait_{review_id}", None)
            st.rerun()
        requested = sum(1 for e in events if e["event_type"] == "rebuttal_requested")
        answered = sum(1 for e in events if e["event_type"] in ("rebuttal_received", "rebuttal_failed"))
        done, total = ui.agent_progress(status["agents"])
        ui.emit_html(ui.status_badge(status["status"]) + f'<span class="art-chip" style="color:#334155;background:#e2e8f0">'
                     f'{ui.esc(status["phase"])}</span>')
        if status["status"] == "REBUTTING" and requested:
            st.progress(min(answered / requested, 1.0), text=f"{answered} of {requested} critic rebuttals received")
        elif wait["action"] == "re_review":
            st.progress(done / total if total else 0.0, text=f"Re-review: {done} of {total} specialist agents finished")
        else:
            st.progress(0.0, text="Submitting decisions to the workflow ...")
        ui.event_timeline([e for e in events if e["event_type"] != "tool_executed"], height=240)

    verb = {"submit_decisions": "Decisions were submitted", "re_review": "Targeted re-review started",
            "finalize": "Finalizing the review"}.get(wait["action"], "Working")
    st.info(f"{verb}. This page updates automatically until the review is back at the human review step.", icon=":material/hourglass_top:")
    waiting()


# --------------------------------------------------------------------------- review-level actions
@st.dialog("Finalize review")
def _finalize_dialog(review_id: str, status: dict) -> None:
    st.markdown(
        "Finalizing ends the review and generates the PDF and JSON reports.\n\n"
        f"- **{status['open_critical_high']}** Critical/High finding(s) are still open.\n"
        f"- **{status['pending_decisions']}** pending decision(s) will be submitted with finalization.\n\n"
        "**Finalizing does not approve the architecture.** The result is `COMPLETED` or `COMPLETED_WITH_OPEN_RISKS`, and approval "
        "remains a human decision.")
    left, right = st.columns(2)
    if left.button("Finalize review", type="primary", key="confirm_finalize", width="stretch"):
        baseline = ui.attempt(api.last_event_seq, review_id)
        if baseline is not None and ui.attempt(api.resume_review, review_id, "finalize") is not None:
            _start_wait(review_id, "finalize", baseline)
            st.rerun()
    if right.button("Cancel", key="cancel_finalize", width="stretch"):
        st.rerun()


def _action_bar(review_id: str, status: dict) -> None:
    awaiting = status["status"] == "AWAITING_HUMAN"
    ui.metric_cards([
        ("Review status", ui.label(status["status"]), status["phase"]),
        ("Pending decisions", status["pending_decisions"], "recorded, not yet submitted"),
        ("Open Critical + High", status["open_critical_high"], "unresolved"),
        ("Round", f"{status['current_round']} / {status['max_rounds']}", f"architecture revision {status['architecture_revision']}"),
    ])
    st.space("small")
    pending = status["pending_decisions"]
    c1, c2, c3 = st.columns([2, 1.4, 2])
    if c1.button(f"Submit {pending} pending decision{'s' if pending != 1 else ''}", type="primary", icon=":material/send:",
                 disabled=not awaiting or pending == 0, key="submit_decisions", width="stretch",
                 help="Runs critic rebuttals for disputed findings, then returns to the human review step."):
        baseline = ui.attempt(api.last_event_seq, review_id)
        if baseline is not None and ui.attempt(api.resume_review, review_id, "submit_decisions") is not None:
            _start_wait(review_id, "submit_decisions", baseline)
            st.rerun()
    if c2.button("Finalize review", icon=":material/flag:", disabled=not status.get("can_finalize"), key="finalize", width="stretch"):
        _finalize_dialog(review_id, status)
    if not awaiting:
        c3.caption(f"Decisions, submission and finalization require status AWAITING_HUMAN (currently {status['status']}).")


# --------------------------------------------------------------------------- filters and table
def _filters(status: dict) -> dict:
    with st.expander("Filters", expanded=True):
        a, b, c, d = st.columns(4)
        severity = a.selectbox("Severity", [ALL, *ui.SEVERITIES], key="f_severity")
        agent = b.selectbox("Agent", [ALL, *ui.AGENT_IDS], format_func=lambda x: ui.AGENT_LABELS.get(x, x), key="f_agent")
        fstatus = c.selectbox("Status", [ALL, *FINDING_STATUSES], format_func=ui.label, key="f_status")
        evidence = d.selectbox("Evidence level", [ALL, *EVIDENCE_LEVELS], format_func=ui.label, key="f_evidence")
        e, f, g, h = st.columns(4)
        component = e.text_input("Affected component contains", key="f_component")
        revision = f.selectbox("Architecture revision", [ALL, *range(1, status["architecture_revision"] + 1)], key="f_revision")
        moderator = g.selectbox("Moderator status", [ALL, *MODERATOR_STATUSES], format_func=ui.label, key="f_moderator")
        query = h.text_input("Search title / description", key="f_query")
        include_rejected = st.checkbox("Include rejected findings", value=False, key="f_rejected")

    def pick(value: object) -> object:
        return None if value in (ALL, "") else value

    return {
        "severity": pick(severity), "agent": pick(agent), "status": pick(fstatus), "evidence_level": pick(evidence),
        "component": pick(component.strip()), "revision": pick(revision), "moderator_status": pick(moderator),
        "q": pick(query.strip()), "include_rejected": include_rejected,
    }


def _findings_table(findings: list[dict], filters: dict) -> None:
    frame = pd.DataFrame([{
        "Severity": f["severity"], "Title": f["title"], "Domain": ui.AGENT_LABELS.get(f["agent_id"], f["agent_id"]),
        "Status": f["status"], "Evidence": f["evidence_level"], "Moderator": f["moderator_status"],
        "Components": ui.join_or_dash(f.get("affected_components") or []),
        "Revisions": f"{f['first_seen_revision']}-{f['last_seen_revision']}",
        "Simulated": "yes" if f.get("simulated") else "",
    } for f in findings])
    key = "findings_table_" + str(abs(hash(tuple(sorted((k, str(v)) for k, v in filters.items())))))
    event = st.dataframe(ui.style_frame(frame, severity=["Severity"], status=["Status", "Evidence", "Moderator"]), key=key,
                         on_select="rerun", selection_mode="single-row", hide_index=True, width="stretch",
                         height=min(420, 38 + 35 * len(findings)))
    rows = event.selection.rows if event and event.selection else []
    if rows:
        st.session_state["finding_id"] = findings[rows[0]]["finding_id"]


# --------------------------------------------------------------------------- decisions
def _record(review_id: str, payload: dict, message: str) -> None:
    if ui.attempt(api.record_decision, review_id, payload) is not None:
        st.toast(message)
        st.rerun()


def _decision_panel(review_id: str, status: dict, view: dict, max_disputes: int) -> None:
    finding = view["finding"]
    fid = finding["finding_id"]
    if status["status"] != "AWAITING_HUMAN":
        st.caption(f"Decisions can only be recorded while the review is AWAITING_HUMAN (currently {status['status']}).")
        return
    if finding["status"] in ui.CLOSED_FINDING_STATUSES or finding["moderator_status"] == "REJECTED":
        st.caption(f"This finding is {ui.label(finding['status'])}; no further decision is possible.")
        return
    remaining = max(0, max_disputes - int(finding.get("dispute_count", 0)))
    unsubmitted = [d for d in view["decisions"] if not d.get("submitted")]
    with st.container(border=True):
        ui.section_label("Record an architect decision")
        if unsubmitted:
            st.info("Recorded but not yet submitted: " + "; ".join(
                f"{d['decision_type']} ({ui.fmt_dt(d['created_at'])})" for d in unsubmitted), icon=":material/pending:")
        accept, dispute, defer = st.tabs(["Accept", "Dispute", "Defer"])
        with accept:
            with st.form(f"accept_{fid}", clear_on_submit=True):
                notes = st.text_area("Notes (optional)", key=f"accept_notes_{fid}", height=80)
                if st.form_submit_button("Accept finding", type="primary"):
                    _record(review_id, {"finding_id": fid, "decision_type": "ACCEPT", "notes": notes}, "Finding accepted.")
        with dispute:
            st.caption(f"Remaining disputes for this finding: {remaining} of {max_disputes}. A critic will be asked to rebut; "
                       "the moderator decides the outcome after you submit.")
            with st.form(f"dispute_{fid}", clear_on_submit=True):
                why = st.text_area(f"Justification (required, at least {MIN_JUSTIFICATION} characters)", key=f"dispute_why_{fid}", height=100)
                if st.form_submit_button("Dispute finding", disabled=remaining == 0):
                    if len(why.strip()) < MIN_JUSTIFICATION:
                        st.error(f"A dispute needs a justification of at least {MIN_JUSTIFICATION} characters.")
                    else:
                        _record(review_id, {"finding_id": fid, "decision_type": "DISPUTE", "justification": why}, "Dispute recorded.")
        with defer:
            with st.form(f"defer_{fid}", clear_on_submit=True):
                why = st.text_area(f"Justification (required, at least {MIN_JUSTIFICATION} characters)", key=f"defer_why_{fid}", height=80)
                a, b, c = st.columns(3)
                review_date = a.date_input("Review date (optional)", value=None, key=f"defer_date_{fid}")
                owner = b.text_input("Risk owner (optional)", key=f"defer_owner_{fid}")
                target = c.text_input("Remediation target (optional)", key=f"defer_target_{fid}")
                if st.form_submit_button("Defer finding"):
                    if len(why.strip()) < MIN_JUSTIFICATION:
                        st.error(f"Deferring needs a justification of at least {MIN_JUSTIFICATION} characters.")
                    else:
                        _record(review_id, {
                            "finding_id": fid, "decision_type": "DEFER", "justification": why,
                            "review_date": review_date.isoformat() if review_date else None,
                            "risk_owner": owner.strip() or None, "remediation_target": target.strip() or None,
                        }, "Finding deferred; a deferred-risk ADR draft was created.")


def _findings_tab(review_id: str, status: dict, max_disputes: int) -> None:
    filters = _filters(status)
    findings = ui.fetch(api.list_findings, review_id, **filters)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ui.SEVERITIES}
    ui.emit_html(f"<b>{len(findings)}</b> finding(s) shown &nbsp; " + "".join(
        f'{ui.severity_chip(s)}<span style="margin-right:10px">{n}</span>' for s, n in counts.items()))
    if not findings:
        st.info("No findings match these filters." if any(v for k, v in filters.items() if k != "include_rejected")
                else "No findings have been produced yet.", icon=":material/search_off:")
        return
    _findings_table(findings, filters)
    ids = [f["finding_id"] for f in findings]
    selected = st.session_state.get("finding_id")
    if selected not in ids:
        selected = ids[0]
        st.session_state["finding_id"] = selected
    st.caption("Select a row to inspect a finding; the first finding is shown by default.")
    view = ui.fetch(api.get_finding, selected)
    adr_titles: dict[str, str] = {}
    if view.get("adrs"):
        adr_titles = {a["adr_id"]: f"ADR-{a['number']:03d} {a['title']} ({a['status']})" for a in ui.fetch(api.list_adrs, review_id)}
    _decision_panel(review_id, status, view, max_disputes)
    st.divider()
    ui.finding_detail(view, adr_titles)


# --------------------------------------------------------------------------- revisions
def _impact_view(impact: dict) -> None:
    broadened = " (confidence is low, so the re-review was broadened to all enabled agents)" if impact.get("broadened") else ""
    ui.emit_html(f'<div class="art-card"><div class="t">Impact summary</div><div class="meta">{ui.esc(impact.get("summary") or "No summary.")}</div>'
                 f'<div class="meta">Confidence: {ui.status_badge(impact.get("confidence"), ui.label(impact.get("confidence")))}'
                 f'{ui.esc(broadened)}</div></div>')
    groups = [("Changed artifacts", "changed_artifacts"), ("Added artifacts", "added_artifacts"), ("Removed artifacts", "removed_artifacts"),
              ("Impacted components", "impacted_components"), ("Changed components", "changed_components"),
              ("Added components", "added_components"), ("Removed components", "removed_components")]
    cols = st.columns(2)
    for idx, (title, key) in enumerate(groups):
        values = impact.get(key) or []
        if values:
            with cols[idx % 2]:
                st.markdown(f"**{title}** ({len(values)})")
                st.write(", ".join(values))
    if impact.get("changed_properties"):
        with st.expander(f"Changed properties ({len(impact['changed_properties'])})"):
            st.write(", ".join(impact["changed_properties"]))
    agents = impact.get("affected_agents") or []
    st.markdown("**Agents that will re-run**")
    if not agents:
        st.caption("No specialist agent is affected by this revision.")
    reasons = impact.get("reasons") or {}
    for agent in agents:
        with st.container(border=True):
            st.markdown(f"**{ui.AGENT_LABELS.get(agent, agent)}**")
            for why in reasons.get(agent, ["Included by the impact analysis."]):
                st.markdown(f"- {ui.md_text(why)}")


def _revision_form(review_id: str, review: dict, awaiting: bool) -> None:
    demo_available = bool(review.get("simulated") and review.get("scenario_id"))
    with st.container(border=True):
        ui.section_label("Submit a revised architecture")
        files = st.file_uploader("Revised files (replace files with the same name)", accept_multiple_files=True, key="rev_files",
                                 disabled=not awaiting)
        items: list[tuple[str, bytes, str]] = []
        primary_taken = False
        for idx, f in enumerate(files or []):
            role = ui.default_document_role(f.name, primary_taken)
            primary_taken = primary_taken or role == "PRIMARY_ARCHITECTURE"
            cols = st.columns([3, 2], vertical_alignment="center")
            cols[0].write(f"{f.name}  ·  {f.size / 1024:.1f} KB")
            picked = cols[1].selectbox("Role", ui.DOCUMENT_ROLES, index=ui.DOCUMENT_ROLES.index(role), key=f"rev_role_{idx}_{f.name}_{f.size}",
                                       label_visibility="collapsed")
            items.append((f.name, f.getvalue(), picked))
        notes = st.text_area("Change notes", key="rev_notes", height=90, disabled=not awaiting,
                             placeholder="Describe what changed, e.g. 'Database moved to a private multi-AZ deployment with encryption'.")
        use_demo = False
        if demo_available:
            use_demo = st.checkbox("Apply predefined demo revision", key="rev_demo", disabled=not awaiting,
                                   help="Uses the revision bundled with this demo scenario.")
        elif not review.get("simulated"):
            st.caption("The predefined demo revision is only available for demo reviews.")
        if st.button("Create revision", icon=":material/difference:", disabled=not awaiting, key="rev_create"):
            if not (items or notes.strip() or use_demo):
                st.error("Upload revised files, enter change notes or apply the demo revision.")
            elif ui.attempt(api.create_revision, review_id, notes, use_demo, items) is not None:
                st.toast("Revision created. Review the impact below, then run the targeted re-review.")
                st.rerun()


def _revisions_tab(review_id: str, review: dict, status: dict) -> None:
    awaiting = status["status"] == "AWAITING_HUMAN"
    if not awaiting:
        st.caption(f"Revisions can only be submitted while the review is AWAITING_HUMAN (currently {status['status']}).")
    revisions = ui.fetch(api.list_revisions, review_id)
    pending = next((r for r in revisions if r["revision_id"] == status.get("pending_revision_id")), None)
    _revision_form(review_id, review, awaiting)

    ui.section_label("Pending revision")
    if pending is None:
        st.caption("There is no pending revision. Create one above, then run a targeted re-review.")
    else:
        st.markdown(f"**Revision {pending['number']}** ({ui.label(pending.get('source'))}) · {ui.fmt_dt(pending.get('created_at'))}")
        if pending.get("change_notes"):
            st.write(pending["change_notes"])
        if pending.get("impact"):
            _impact_view(pending["impact"])

    reasons = []
    if not awaiting:
        reasons.append(f"the review is {status['status']} (re-review requires AWAITING_HUMAN)")
    elif status["current_round"] >= status["max_rounds"]:
        reasons.append(f"the maximum number of rounds ({status['max_rounds']}) has been reached; finalize the review instead")
    if pending is None:
        reasons.append("there is no pending revision")
    can = bool(status.get("can_re_review"))
    if st.button("Run targeted re-review", type="primary", icon=":material/replay:", disabled=not can, key="re_review"):
        baseline = ui.attempt(api.last_event_seq, review_id)
        if baseline is not None and ui.attempt(api.re_review, review_id, status.get("pending_revision_id")) is not None:
            _start_wait(review_id, "re_review", baseline)
            st.rerun()
    if not can:
        st.caption("Re-review is unavailable: " + "; ".join(reasons or ["no pending revision"]) + ".")
    elif status["pending_decisions"]:
        st.caption(f"{status['pending_decisions']} pending decision(s) are submitted together with the re-review.")

    past = [r for r in revisions if r["revision_id"] != status.get("pending_revision_id")]
    if past:
        with st.expander(f"Revision history ({len(past)})"):
            st.dataframe(pd.DataFrame([{
                "Revision": r["number"], "Source": r["source"], "Created": ui.fmt_dt(r.get("created_at")),
                "Artifacts": len(r.get("artifact_ids") or []), "Change notes": r.get("change_notes") or "",
                "Impact": (r.get("impact") or {}).get("summary", ""),
                "Affected agents": ui.join_or_dash((r.get("impact") or {}).get("affected_agents") or []),
            } for r in past]), hide_index=True, width="stretch")


# --------------------------------------------------------------------------- page
ui.ensure_shell("findings")
ui.page_header("Findings Explorer", "Inspect evidence, record architect decisions, revise the architecture and finalize.")
review = ui.require_review()
review_id = review["review_id"]
ui.simulated_banner(review)
st.markdown(f"**{ui.md_text(review['name'])}**  ·  `{review_id}`")
status = ui.fetch(api.review_status, review_id)
max_disputes = int(ui.fetch(api.config)["settings"].get("max_disputes_per_finding", 2))

_flash(review_id)
wait = st.session_state.get(f"wait_{review_id}")
if wait:
    _wait_panel(review_id, wait)
    st.stop()

if status["status"] == "AWAITING_HUMAN":
    st.info("Record Accept, Dispute or Defer decisions, then submit them. Disputes are answered by the responsible critic and adjudicated "
            "by the moderator before you decide again.", icon=":material/how_to_reg:")
elif status["status"] in ui.RUNNING_STATUSES:
    st.info("The workflow is running. Decisions open when the review reaches AWAITING_HUMAN; follow progress on Review Execution.",
            icon=":material/hourglass_top:")
if status["status"] in ui.COMPLETED_STATUSES:
    ui.not_an_approval_notice()

_action_bar(review_id, status)
tab_findings, tab_revise = st.tabs(["Findings", "Revise architecture"])
with tab_findings:
    _findings_tab(review_id, status, max_disputes)
with tab_revise:
    _revisions_tab(review_id, review, status)
