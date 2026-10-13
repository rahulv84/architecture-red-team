"""Dashboard: portfolio totals, risk distribution and recent reviews (GET /health, /config, /projects, /reviews)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import ui  # noqa: E402

RECENT_LIMIT = 8
IN_PROGRESS_LABEL = "EXTRACTING / ANALYZING / MODERATING / REBUTTING / RE_REVIEWING"


def _totals(reviews: list[dict], projects: list[dict]) -> list[tuple[str, object, str]]:
    in_progress = sum(1 for r in reviews if r["status"] in ui.RUNNING_STATUSES)
    awaiting = sum(1 for r in reviews if r["status"] == "AWAITING_HUMAN")
    completed = sum(1 for r in reviews if r["status"] in ui.COMPLETED_STATUSES)
    open_ch = sum(r.get("open_critical_high", 0) for r in reviews)
    return [
        ("Projects", len(projects), ""), ("Reviews", len(reviews), f"{sum(1 for r in reviews if r.get('simulated'))} simulated demo"),
        ("In progress", in_progress, "agents or moderator running"), ("Awaiting decisions", awaiting, "human decisions needed"),
        ("Completed", completed, "incl. completed with open risks"), ("Open Critical + High", open_ch, "unresolved across reviews"),
    ]


def _domain_chart(reviews: list[dict]) -> None:
    counts = {a: sum(r.get("open_by_domain", {}).get(a, 0) for r in reviews) for a in ui.AGENT_IDS}
    frame = pd.DataFrame({"Domain": [ui.AGENT_LABELS[a] for a in counts], "Open findings": list(counts.values())})
    chart = alt.Chart(frame).mark_bar(color="#475569", cornerRadiusEnd=3).encode(
        y=alt.Y("Domain:N", sort=None, title=None), x=alt.X("Open findings:Q", title="Open findings", axis=alt.Axis(tickMinStep=1)),
        tooltip=["Domain", "Open findings"],
    ).properties(height=220)
    st.altair_chart(chart, width="stretch")


def _severity_chart(reviews: list[dict]) -> None:
    counts = {s: sum(r.get("severity_counts", {}).get(s, 0) for r in reviews) for s in ui.SEVERITIES}
    frame = pd.DataFrame({"Severity": list(counts), "Findings": list(counts.values())})
    chart = alt.Chart(frame).mark_bar(cornerRadiusEnd=3).encode(
        y=alt.Y("Severity:N", sort=list(ui.SEVERITIES), title=None), x=alt.X("Findings:Q", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("Severity:N", scale=alt.Scale(domain=list(ui.SEVERITIES), range=[ui.SEVERITY_COLORS[s] for s in ui.SEVERITIES]),
                        legend=None),
        tooltip=["Severity", "Findings"],
    ).properties(height=220)
    st.altair_chart(chart, width="stretch")


def _recent_reviews(reviews: list[dict]) -> None:
    head = st.columns([3.2, 1.7, 0.9, 1.1, 1.8, 1.0])
    for col, text in zip(head, ["Review", "Status", "Round", "Open C+H", "Updated", ""]):
        col.caption(text.upper())
    for r in reviews[:RECENT_LIMIT]:
        c = st.columns([3.2, 1.7, 0.9, 1.1, 1.8, 1.0], vertical_alignment="center")
        demo = ui.simulated_chip() if r.get("simulated") else ""
        c[0].markdown(f"**{ui.md_text(r['name'])}**")
        c[0].caption(r["review_id"])
        c[1].html(ui.status_badge(r["status"]) + demo)
        c[2].write(f"{r['current_round']}/{r['max_rounds']}")
        c[3].write(r.get("open_critical_high", 0))
        c[4].write(ui.fmt_dt(r.get("updated_at")))
        if c[5].button("Open", key=f"open_{r['review_id']}", width="stretch"):
            ui.open_review(r["review_id"], r["status"])


ui.ensure_shell("dashboard")
ui.page_header("Dashboard", "Architecture governance overview: reviews, open risk and decisions awaiting an architect.")
ui.local_only_notice()
health = ui.fetch(api.health)
config = ui.fetch(api.config)
reviews = ui.fetch(api.list_reviews)
projects = ui.fetch(api.list_projects)

active_id = ui.get_review_id()
active = next((r for r in reviews if r["review_id"] == active_id), None)
ui.simulated_banner(active)

left, right = st.columns([3, 1], vertical_alignment="center")
with left:
    mode = "offline demo mode" if health.get("demo_mode_default") else "live-model mode"
    st.html(f'{ui.status_badge("COMPLETED", "BACKEND " + str(health.get("status", "?")).upper())}'
            f'<span class="art-chip" style="color:#334155;background:#e2e8f0">v{ui.esc(health.get("version"))}</span>'
            f'<span class="art-chip" style="color:#334155;background:#e2e8f0">default provider: {ui.esc(health.get("provider"))}</span>'
            f'<span class="art-chip" style="color:{"#7f1d1d" if health.get("demo_mode_default") else "#92400e"};'
            f'background:{"#fee2e2" if health.get("demo_mode_default") else "#fef3c7"}">default: {ui.esc(mode)}</span>')
    checkov = config.get("tools", {})
    st.caption(f"Checkov: {'installed' if checkov.get('checkov_installed') else 'not installed (its coverage is reported as not executed)'}")
with right:
    if st.button("New Architecture Review", type="primary", icon=":material/add:", width="stretch"):
        ui.goto("new_review")

ui.metric_cards(_totals(reviews, projects))
st.space("small")

if not reviews:
    st.info("No reviews yet. Create one with the button above; the offline demo scenarios need no API key.", icon=":material/info:")
    st.stop()

scope_options = ["All reviews"] + (["Active review"] if active else [])
scope = st.radio("Risk distribution scope", scope_options, horizontal=True) if len(scope_options) > 1 else scope_options[0]
scoped = [active] if scope == "Active review" and active else reviews
ui.section_label("Risk distribution")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Open findings by domain**")
    _domain_chart(scoped)
with c2:
    st.markdown("**Findings by severity** (excluding moderator-rejected)")
    _severity_chart(scoped)
if any(r.get("simulated") for r in scoped):
    st.caption("Counts include simulated demo reviews; their findings are fixture-generated.")

ui.section_label("Recent reviews")
_recent_reviews(reviews)

with st.expander(f"All reviews ({len(reviews)})"):
    table = pd.DataFrame([{
        "Review": r["name"], "ID": r["review_id"], "Status": r["status"], "Mode": "demo" if r.get("simulated") else "live",
        "Round": f"{r['current_round']}/{r['max_rounds']}", "Findings": r.get("finding_count", 0),
        "Open C+H": r.get("open_critical_high", 0), "Created": ui.fmt_dt(r.get("created_at")), "Updated": ui.fmt_dt(r.get("updated_at")),
    } for r in reviews])
    st.dataframe(ui.style_frame(table, status=["Status"]), hide_index=True, width="stretch")
st.caption(f"In progress means status {IN_PROGRESS_LABEL}.")
