"""Reports: assessment overview, scorecard, findings views, ADRs, evaluation harness and downloadable deliverables.

Endpoints: GET /reviews/{id}/report-preview, /score, /adrs, /reports, /evaluation; POST /reviews/{id}/reports;
GET /reports/{id}/download.
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import ui  # noqa: E402

CLOSED = ui.CLOSED_FINDING_STATUSES
UNDECIDED = ("OPEN", "DISPUTED", "REBUTTED", "REMEDIATION_PROPOSED")
SEVERITY_SCALE = alt.Scale(domain=list(ui.SEVERITIES), range=[ui.SEVERITY_COLORS[s] for s in ui.SEVERITIES])


def _visible(report: dict) -> list[dict]:
    """Findings that count (not rejected by the moderator or the architect)."""
    return [f for f in report["findings"] if f["moderator_status"] != "REJECTED" and f["status"] != "REJECTED"]


def _finding_rows(findings: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Severity": f["severity"], "Title": f["title"], "Domain": ui.AGENT_LABELS.get(f["domain"], f["domain"]), "Status": f["status"],
        "Evidence": f["evidence_level"], "Components": ui.join_or_dash(f.get("affected_components") or []),
        "Remaining risk": f.get("remaining_risk", ""),
    } for f in findings], columns=["Severity", "Title", "Domain", "Status", "Evidence", "Components", "Remaining risk"])


def _table(findings: list[dict], empty: str) -> None:
    if not findings:
        st.caption(empty)
        return
    st.dataframe(ui.style_frame(_finding_rows(findings), severity=["Severity"], status=["Status", "Evidence"]), hide_index=True, width="stretch")


# --------------------------------------------------------------------------- sections
def _overview(report: dict) -> None:
    summary = report["executive_summary"]
    ui.not_an_approval_notice()
    counts = summary.get("counts", {})
    ui.metric_cards([
        ("Review status", ui.label(report["review_status"]), f"revision {report['architecture_revision']}"),
        ("Findings", counts.get("findings", 0), f"{counts.get('rejected', 0)} rejected"),
        ("Open", counts.get("open", 0), "not resolved or rejected"),
        ("Overall band", report["scorecard"]["overall_band"], "weakest assessed domain"),
    ])
    st.space("small")
    ui.section_label("Overall assessment")
    st.write(summary.get("overall_assessment"))
    st.markdown(ui.md_text(summary.get("narrative")))
    ui.section_label("Review coverage")
    st.write(summary.get("review_coverage"))
    coverage = report.get("coverage", {})
    agents = coverage.get("agents") or []
    if agents:
        st.dataframe(ui.style_frame(pd.DataFrame([{
            "Agent": a["label"], "Round": a["round"], "Status": a["status"], "Findings": a["findings"],
            "Tool executions": a["tool_count"], "Violations": a["tool_violations"], "Error": a.get("error") or "",
        } for a in agents]), status=["Status"]), hide_index=True, width="stretch")
    tools = coverage.get("tools") or []
    if tools:
        st.dataframe(ui.style_frame(pd.DataFrame([{
            "Tool": t.get("tool_name"), "Status": t.get("status"), "Violations": t.get("violations"), "Detail": t.get("detail") or "",
        } for t in tools]), status=["Status"]), hide_index=True, width="stretch")
    ui.section_label("Major risks")
    risks = summary.get("major_risks") or []
    by_id = {f["finding_id"]: f for f in report["findings"]}
    if risks:
        for r in risks:
            ui.finding_card(by_id.get(r["finding_id"], r))
    else:
        st.caption("No open Critical or High findings.")
    left, right = st.columns(2)
    with left:
        ui.section_label("Areas requiring clarification")
        items = summary.get("clarifications") or []
        st.markdown("\n".join(f"- {ui.md_text(i)}" for i in items) if items else "_None identified._")
    with right:
        ui.section_label("Recommendations")
        items = summary.get("recommendations") or []
        st.markdown("\n".join(f"- {ui.md_text(i)}" for i in items) if items else "_None._")


def _scorecard(score: dict) -> None:
    ui.not_an_approval_notice()
    ui.emit_html(f'<div class="art-card"><div class="t">Overall band: {ui.esc(score["overall_band"])}</div>'
                 f'<div class="meta">{ui.esc(score["overall_statement"])}</div></div>')
    domains = score["domains"]
    st.dataframe(ui.style_frame(pd.DataFrame([{
        "Domain": d["label"], "Score": d["score"], "Confirmed-only score": d["confirmed_only_score"], "Band": d["band"],
        "Coverage": d["coverage"], "Open findings": d["open_findings"], "Confirmed": d["confirmed_findings"],
        "Unverified": d["unverified_findings"],
    } for d in domains]), status=["Coverage"]), hide_index=True, width="stretch")
    long = pd.DataFrame([{"Domain": d["label"], "Series": name, "Score": d[key]}
                         for d in domains for name, key in (("Score", "score"), ("Confirmed-only", "confirmed_only_score"))])
    chart = alt.Chart(long).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("Domain:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)), xOffset="Series:N",
        y=alt.Y("Score:Q", scale=alt.Scale(domain=[0, 100]), title="Score (0-100, indicative)"),
        color=alt.Color("Series:N", scale=alt.Scale(domain=["Score", "Confirmed-only"], range=["#475569", "#1d4ed8"])),
        tooltip=["Domain", "Series", "Score"],
    ).properties(height=260)
    st.altair_chart(chart, width="stretch")
    ui.section_label("Methodology")
    st.markdown(ui.md_text(score["methodology"]))
    st.caption("Scores are rounded indications of unresolved risk in the supplied material. They are not an approval, a certification or a "
               "prediction. The confirmed-only score ignores findings that still need validation.")
    ui.section_label("Domain explanations and contributions")
    for d in domains:
        with st.expander(f"{d['label']} · {d['score']} · {d['band']} · coverage {d['coverage']}"):
            st.write(d["explanation"])
            if d.get("information_gaps"):
                st.markdown("**Information gaps**\n" + "\n".join(f"- {ui.md_text(g)}" for g in d["information_gaps"]))
            if d.get("contributions"):
                st.dataframe(ui.style_frame(pd.DataFrame([{
                    "Finding": c["title"], "Severity": c["severity"], "Evidence": c["evidence_level"], "Status": c["status"],
                    "Penalty": c["penalty"], "Counted as": c["counted_as"],
                } for c in d["contributions"]]), severity=["Severity"], status=["Status", "Evidence"]), hide_index=True, width="stretch")
            else:
                st.caption("No findings contribute to this domain's score.")


def _findings_views(report: dict) -> None:
    visible = _visible(report)
    left, right = st.columns(2)
    sev = pd.DataFrame({"Severity": list(ui.SEVERITIES), "Findings": [sum(1 for f in visible if f["severity"] == s) for s in ui.SEVERITIES]})
    with left:
        st.markdown("**Severity distribution**")
        st.altair_chart(alt.Chart(sev).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("Severity:N", sort=list(ui.SEVERITIES), axis=alt.Axis(labelAngle=0)), y=alt.Y("Findings:Q", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Severity:N", scale=SEVERITY_SCALE, legend=None), tooltip=["Severity", "Findings"]).properties(height=240),
            width="stretch")
    with right:
        st.markdown("**Findings by domain and severity**")
        by_domain = pd.DataFrame([{"Domain": ui.AGENT_LABELS[a], "Severity": s,
                                   "Findings": sum(1 for f in visible if f["domain"] == a and f["severity"] == s)}
                                  for a in ui.AGENT_IDS for s in ui.SEVERITIES])
        st.altair_chart(alt.Chart(by_domain).mark_bar().encode(
            x=alt.X("Domain:N", sort=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("Findings:Q", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color("Severity:N", scale=SEVERITY_SCALE, legend=alt.Legend(orient="bottom")),
            tooltip=["Domain", "Severity", "Findings"]).properties(height=240), width="stretch")

    ui.section_label("Domain-specific findings")
    tabs = st.tabs([f"{ui.AGENT_LABELS[a]} ({sum(1 for f in visible if f['domain'] == a)})" for a in ui.AGENT_IDS])
    for agent, tab in zip(ui.AGENT_IDS, tabs):
        with tab:
            _table([f for f in visible if f["domain"] == agent], f"No {ui.AGENT_LABELS[agent]} findings.")

    ui.section_label("Accepted and deferred risks")
    st.caption("ACCEPTED: the architect agreed the finding is valid and remediation is outstanding. DEFERRED: remediation postponed "
               "with a justification, owner and review date.")
    risks = [f for f in visible if f["status"] in ("ACCEPTED", "DEFERRED")]
    if risks:
        rows = []
        for f in risks:
            last = next((d for d in reversed(f.get("architect_decisions") or []) if d["decision_type"] in ("ACCEPT", "DEFER")), {})
            rows.append({"Severity": f["severity"], "Title": f["title"], "Status": f["status"],
                         "Justification / notes": last.get("justification") or last.get("notes") or "",
                         "Risk owner": last.get("risk_owner") or "", "Review date": last.get("review_date") or "",
                         "Remediation target": last.get("remediation_target") or "", "Remaining risk": f["remaining_risk"]})
        st.dataframe(ui.style_frame(pd.DataFrame(rows), severity=["Severity"], status=["Status"]), hide_index=True, width="stretch")
    else:
        st.caption("No findings have been accepted or deferred.")

    ui.section_label("Outstanding findings")
    st.caption("Open findings that still need an architect decision or a critic rebuttal outcome.")
    _table([f for f in visible if f["status"] in UNDECIDED], "No findings are awaiting a decision.")


def _history(report: dict) -> None:
    history = report["review_history"]
    ui.metric_cards([("Rounds", f"{history['rounds']} / {history['max_rounds']}", "review rounds used"),
                     ("Revisions", len(history["revisions"]), ""), ("Events", history["event_count"], "recorded"),
                     ("Decisions", len(report["decisions"]), "architect decisions")])
    st.space("small")
    ui.section_label("Architecture revisions")
    st.dataframe(pd.DataFrame([{
        "Revision": r["number"], "Source": r["source"], "Created": ui.fmt_dt(r["created_at"]), "Change notes": r.get("change_notes") or "",
        "Impact": r.get("impact_summary") or "", "Affected agents": ui.join_or_dash(r.get("affected_agents") or []),
        "Confidence": ui.na(r.get("confidence")), "Artifacts": len(r.get("artifact_ids") or []),
    } for r in history["revisions"]]), hide_index=True, width="stretch")
    ui.section_label("Architect decisions")
    if report["decisions"]:
        titles = {f["finding_id"]: f["title"] for f in report["findings"]}
        frame = ui.decisions_frame(report["decisions"])
        frame.insert(1, "Finding", [titles.get(d["finding_id"], d["finding_id"]) for d in report["decisions"]])
        st.dataframe(frame, hide_index=True, width="stretch")
    else:
        st.caption("No architect decisions recorded.")
    ui.section_label("Timeline")
    ui.event_timeline([{"timestamp": t["time"], "message": t["message"], "agent_id": t.get("agent"), "level": "INFO"}
                       for t in history.get("timeline", [])], newest_first=False, height=320)


def _adrs(review_id: str) -> None:
    adrs = ui.fetch(api.list_adrs, review_id)
    if not adrs:
        st.info("No architecture decision records exist yet. Deferred risks and meaningful decisions produce ADR drafts.",
                icon=":material/info:")
        return
    st.caption(f"{len(adrs)} ADR draft(s) generated from architect decisions. Review and adopt them through your own process.")
    for adr in sorted(adrs, key=lambda a: a["number"]):
        with st.expander(f"ADR-{adr['number']:03d} · {adr['title']} · {adr['status']}"):
            st.caption(f"Owner: {adr.get('owner') or 'Unassigned'} · related findings: {ui.join_or_dash(adr.get('related_finding_ids') or [])}")
            st.markdown(ui.md_text(adr.get("markdown") or "_No content._"))


def _evaluation(review_id: str) -> None:
    ev = ui.fetch(api.review_evaluation, review_id)
    st.warning("Observed on the included fixtures only. These figures describe this run against seeded expectations; they are not "
               "accuracy claims for other architectures.", icon=":material/science:")
    st.caption(ev.get("note") or "")
    ui.metric_cards([
        ("Precision", ui.na(ev.get("precision")), "matched / considered findings"), ("Recall", ui.na(ev.get("recall")), "seeded flaws found"),
        ("Duplicates merged", ev.get("duplicates_merged", "N/A"), f"{ev.get('candidate_findings', 'N/A')} candidates"),
        ("Unsupported findings", ev.get("unsupported_findings", "N/A"), "rejected or needing validation"),
    ])
    st.space("small")
    ui.metric_cards([
        ("Distinct findings", ev.get("distinct_findings", "N/A"), ""), ("Residual duplicates", ev.get("residual_duplicates", "N/A"), ""),
        ("Review rounds", ev.get("review_rounds", "N/A"), ""),
        ("Execution time", "N/A" if ev.get("execution_seconds") is None else f"{ev['execution_seconds']} s", "start to moderation"),
    ])
    inv = ev.get("agent_invocations") or {}
    if inv:
        ui.section_label("Agent invocations")
        st.dataframe(pd.DataFrame([{"Agent": ui.AGENT_LABELS.get(a, a), "Invocations": n} for a, n in inv.items()]),
                     hide_index=True, width="stretch")
    if ev.get("labeled"):
        left, right = st.columns(2)
        with left:
            ui.section_label(f"Matched seeded flaws ({len(ev.get('matched', []))})")
            st.write(", ".join(ev.get("matched") or []) or "—")
            ui.section_label(f"Missed seeded flaws ({len(ev.get('missed', []))})")
            st.write(", ".join(ev.get("missed") or []) or "None")
        with right:
            ui.section_label(f"Findings without a seeded match ({len(ev.get('unexpected', []))})")
            st.markdown("\n".join(f"- {ui.md_text(t)}" for t in ev.get("unexpected") or []) or "_None_")
        tools = ev.get("deterministic_tool_detection") or {}
        if tools:
            ui.section_label("Deterministic tool detection")
            st.write(f"{len(tools.get('detected_by_tools', []))} of {tools.get('flaws_with_tool_rules', 0)} seeded flaws with tool rules "
                     "were detected by deterministic tools.")
        if ev.get("seeded_unsupported"):
            ui.section_label("Seeded unsupported claims")
            st.dataframe(pd.DataFrame([{"Flaw": s["flaw_id"], "Claim contains": s["title_contains"],
                                        "Handled (rejected or needs validation)": "yes" if s["handled"] else "no"}
                                       for s in ev["seeded_unsupported"]]), hide_index=True, width="stretch")


def _deliverables(review_id: str) -> None:
    st.caption("Reports are snapshots of the current state. Generate again after new decisions or revisions. "
               "Finalizing a review generates them automatically.")
    if st.button("Generate PDF + JSON report", type="primary", icon=":material/picture_as_pdf:", key="generate_reports"):
        with st.spinner("Generating reports ..."):
            made = ui.attempt(api.generate_reports, review_id)
        if made is not None:
            st.toast(f"Generated {len(made)} report file(s).")
            st.rerun()
    reports = ui.fetch(api.list_reports, review_id)
    if not reports:
        st.info("No report files have been generated for this review yet.", icon=":material/info:")
        return
    head = st.columns([4, 1, 1, 1.4, 1.4])
    for col, text in zip(head, ["File", "Format", "Revision", "Created", ""]):
        col.caption(text.upper())
    for r in reports:
        c = st.columns([4, 1, 1, 1.4, 1.4], vertical_alignment="center")
        c[0].write(f"{r['filename']}  ·  {r['size_bytes'] / 1024:.1f} KB")
        c[1].write(r["format"].upper())
        c[2].write(r["revision"])
        c[3].write(ui.fmt_dt(r["created_at"]))
        c[4].download_button("Download", data=partial(api.download_report, r["report_id"]), file_name=r["filename"],
                             mime="application/pdf" if r["format"] == "pdf" else "application/json",
                             key=f"dl_{r['report_id']}", on_click="ignore", width="stretch")


ui.ensure_shell("reports")
ui.page_header("Reports", "Assessment overview, scorecard, decisions and downloadable deliverables for the selected review.")
review = ui.require_review()
review_id = review["review_id"]
ui.simulated_banner(review)
ui.emit_html(f'<b>{ui.esc(review["name"])}</b> &nbsp; <code>{ui.esc(review_id)}</code> &nbsp; {ui.status_badge(review["status"])}')

report = ui.fetch(api.report_preview, review_id)
score = ui.fetch(api.review_score, review_id)
tabs = st.tabs(["Overview", "Scorecard", "Findings", "Review history", "ADRs", "Evaluation harness", "Deliverables"])
with tabs[0]:
    _overview(report)
with tabs[1]:
    _scorecard(score)
with tabs[2]:
    _findings_views(report)
with tabs[3]:
    _history(report)
with tabs[4]:
    _adrs(review_id)
with tabs[5]:
    _evaluation(review_id)
with tabs[6]:
    _deliverables(review_id)
with st.expander("Limitations and disclaimers"):
    st.markdown("\n".join(f"- {ui.md_text(t)}" for t in [*report.get("limitations", []), *report.get("disclaimers", [])]))
