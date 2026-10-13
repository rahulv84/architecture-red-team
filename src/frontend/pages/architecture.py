"""Architecture Explorer: the extracted architecture model and its source documents.

Endpoints: GET /reviews/{id}/architecture?revision=, GET /documents/{id}/content.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import graphs, ui  # noqa: E402

PROVENANCE_MARK = {"EXPLICIT": "explicit", "INFERRED": "inferred", "UNKNOWN": "unknown"}
TEXT_LIMITS = {"60,000 characters": 60_000, "200,000 characters": 200_000, "500,000 characters": 500_000}


def _value_text(value: object, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("yes" if value else "no")


def _provenance(component: dict, key: str) -> str:
    return component.get("provenance", {}).get(key, "EXPLICIT" if key in component.get("configuration", {}) else "UNKNOWN")


def _config_summary(component: dict) -> str:
    cfg = component.get("configuration") or {}
    return "; ".join(f"{k}={_value_text(v, 60)} [{PROVENANCE_MARK.get(_provenance(component, k), '?')}]" for k, v in cfg.items()) or "—"


def _short_refs(refs: list[dict], limit: int = 3) -> str:
    names = [f"{r.get('filename') or r.get('artifact_id')} {ui.location_text(r)}".strip() for r in refs[:limit]]
    more = f" (+{len(refs) - limit} more)" if len(refs) > limit else ""
    return "; ".join(names) + more if names else "—"


def _components_tab(model: dict, docs: dict[str, dict]) -> None:
    components = model.get("components", [])
    if not components:
        st.info("No components were extracted from the supplied material.", icon=":material/info:")
        return
    a, b, c = st.columns([2, 2, 3])
    types = a.multiselect("Type", sorted({c_["type"] for c_ in components}), key="arch_types")
    providers = b.multiselect("Provider", sorted({c_["provider"] for c_ in components}), key="arch_providers")
    query = c.text_input("Search id, name or description", key="arch_query").strip().lower()
    shown = [c_ for c_ in components
             if (not types or c_["type"] in types) and (not providers or c_["provider"] in providers)
             and (not query or query in f"{c_['component_id']} {c_['name']} {c_.get('description', '')}".lower())]
    st.caption(f"{len(shown)} of {len(components)} component(s)")
    if not shown:
        return
    frame = pd.DataFrame([{
        "ID": c_["component_id"], "Name": c_["name"], "Type": c_["type"], "Provider": c_["provider"],
        "Confidence": round(c_.get("confidence", 0), 2), "Dependencies": ui.join_or_dash(c_.get("dependencies") or []),
        "Configuration": _config_summary(c_), "Sources": _short_refs(c_.get("source_references") or []),
    } for c_ in shown])
    event = st.dataframe(frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         height=min(420, 38 + 35 * len(shown)), key=f"arch_components_{len(shown)}_{hash((tuple(types), tuple(providers), query))}")
    rows = event.selection.rows if event and event.selection else []
    if rows:
        st.session_state["arch_component"] = shown[rows[0]]["component_id"]
    selected = next((c_ for c_ in shown if c_["component_id"] == st.session_state.get("arch_component")), shown[0])
    _component_detail(selected, model, docs)


def _component_detail(component: dict, model: dict, docs: dict[str, dict]) -> None:
    st.divider()
    st.markdown(f"### {ui.md_text(component['name'])}")
    st.caption(f"`{component['component_id']}` · {component['type']} · provider {component['provider']} · "
               f"confidence {ui.pct(component.get('confidence'))}" + (f" · zone {component['zone']}" if component.get("zone") else ""))
    if component.get("description"):
        st.write(component["description"])
    ui.section_label("Configuration and provenance")
    cfg = component.get("configuration") or {}
    if cfg:
        frame = pd.DataFrame([{"Property": k, "Value": _value_text(v, 400), "Provenance": _provenance(component, k)} for k, v in cfg.items()])
        st.dataframe(ui.style_frame(frame, status=["Provenance"]), hide_index=True, width="stretch")
    else:
        st.caption("No configuration properties were extracted.")
    names = {c["component_id"]: c["name"] for c in model.get("components", [])}
    outgoing = [r for r in model.get("relationships", []) if r["source"] == component["component_id"]]
    incoming = [r for r in model.get("relationships", []) if r["target"] == component["component_id"]]
    left, right = st.columns(2)
    with left:
        ui.section_label("Depends on")
        deps = component.get("dependencies") or []
        st.write(", ".join(names.get(d, d) for d in deps) if deps else "—")
        st.caption("Outgoing: " + (", ".join(f"{r['relationship_type']} → {names.get(r['target'], r['target'])}" for r in outgoing[:12]) or "none"))
    with right:
        ui.section_label("Used by")
        st.write(", ".join(f"{names.get(r['source'], r['source'])} ({r['relationship_type']})" for r in incoming[:12]) or "—")
    refs = component.get("source_references") or []
    ui.section_label(f"Supporting source artifacts ({len(refs)})")
    used = sorted({docs[r["artifact_id"]]["filename"] for r in refs if r.get("artifact_id") in docs})
    if used:
        st.caption("Documents: " + ", ".join(used))
    ui.render_source_references(refs)


def _relationships_tab(model: dict) -> None:
    rels = model.get("relationships", [])
    if not rels:
        st.info("No relationships were extracted.", icon=":material/info:")
        return
    names = {c["component_id"]: c["name"] for c in model.get("components", [])}
    types = st.multiselect("Relationship type", sorted({r["relationship_type"] for r in rels}), key="arch_rel_types")
    shown = [r for r in rels if not types or r["relationship_type"] in types]
    st.caption(f"{len(shown)} of {len(rels)} relationship(s)")
    frame = pd.DataFrame([{
        "ID": r["relationship_id"], "Source": names.get(r["source"], r["source"]), "Type": r["relationship_type"],
        "Target": names.get(r["target"], r["target"]), "Label": r.get("label") or "", "Protocol": r.get("protocol") or "",
        "Synchronous": _yes_no(r.get("synchronous")),
        "Provenance": r.get("provenance"), "Evidence": _short_refs(r.get("evidence") or []),
    } for r in shown])
    st.dataframe(ui.style_frame(frame, status=["Provenance"]), hide_index=True, width="stretch")


def _graph_tab(model: dict) -> None:
    if not model.get("relationships"):
        st.info("There are no relationships to draw.", icon=":material/info:")
        return
    a, b, c = st.columns([2, 2, 1])
    show_hidden = a.toggle("Show network-membership and identity-usage edges", value=False, key="arch_show_hidden",
                           help="These edges are hidden by default because they dominate larger graphs.")
    names = {c_["component_id"]: c_["name"] for c_ in model.get("components", [])}
    focus = b.selectbox("Focus on a component", [None, *sorted(names, key=lambda i: names[i].lower())], key="arch_focus",
                        format_func=lambda i: "Whole architecture" if i is None else f"{names[i]} ({i})")
    hops = c.number_input("Hops", min_value=1, max_value=4, value=1, key="arch_hops", disabled=focus is None)
    hidden = () if show_hidden else graphs.HIDDEN_BY_DEFAULT
    dot, counts = graphs.dependency_dot(model, hidden, focus=focus, hops=int(hops))
    if counts["nodes"] == 0:
        st.info("No components remain after filtering. Enable the hidden edge types to see every relationship.")
        return
    st.graphviz_chart(dot, width="stretch")
    ui.legend([(group, fill, border) for group, (fill, border, _) in graphs.TYPE_GROUPS.items()])
    st.caption(f"{counts['nodes']} component(s) and {counts['edges']} edge(s) drawn; {counts['hidden_edges']} hidden edge(s); "
               f"{counts['isolated']} component(s) without a visible edge omitted. Dashed edges are inferred.")


def _list_section(title: str, values: list[str]) -> None:
    ui.section_label(f"{title} ({len(values)})")
    if values:
        st.markdown("\n".join(f"- {ui.md_text(v)}" for v in values))
    else:
        st.caption("None stated.")


def _requirements_tab(model: dict) -> None:
    ui.section_label(f"Non-functional requirements ({len(model.get('nfrs', []))})")
    if model.get("nfrs"):
        st.dataframe(ui.style_frame(pd.DataFrame([{
            "Category": n["category"], "Statement": n["statement"], "Value": n.get("value") or "", "Provenance": n.get("provenance"),
            "Sources": _short_refs(n.get("source_references") or []),
        } for n in model["nfrs"]]), status=["Provenance"]), hide_index=True, width="stretch")
    else:
        st.caption("None stated.")
    ui.section_label(f"Constraints ({len(model.get('constraints', []))})")
    if model.get("constraints"):
        st.dataframe(pd.DataFrame([{"Category": c["category"], "Statement": c["statement"],
                                    "Sources": _short_refs(c.get("source_references") or [])} for c in model["constraints"]]),
                     hide_index=True, width="stretch")
    else:
        st.caption("None stated.")
    ui.section_label(f"Assumptions ({len(model.get('assumptions', []))})")
    if model.get("assumptions"):
        st.dataframe(pd.DataFrame([{"Statement": a["statement"], "Reason": a.get("reason") or "",
                                    "Confidence": ui.pct(a.get("confidence"))} for a in model["assumptions"]]),
                     hide_index=True, width="stretch")
    else:
        st.caption("None recorded.")
    left, right = st.columns(2)
    with left:
        _list_section("Security controls", model.get("security_controls", []))
        _list_section("Authentication mechanisms", model.get("auth_mechanisms", []))
        _list_section("Deployment regions", model.get("deployment_regions", []))
    with right:
        _list_section("Operational tooling", model.get("operational_tooling", []))
        _list_section("Availability and DR statements", model.get("availability_dr", []))
        _list_section("Expected workload", model.get("expected_workload", []))
    flows = model.get("data_flows", [])
    if flows:
        ui.section_label(f"Data flows ({len(flows)})")
        st.dataframe(pd.DataFrame([{
            "Name": f["name"], "Source": f["source"], "Destination": f["destination"], "Classification": f.get("data_classification") or "",
            "Protocol": f.get("protocol") or "", "Encrypted in transit": _yes_no(f.get("encrypted_in_transit")),
            "Crosses trust boundary": _yes_no(f.get("crosses_trust_boundary")),
        } for f in flows]), hide_index=True, width="stretch")
    _list_section("Unresolved information", model.get("unresolved", []))


def _notes_tab(model: dict, docs: dict[str, dict]) -> None:
    notes = sorted(model.get("notes", []), key=lambda n: {"LIMITATION": 0, "WARNING": 1}.get(n.get("level"), 2))
    if not notes:
        st.success("The extraction stage recorded no notes or limitations.", icon=":material/check_circle:")
        return
    limitations = sum(1 for n in notes if n.get("level") == "LIMITATION")
    st.caption(f"{len(notes)} note(s), {limitations} limitation(s). Limitations describe analysis the platform could not perform.")
    for note in notes:
        source = docs.get(note.get("artifact_id") or "", {}).get("filename")
        text = f"{note['message']}" + (f"  \n_Source: {source}_" if source else "")
        level = note.get("level")
        if level == "LIMITATION":
            st.warning(f"**Limitation.** {text}", icon=":material/block:")
        elif level == "WARNING":
            st.warning(text, icon=":material/warning:")
        else:
            st.info(text, icon=":material/info:")


def _documents_tab(documents: list[dict]) -> None:
    if not documents:
        st.info("No source documents are linked to this architecture revision.", icon=":material/info:")
        return
    by_id = {d["artifact_id"]: d for d in documents}
    st.dataframe(ui.style_frame(pd.DataFrame([{
        "File": d["filename"], "Type": d["detected_type"], "Role": d["role"], "Parse status": d["parse_status"],
        "Size (KB)": f"{d['size_bytes'] / 1024:.1f}", "Added in revision": d.get("revision_added"),
        "Active": "yes" if d.get("active") else "superseded",
    } for d in documents]), status=["Parse status"]), hide_index=True, width="stretch")
    a, b = st.columns([3, 2])
    chosen = a.selectbox("View document", list(by_id), format_func=lambda i: f"{by_id[i]['filename']} ({by_id[i]['detected_type']})",
                         key="arch_doc")
    limit_label = b.selectbox("Text limit", list(TEXT_LIMITS), key="arch_doc_limit")
    content = ui.fetch(api.document_content, chosen, max_chars=TEXT_LIMITS[limit_label])
    ui.emit_html(ui.status_badge(content.get("parse_status")) + f'<span class="art-chip" style="color:#334155;background:#e2e8f0">'
                 f'{ui.esc(content.get("kind") or content.get("detected_type"))}</span>')
    if content.get("parse_error"):
        st.error(content["parse_error"])
    for warning in content.get("warnings") or []:
        st.warning(warning)
    if content.get("truncated"):
        st.caption("The text was truncated; choose a higher limit to see more.")
    nodes, edges, resources, sections = (content.get(k) or [] for k in ("nodes", "edges", "resources", "sections"))
    labels = ["Text"] + (["Sections"] if sections else []) + (["Diagram"] if nodes or edges else []) + (["IaC resources"] if resources else [])
    tabs = st.tabs(labels)
    with tabs[0]:
        if not content.get("text"):
            st.caption("No text was extracted from this document.")
        elif st.toggle("Render as markdown", value=False, key="arch_doc_md", disabled=content.get("kind") != "markdown"):
            st.markdown(ui.md_text(content["text"]))
        else:
            st.code(content["text"], language=None, line_numbers=True, wrap_lines=True, height=420)
    for label_, tab in zip(labels[1:], tabs[1:]):
        with tab:
            if label_ == "Sections":
                st.dataframe(pd.DataFrame([{
                    "Heading": s.get("heading") or "", "Page": s.get("page"), "Slide": s.get("slide"),
                    "Lines": f"{s['line_start']}-{s['line_end']}" if s.get("line_start") else "", "Text": _value_text(s.get("text", ""), 240),
                } for s in sections]), hide_index=True, width="stretch")
            elif label_ == "Diagram":
                st.graphviz_chart(graphs.diagram_dot(nodes, edges), width="stretch")
                st.dataframe(pd.DataFrame(nodes), hide_index=True, width="stretch")
                if edges:
                    st.dataframe(pd.DataFrame(edges), hide_index=True, width="stretch")
            else:
                st.dataframe(pd.DataFrame(resources), hide_index=True, width="stretch")


ui.ensure_shell("architecture")
ui.page_header("Architecture Explorer", "The architecture model extracted from the supplied documents, with provenance and sources.")
review = ui.require_review()
review_id = review["review_id"]
ui.simulated_banner(review)
current = ui.fetch(api.review_architecture, review_id)
if not current.get("available"):
    st.info("No architecture model is available yet. It is produced by the extraction stage once the review has started.",
            icon=":material/hourglass_empty:")
    st.stop()

revisions = sorted(set(current.get("revisions") or [current["revision"]]))
default_index = revisions.index(current["revision"]) if current["revision"] in revisions else len(revisions) - 1
revision = st.selectbox("Architecture revision", revisions, index=default_index, key=f"arch_rev_{review_id}",
                        format_func=lambda n: f"Revision {n}" + (" (current)" if n == current["revision"] else ""))
data = current if revision == current["revision"] else ui.fetch(api.review_architecture, review_id, revision=revision)
model = data["model"]
documents = {d["artifact_id"]: d for d in data.get("documents", [])}

summary = model.get("title") or review["name"]
st.markdown(f"**{ui.md_text(summary)}**  ·  environment {ui.md_text(model.get('environment') or 'Unknown')}")
if model.get("purpose"):
    st.caption(model["purpose"])
ui.metric_cards([
    ("Components", len(model.get("components", [])), ""), ("Relationships", len(model.get("relationships", [])), ""),
    ("Requirements", len(model.get("nfrs", [])), "non-functional"), ("Source documents", len(documents), ""),
    ("Unresolved", len(model.get("unresolved", [])), "information gaps"),
])
st.space("small")
tabs = st.tabs(["Components", "Relationships", "Dependency graph", "Requirements and constraints", "Extraction notes", "Source documents"])
with tabs[0]:
    _components_tab(model, documents)
with tabs[1]:
    _relationships_tab(model)
with tabs[2]:
    _graph_tab(model)
with tabs[3]:
    _requirements_tab(model)
with tabs[4]:
    _notes_tab(model, documents)
with tabs[5]:
    _documents_tab(list(documents.values()))
