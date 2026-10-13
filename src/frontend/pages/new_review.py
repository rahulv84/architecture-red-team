"""New Review: choose material (demo scenario or uploads), configure the analysis and start it.

Endpoints: GET /config, /providers, /demo/scenarios, /documents/supported; POST /demo/scenarios/{id}/project,
POST /projects, POST /projects/{id}/documents, GET /projects/{id}/documents, POST /reviews, POST /reviews/{id}/start.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import ui  # noqa: E402

PROJECT_KEY = "nr_project"
ERROR_KEY = "nr_error"
CREATED_KEY = "nr_created_reviews"


def _load_demo(scenario: dict) -> None:
    """Button callback: create the demo project and pre-fill the review details."""
    try:
        result = api.create_demo_project(scenario["scenario_id"])
    except api.ApiError as exc:
        st.session_state[ERROR_KEY] = exc
        return
    st.session_state.pop(ERROR_KEY, None)
    st.session_state[PROJECT_KEY] = {"project": result["project"], "source": "demo", "scenario_id": scenario["scenario_id"],
                                     "rejected": result.get("rejected", [])}
    st.session_state["nr_project_name"] = result["project"]["name"]
    st.session_state["nr_arch_name"] = scenario["name"]
    st.session_state["nr_desc"] = scenario["description"]
    if scenario.get("cloud_environment") in ui.CLOUD_ENVIRONMENTS:
        st.session_state["nr_cloud"] = scenario["cloud_environment"]


def _reset_project() -> None:
    st.session_state.pop(PROJECT_KEY, None)
    st.session_state.pop(ERROR_KEY, None)


def _documents_table(project_id: str, rejected: list[dict]) -> int:
    """Show the parsed documents of the project; return the number of usable (PARSED/PARTIAL) documents."""
    docs = ui.fetch(api.list_documents, project_id)
    if docs:
        frame = pd.DataFrame([{
            "File": d["filename"], "Detected type": d["detected_type"], "Role": d["role"], "Parse status": d["parse_status"],
            "Size (KB)": f"{d['size_bytes'] / 1024:.1f}",
            "Warnings / errors": " | ".join([*d.get("parse_warnings", []), *([d["parse_error"]] if d.get("parse_error") else [])]),
        } for d in docs])
        st.dataframe(ui.style_frame(frame, status=["Parse status"]), hide_index=True, width="stretch")
    else:
        st.caption("No documents accepted yet.")
    if rejected:
        ui.section_label("Rejected files")
        st.dataframe(pd.DataFrame([{"File": r.get("filename"), "Reason": r.get("error"), "Code": r.get("code")} for r in rejected]),
                     hide_index=True, width="stretch")
    return sum(1 for d in docs if d["parse_status"] in ("PARSED", "PARTIAL"))


def _demo_path(scenarios: list[dict]) -> None:
    if not scenarios:
        st.warning("The backend reports no demo scenarios.")
        return
    by_id = {s["scenario_id"]: s for s in scenarios}
    chosen = st.selectbox("Demo scenario", list(by_id), format_func=lambda i: by_id[i]["name"], key="nr_scenario")
    s = by_id[chosen]
    st.markdown(ui.md_text(s["description"]))
    st.html(f'<span class="art-chip" style="color:#334155;background:#e2e8f0">{ui.esc(s.get("cloud_environment"))}</span>'
            f'<span class="art-chip" style="color:#7f1d1d;background:#fee2e2">{ui.esc(s.get("seeded_flaws", 0))} seeded flaws</span>'
            + "".join(f'<span class="art-chip" style="color:#334155;background:#f1f5f9">{ui.esc(f)}</span>' for f in s.get("files", [])))
    rev = s.get("revision") or {}
    if rev.get("title"):
        st.caption(f"Includes a predefined revision for re-review: {rev['title']}.")
    st.button("Load this scenario", icon=":material/download:", on_click=_load_demo, args=(s,), key="nr_load_demo")


def _upload_path(supported: dict) -> None:
    extensions = [e.lstrip(".") for e in supported.get("extensions", [])]
    st.caption("Supported file types: " + ", ".join(f".{e}" for e in extensions))
    files = st.file_uploader("Architecture documents, diagrams and infrastructure code", accept_multiple_files=True,
                             type=extensions or None, key="nr_files")
    items: list[tuple[str, bytes, str]] = []
    primary_taken = False
    for idx, f in enumerate(files or []):
        role = ui.default_document_role(f.name, primary_taken)
        primary_taken = primary_taken or role == "PRIMARY_ARCHITECTURE"
        cols = st.columns([3, 2], vertical_alignment="center")
        cols[0].write(f"{f.name}  ·  {f.size / 1024:.1f} KB")
        picked = cols[1].selectbox("Role", ui.DOCUMENT_ROLES, index=ui.DOCUMENT_ROLES.index(role), key=f"nr_role_{idx}_{f.name}_{f.size}",
                                   label_visibility="collapsed")
        items.append((f.name, f.getvalue(), picked))
    project_name = st.session_state.get("nr_project_name", "").strip()
    can_upload = bool(items) and bool(project_name)
    if items and not project_name:
        st.caption("Enter a project name above to upload.")
    if st.button("Upload and parse", icon=":material/upload:", disabled=not can_upload, key="nr_upload"):
        current = st.session_state.get(PROJECT_KEY)
        with st.spinner("Uploading and parsing documents..."):
            if current and current["source"] == "upload":
                project = current["project"]
            else:
                project = ui.attempt(api.create_project, project_name, st.session_state.get("nr_desc", ""),
                                     st.session_state.get("nr_cloud", "Unknown"))
            result = ui.attempt(api.upload_documents, project["project_id"], items) if project else None
        if project and result is not None:
            previous = current["rejected"] if current and current["source"] == "upload" else []
            st.session_state[PROJECT_KEY] = {"project": project, "source": "upload", "scenario_id": None,
                                             "rejected": previous + result.get("rejected", [])}
            st.rerun()


ui.ensure_shell("new_review")
ui.page_header("New Architecture Review", "Provide architecture material, choose how it is analysed, then start the specialist critics.")
ui.local_only_notice()
if st.session_state.get(ERROR_KEY):
    ui.show_api_error(st.session_state[ERROR_KEY])

config = ui.fetch(api.config)
settings = config["settings"]
providers = ui.fetch(api.providers)
scenarios = ui.fetch(ui.cached_scenarios)
supported = ui.fetch(ui.cached_supported_documents)

ui.section_label("1. Review details")
with st.container(border=True):
    c1, c2 = st.columns(2)
    c1.text_input("Project name", key="nr_project_name", placeholder="e.g. Customer payments platform")
    c2.text_input("Architecture name", key="nr_arch_name", placeholder="e.g. Payments platform v2")
    st.text_area("Description", key="nr_desc", height=80, placeholder="What does this architecture do and what should reviewers know?")
    c3, c4 = st.columns(2)
    st.session_state.setdefault("nr_cloud", "Unknown")
    st.session_state.setdefault("nr_demo", bool(settings.get("demo_mode", True)))
    c3.selectbox("Cloud environment", ui.CLOUD_ENVIRONMENTS, key="nr_cloud")
    demo_mode = c4.toggle("Offline demo mode", key="nr_demo",
                          help="Uses the deterministic mock provider and fixtures; no network calls and no API key.")
    provider_info: dict = {}
    model_id = None
    provider = None
    if demo_mode:
        st.caption("Offline demo mode: provider `mock-deterministic`, no network access. Findings are simulated and clearly marked.")
    else:
        live = [p for p in providers if p["provider"] != "mock"]
        names = [p["provider"] for p in live]
        default_idx = names.index(settings["llm_provider"]) if settings.get("llm_provider") in names else 0
        provider = st.selectbox("Model provider", names, index=default_idx, key="nr_provider",
                                format_func=lambda n: f"{n} ({'configured' if next(p for p in live if p['provider'] == n)['configured'] else 'not configured'})")
        provider_info = next(p for p in live if p["provider"] == provider)
        model_id = st.text_input("Model id", value=provider_info["model_id"], key=f"nr_model_{provider}",
                                 help="Editable; defaults to the model configured on the Settings page.")
        ui.live_mode_warning()
        if not provider_info["configured"]:
            st.warning(f"Provider '{provider}' is not configured: {provider_info['message']} Configure it on the Settings page first.")

ui.section_label("2. Architecture material")
with st.container(border=True):
    tab_demo, tab_upload = st.tabs(["Demo scenario", "Upload files"])
    with tab_demo:
        _demo_path(scenarios)
    with tab_upload:
        _upload_path(supported)

prepared = st.session_state.get(PROJECT_KEY)
usable = 0
if prepared:
    ui.section_label("Prepared project")
    with st.container(border=True):
        top, side = st.columns([4, 1], vertical_alignment="center")
        proj = prepared["project"]
        top.markdown(f"**{ui.md_text(proj['name'])}**  \n`{proj['project_id']}`" + ("  ·  demo scenario" if prepared["source"] == "demo" else ""))
        side.button("Discard", on_click=_reset_project, key="nr_discard", width="stretch")
        usable = _documents_table(proj["project_id"], prepared.get("rejected", []))
        if usable == 0:
            st.warning("No document was parsed successfully; a review needs at least one parsed document.")
        if prepared["source"] == "upload" and demo_mode:
            st.info("Offline demo mode only generates findings for the bundled demo scenarios. For your own files it records deterministic "
                    "tool observations but produces no findings; turn demo mode off to analyse them with a live model.",
                    icon=":material/info:")

ui.section_label("3. Analysis options")
with st.container(border=True):
    agent_ids = config.get("agents") or list(ui.AGENT_IDS)
    agents = st.multiselect("Specialist agents", agent_ids, default=agent_ids, format_func=lambda a: ui.AGENT_LABELS.get(a, a),
                            key="nr_agents")
    max_rounds = st.number_input("Maximum review rounds", min_value=1, max_value=10, value=int(settings.get("max_review_rounds", 3)),
                                 step=1, key="nr_rounds", help="Each targeted re-review after an architecture revision uses one round.")

ui.section_label("4. Start")
ready = bool(prepared) and usable > 0 and bool(agents) and bool(st.session_state.get("nr_arch_name", "").strip())
if not demo_mode and provider_info and not provider_info.get("configured"):
    ready = False
missing: list[str] = []
if not prepared:
    missing.append("load a demo scenario or upload files")
if not st.session_state.get("nr_arch_name", "").strip():
    missing.append("enter an architecture name")
if not agents:
    missing.append("select at least one specialist agent")
if missing:
    st.caption("To start: " + "; ".join(missing) + ".")

if st.button("Start Review", type="primary", icon=":material/play_arrow:", disabled=not ready, key="nr_start"):
    project_id = prepared["project"]["project_id"]
    created = st.session_state.setdefault(CREATED_KEY, {})
    review_id = created.get(project_id)
    with st.spinner("Creating and starting the review..."):
        if review_id is None:
            payload = {
                "project_id": project_id, "name": st.session_state["nr_arch_name"].strip(),
                "description": st.session_state.get("nr_desc", ""), "cloud_environment": st.session_state.get("nr_cloud", "Unknown"),
                "demo_mode": demo_mode, "provider": provider, "model_id": model_id or None,
                "scenario_id": prepared.get("scenario_id"), "enabled_agents": agents, "max_rounds": int(max_rounds),
            }
            review = ui.attempt(api.create_review, payload)
            if review:
                review_id = review["review_id"]
                created[project_id] = review_id
        started = ui.attempt(api.start_review, review_id) if review_id else None
    if started is not None and review_id:
        st.session_state["started_" + review_id] = time.time()
        st.session_state.pop(PROJECT_KEY, None)
        created.pop(project_id, None)
        ui.set_review_id(review_id)
        ui.goto("review_progress")
    elif review_id:
        ui.set_review_id(review_id)
        st.warning(f"Review {review_id} was created but could not be started. Press Start Review again to retry, "
                   "or use Start on the Review Execution page.")
