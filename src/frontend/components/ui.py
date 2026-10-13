"""Shared presentation helpers for the Streamlit pages.

Responsibilities: global CSS, chips and banners, page registry and navigation, active-review selection,
backend error handling and the reusable renderers (metric cards, agent cards, event timeline, source
references, finding detail).  Every backend-provided string is HTML-escaped before it reaches
``st.html``; nothing here talks to the backend except through ``frontend.api_client``.
"""
from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from frontend import api_client as api

# --------------------------------------------------------------------------- vocabulary
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")
SEVERITY_COLORS = {"CRITICAL": "#b91c1c", "HIGH": "#c2410c", "MEDIUM": "#a16207", "LOW": "#1d4ed8", "INFORMATIONAL": "#475569"}
AGENT_IDS = ("security", "resilience", "finops", "compliance", "performance", "operability")
AGENT_LABELS = {"security": "Security", "resilience": "Resilience", "finops": "FinOps", "compliance": "Compliance",
                "performance": "Performance", "operability": "Operability"}
RUNNING_STATUSES = frozenset({"EXTRACTING", "ANALYZING", "MODERATING", "REBUTTING", "RE_REVIEWING"})
COMPLETED_STATUSES = frozenset({"COMPLETED", "COMPLETED_WITH_OPEN_RISKS"})
CLOSED_FINDING_STATUSES = frozenset({"REJECTED", "RESOLVED", "VERIFIED"})
AGENT_DONE_STATUSES = frozenset({"COMPLETED", "SKIPPED", "FAILED"})
DOCUMENT_ROLES = ("PRIMARY_ARCHITECTURE", "DIAGRAM", "INFRASTRUCTURE", "STANDARD", "ADR", "INCIDENT", "SUPPORTING")
CLOUD_ENVIRONMENTS = ("AWS", "Azure", "GCP", "Hybrid", "On-premises", "Kubernetes", "Unknown")

SIMULATED_BANNER_TEXT = "SIMULATED DEMO MODE — findings are fixture-generated, not AI-discovered"
LOCAL_ONLY_TEXT = ("Local proof of concept: the API binds to loopback and there is no authentication. "
                   "Do not expose this application to a network.")
LIVE_WARNING_TEXT = ("Live-model analysis sends relevant document content to the selected model provider. "
                     "Do not submit material you are not permitted to share with that provider.")
NOT_AN_APPROVAL_TEXT = ("This assessment is advisory and is not an approval of the architecture. "
                        "Approval remains a human decision.")

# (text colour, background) per status word, shared by review, agent, finding, parse and tool states
STATUS_STYLES: dict[str, tuple[str, str]] = {
    "CREATED": ("#334155", "#e2e8f0"), "UPLOADING": ("#334155", "#e2e8f0"),
    "EXTRACTING": ("#1d4ed8", "#dbeafe"), "ANALYZING": ("#1d4ed8", "#dbeafe"), "MODERATING": ("#1d4ed8", "#dbeafe"),
    "REBUTTING": ("#1d4ed8", "#dbeafe"), "RE_REVIEWING": ("#1d4ed8", "#dbeafe"), "RUNNING": ("#1d4ed8", "#dbeafe"),
    "AWAITING_HUMAN": ("#92400e", "#fef3c7"),
    "COMPLETED": ("#166534", "#dcfce7"), "COMPLETED_WITH_OPEN_RISKS": ("#9a3412", "#ffedd5"),
    "FAILED": ("#991b1b", "#fee2e2"), "PENDING": ("#475569", "#f1f5f9"), "SKIPPED": ("#64748b", "#f1f5f9"),
    "OPEN": ("#334155", "#e2e8f0"), "ACCEPTED": ("#1e40af", "#dbeafe"), "DISPUTED": ("#6d28d9", "#ede9fe"),
    "REBUTTED": ("#6d28d9", "#ede9fe"), "REJECTED": ("#64748b", "#f1f5f9"), "DEFERRED": ("#92400e", "#fef3c7"),
    "REMEDIATION_PROPOSED": ("#1d4ed8", "#dbeafe"), "RESOLVED": ("#166534", "#dcfce7"), "VERIFIED": ("#0f766e", "#ccfbf1"),
    "VALIDATED": ("#166534", "#dcfce7"), "MERGED": ("#1e40af", "#dbeafe"), "REQUIRES_VALIDATION": ("#92400e", "#fef3c7"),
    "CONFIRMED": ("#166534", "#dcfce7"), "INFERRED": ("#1e40af", "#dbeafe"), "NEEDS_VALIDATION": ("#92400e", "#fef3c7"),
    "PARSED": ("#166534", "#dcfce7"), "PARTIAL": ("#92400e", "#fef3c7"), "UNSUPPORTED": ("#991b1b", "#fee2e2"),
    "EXECUTED": ("#166534", "#dcfce7"), "NOT_EXECUTED": ("#92400e", "#fef3c7"), "ERROR": ("#991b1b", "#fee2e2"),
    "VIOLATION": ("#991b1b", "#fee2e2"), "PASS": ("#166534", "#dcfce7"), "INFO": ("#475569", "#f1f5f9"),
    "WARNING": ("#92400e", "#fef3c7"), "LIMITATION": ("#92400e", "#fef3c7"),
    "EXPLICIT": ("#166534", "#dcfce7"), "UNKNOWN": ("#475569", "#f1f5f9"),
    "FULL": ("#166534", "#dcfce7"), "NONE": ("#991b1b", "#fee2e2"),
}
PRINCIPLE_ORIGINS: dict[str, tuple[str, str, str, str]] = {
    # origin -> (label, text colour, background, explanation)
    "ORGANIZATIONAL_STANDARD": ("Organizational standard", "#1e3a8a", "#dbeafe",
                                "Cites a rule from the organisation's own supplied standards."),
    "CONTROL_MAPPING": ("Control mapping (indicative)", "#6d28d9", "#ede9fe",
                        "Maps to an external control framework; an indication of a possible gap, not an audit result."),
    "GENERAL_PRACTICE": ("General practice (not an org standard)", "#475569", "#f1f5f9",
                         "Industry good practice; no organisational standard was cited."),
}


_DIAGRAM_EXTENSIONS = frozenset({".drawio", ".mmd", ".mermaid", ".puml", ".plantuml", ".pu", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".xml"})
_IAC_EXTENSIONS = frozenset({".tf", ".tfvars", ".yaml", ".yml", ".json"})
_ROLE_HINTS = (("adr", "ADR"), ("incident", "INCIDENT"), ("postmortem", "INCIDENT"), ("outage", "INCIDENT"),
               ("standard", "STANDARD"), ("policy", "STANDARD"), ("guideline", "STANDARD"))


def default_document_role(filename: str, primary_taken: bool) -> str:
    """Sensible document role for an uploaded file (extension first, then file-name hints)."""
    lowered = filename.lower()
    ext = Path(lowered).suffix
    if ext in _DIAGRAM_EXTENSIONS:
        return "DIAGRAM"
    if ext in _IAC_EXTENSIONS:
        return "INFRASTRUCTURE"
    for hint, role in _ROLE_HINTS:
        if hint in lowered:
            return role
    return "SUPPORTING" if primary_taken else "PRIMARY_ARCHITECTURE"


# --------------------------------------------------------------------------- page registry
@dataclass(frozen=True)
class PageSpec:
    """One navigable page: script path (relative to ``frontend/app.py``), title, icon and URL path."""

    path: str
    title: str
    icon: str
    url_path: str


PAGES: dict[str, PageSpec] = {
    "dashboard": PageSpec("pages/dashboard.py", "Dashboard", ":material/dashboard:", "dashboard"),
    "new_review": PageSpec("pages/new_review.py", "New Review", ":material/add_circle:", "new-review"),
    "review_progress": PageSpec("pages/review_progress.py", "Review Execution", ":material/play_circle:", "execution"),
    "findings": PageSpec("pages/findings.py", "Findings Explorer", ":material/rule:", "findings"),
    "architecture": PageSpec("pages/architecture.py", "Architecture Explorer", ":material/account_tree:", "architecture"),
    "reports": PageSpec("pages/reports.py", "Reports", ":material/description:", "reports"),
    "settings": PageSpec("pages/settings.py", "Settings", ":material/settings:", "settings"),
}


SHELL_KEY = "_art_shell"
DEEPLINK_KEY = "_art_deeplink"
ENTRY_SCRIPT = "app.py"


def ensure_shell(page_key: str) -> None:
    """Hand a directly started page over to the entry script.

    On a fresh Streamlit server whose first request is a deep link, Streamlit's legacy ``pages/`` mode runs the
    page script on its own, without ``app.py`` (no theme, no sidebar picker).  In that case remember the target
    page and switch to the entry script, which registers the navigation and forwards to the page.
    """
    try:
        from streamlit.runtime.pages_manager import PagesManager

        legacy = bool(PagesManager.uses_pages_directory)
    except ImportError:
        legacy = False
    if legacy and not st.session_state.get(SHELL_KEY):
        get_review_id()  # keeps a ?review_id= deep link in session state across the switch
        st.session_state[DEEPLINK_KEY] = page_key
        st.switch_page(ENTRY_SCRIPT)


def goto(key: str) -> None:
    """Switch to a registered page; outside the multipage app (e.g. in tests) show a hint instead."""
    spec = PAGES[key]
    try:
        st.switch_page(spec.path)
    except StreamlitAPIException:
        st.info(f"Open the '{spec.title}' page from the sidebar.")


def page_for_status(status: str) -> str:
    """Most useful page for a review in ``status``."""
    if status == "AWAITING_HUMAN":
        return "findings"
    if status in COMPLETED_STATUSES:
        return "reports"
    return "review_progress"


# --------------------------------------------------------------------------- formatting
def esc(value: Any) -> str:
    """HTML-escape any value."""
    return html.escape("" if value is None else str(value), quote=True)


def fmt_dt(value: str | datetime | None) -> str:
    """Format an ISO timestamp (or datetime) as ``YYYY-MM-DD HH:MM:SS`` in local time."""
    if not value:
        return "—"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def label(value: str | None) -> str:
    """Human label for an enum-like value (``AWAITING_HUMAN`` -> ``AWAITING HUMAN``)."""
    return (value or "—").replace("_", " ")


def md_text(value: Any) -> str:
    """Make backend text safe for ``st.markdown`` (no accidental LaTeX)."""
    return str(value or "").replace("$", "\\$")


def join_or_dash(values: Iterable[Any], sep: str = ", ") -> str:
    """Join non-empty values, or an em dash when there are none."""
    items = [str(v) for v in values if v not in (None, "")]
    return sep.join(items) if items else "—"


def pct(value: float | None) -> str:
    """Format a 0..1 ratio as a percentage, or ``N/A``."""
    return "N/A" if value is None else f"{value * 100:.0f}%"


def na(value: Any) -> str:
    """Render ``None`` as ``N/A``."""
    return "N/A" if value is None else str(value)


# --------------------------------------------------------------------------- CSS
_CSS = """
<style>
:root { --art-ink:#0f172a; --art-muted:#64748b; --art-line:#e2e8f0; --art-soft:#f8fafc; --art-accent:#1d4ed8; }
.block-container { padding-top: 4.2rem; padding-bottom: 3rem; max-width: 1280px; }
h1, h2, h3, h4 { color: var(--art-ink); letter-spacing: -0.01em; }
[data-testid="stSidebar"] { background: #f1f5f9; border-right: 1px solid var(--art-line); }
.art-page-header { border-bottom: 1px solid var(--art-line); margin: 0 0 1.1rem 0; padding-bottom: .7rem; }
.art-page-header h1 { font-size: 1.65rem; font-weight: 650; margin: 0; padding: 0; line-height: 1.25; }
.art-page-header p { color: var(--art-muted); margin: .25rem 0 0 0; font-size: .95rem; }
.art-chip { display:inline-block; padding: 1px 9px; border-radius: 999px; font-size: .72rem; font-weight: 600;
  letter-spacing: .02em; line-height: 1.55; border: 1px solid transparent; white-space: nowrap; margin-right: 4px; }
.art-banner { border-radius: 6px; padding: .6rem .9rem; margin: 0 0 .9rem 0; font-size: .88rem; border: 1px solid; }
.art-banner.sim { background:#fef2f2; border-color:#fca5a5; color:#7f1d1d; font-weight: 700; letter-spacing:.02em; }
.art-banner.local { background:#f1f5f9; border-color:#cbd5e1; color:#334155; font-weight: 500; }
.art-banner.live { background:#fffbeb; border-color:#fcd34d; color:#78350f; font-weight: 500; }
.art-banner.note { background:#eff6ff; border-color:#bfdbfe; color:#1e3a8a; font-weight: 500; }
.art-metric { background:#fff; border:1px solid var(--art-line); border-radius: 8px; padding: .75rem .9rem; height: 100%; }
.art-metric .k { color: var(--art-muted); font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.art-metric .v { color: var(--art-ink); font-size: 1.65rem; font-weight: 650; line-height: 1.3; }
.art-metric .v.sm { font-size: 1.05rem; padding: .25rem 0; }
.art-metric .h { color: var(--art-muted); font-size: .76rem; }
.art-card { background:#fff; border:1px solid var(--art-line); border-radius: 8px; padding: .8rem .95rem; margin-bottom: .5rem; }
.art-card .t { font-weight: 650; color: var(--art-ink); font-size: .95rem; }
.art-card .meta { color: var(--art-muted); font-size: .8rem; line-height: 1.55; margin-top: .35rem; }
.art-card .err { color:#991b1b; font-size:.82rem; margin-top:.35rem; }
.art-card details { margin-top:.35rem; font-size:.8rem; color:#334155; }
.art-card summary { cursor:pointer; color: var(--art-muted); }
.art-sev-bar { border-left: 5px solid; padding-left: .8rem; }
.art-section { color: var(--art-muted); font-size:.72rem; font-weight:700; text-transform: uppercase; letter-spacing:.07em; margin:.9rem 0 .25rem 0; }
.art-ref { border:1px solid var(--art-line); border-radius:6px; padding:.5rem .65rem; margin-bottom:.45rem; background: var(--art-soft); }
.art-ref .loc { color: var(--art-muted); font-size:.78rem; }
.art-excerpt { margin:.35rem 0 0 0; padding:.45rem .55rem; background:#fff; border:1px solid var(--art-line); border-radius:4px;
  font-size:.78rem; white-space: pre-wrap; word-break: break-word; color:#1e293b; }
.art-event { display:flex; gap:.6rem; align-items:baseline; padding:.28rem 0; border-bottom:1px solid #f1f5f9; font-size:.82rem; }
.art-event .ts { color: var(--art-muted); font-variant-numeric: tabular-nums; white-space:nowrap; }
.art-event .msg { color:#1e293b; word-break: break-word; }
.art-event.WARNING .msg { color:#92400e; }
.art-event.ERROR .msg { color:#991b1b; font-weight:600; }
.art-exchange { border-left:3px solid #cbd5e1; padding:.35rem .7rem; margin:.4rem 0; background: var(--art-soft); border-radius:0 6px 6px 0; }
.art-exchange.ARCHITECT { border-color:#1d4ed8; } .art-exchange.CRITIC { border-color:#c2410c; } .art-exchange.MODERATOR { border-color:#6d28d9; }
.art-exchange .who { font-size:.72rem; font-weight:700; letter-spacing:.06em; color: var(--art-muted); text-transform:uppercase; }
.art-hist { border-left:2px solid #cbd5e1; margin-left:.4rem; padding:0 0 .6rem .8rem; position:relative; }
.art-hist .dot { position:absolute; left:-6px; top:4px; width:10px; height:10px; border-radius:50%; background:#94a3b8; }
.art-hist .what { font-weight:600; font-size:.85rem; color: var(--art-ink); }
.art-hist .when { color: var(--art-muted); font-size:.76rem; }
.art-legend span.sw { display:inline-block; width:.8rem; height:.8rem; border-radius:3px; margin-right:.3rem; vertical-align:-1px; border:1px solid; }
.art-legend { font-size:.78rem; color:#334155; margin-right:.9rem; display:inline-block; }
div[data-testid="stDataFrame"] { border: 1px solid var(--art-line); border-radius: 6px; }
</style>
"""


def inject_css() -> None:
    """Inject the global stylesheet (call once per run from the entry script)."""
    st.html(_CSS)


# --------------------------------------------------------------------------- chips and banners
def chip(text: str, fg: str, bg: str, title: str = "") -> str:
    """HTML for a pill-shaped chip."""
    tip = f' title="{esc(title)}"' if title else ""
    return f'<span class="art-chip" style="color:{fg};background:{bg};border-color:{fg}33"{tip}>{esc(text)}</span>'


def severity_chip(severity: str) -> str:
    """Chip coloured with the severity palette (CRITICAL #b91c1c ... INFORMATIONAL #475569)."""
    color = SEVERITY_COLORS.get(str(severity).upper(), "#475569")
    return f'<span class="art-chip" style="color:#fff;background:{color};border-color:{color}">{esc(str(severity).upper())}</span>'


def status_badge(status: str | None, text: str | None = None, title: str = "") -> str:
    """Chip for a review, agent, finding, moderator, parse or tool status."""
    key = str(status or "").upper()
    fg, bg = STATUS_STYLES.get(key, ("#475569", "#f1f5f9"))
    return chip(text or label(key), fg, bg, title)


def simulated_chip() -> str:
    """Chip marking fixture-generated (simulated) content."""
    return chip("SIMULATED", "#7f1d1d", "#fee2e2", "Fixture-generated, not AI-discovered")


def origin_label(origin: str | None) -> str:
    """Distinct label for the principle origin."""
    text, fg, bg, why = PRINCIPLE_ORIGINS.get(str(origin or "GENERAL_PRACTICE"), PRINCIPLE_ORIGINS["GENERAL_PRACTICE"])
    return chip(text, fg, bg, why)


def emit_html(markup: str) -> None:
    """Render a trusted HTML fragment (callers escape all dynamic values)."""
    st.html(markup)


def banner(text: str, kind: str = "note") -> None:
    """Full-width banner; ``kind`` is one of sim, local, live, note."""
    emit_html(f'<div class="art-banner {kind}">{esc(text)}</div>')


def simulated_banner(review: dict[str, Any] | None) -> None:
    """Prominent banner shown on every page that displays data of a demo (simulated) review."""
    if review and review.get("simulated"):
        banner(SIMULATED_BANNER_TEXT, "sim")


def local_only_notice() -> None:
    """Local-only / no-authentication notice."""
    banner(LOCAL_ONLY_TEXT, "local")


def live_mode_warning() -> None:
    """Warning that live mode sends document content to the model provider."""
    banner(LIVE_WARNING_TEXT, "live")


def not_an_approval_notice() -> None:
    """Statement that an assessment is not an approval."""
    banner(NOT_AN_APPROVAL_TEXT, "note")


def page_header(title: str, subtitle: str = "") -> None:
    """Consistent page title block."""
    sub = f"<p>{esc(subtitle)}</p>" if subtitle else ""
    emit_html(f'<div class="art-page-header"><h1>{esc(title)}</h1>{sub}</div>')


def section_label(text: str) -> None:
    """Small uppercase section label."""
    emit_html(f'<div class="art-section">{esc(text)}</div>')


# --------------------------------------------------------------------------- backend access
def show_api_error(exc: api.ApiError) -> None:
    """Friendly rendering of an :class:`ApiError`."""
    if exc.unavailable:
        st.error(f"The backend is not reachable ({exc.message}). Start it with `python src/run.py` (or `uvicorn backend.main:app`) "
                 "and reload this page.", icon=":material/cloud_off:")
        return
    st.error(f"{exc.message}", icon=":material/error:")
    if exc.details:
        with st.expander("Error details"):
            st.json(exc.details if isinstance(exc.details, (dict, list)) else {"details": str(exc.details)})


def fetch(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call an API helper; on failure show the error and stop the current script run."""
    try:
        return fn(*args, **kwargs)
    except api.ApiError as exc:
        show_api_error(exc)
        st.stop()


def attempt(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any | None:
    """Call an API helper for a user action; on failure show the error and return ``None``."""
    try:
        return fn(*args, **kwargs)
    except api.ApiError as exc:
        show_api_error(exc)
        return None


@st.cache_data(ttl=300, show_spinner=False)
def cached_scenarios() -> list[dict[str, Any]]:
    """Static lookup: demo scenarios."""
    return api.demo_scenarios()


@st.cache_data(ttl=300, show_spinner=False)
def cached_supported_documents() -> dict[str, Any]:
    """Static lookup: supported extensions and roles."""
    return api.supported_documents()


# --------------------------------------------------------------------------- active review
REVIEW_KEY = "review_id"


def get_review_id() -> str | None:
    """Active review id from session state or the ``review_id`` query parameter."""
    rid = st.session_state.get(REVIEW_KEY)
    if not rid:
        rid = st.query_params.get("review_id")
        if rid:
            st.session_state[REVIEW_KEY] = rid
    return rid or None


def set_review_id(review_id: str | None) -> None:
    """Select the active review."""
    st.session_state[REVIEW_KEY] = review_id
    if review_id:
        st.query_params["review_id"] = review_id
    elif "review_id" in st.query_params:
        del st.query_params["review_id"]


def open_review(review_id: str, status: str) -> None:
    """Select a review and navigate to the page that matches its status."""
    set_review_id(review_id)
    goto(page_for_status(status))


def review_option_label(review: dict[str, Any]) -> str:
    """Selector label for a review."""
    demo = "DEMO · " if review.get("simulated") else ""
    return f"{demo}{review.get('name', review['review_id'])} · {label(review.get('status'))} · {review['review_id']}"


def sidebar_review_picker() -> None:
    """Sidebar selector listing reviews (GET /reviews); keeps session state and query parameters in sync."""
    with st.sidebar:
        section_label("Active review")
        try:
            reviews = api.list_reviews()
        except api.ApiError as exc:
            st.error("Backend unreachable." if exc.unavailable else exc.message, icon=":material/cloud_off:")
            return
        if not reviews:
            st.caption("No reviews yet. Create one from New Review.")
            return
        ids = [r["review_id"] for r in reviews]
        current = get_review_id()
        index = ids.index(current) if current in ids else 0
        chosen = st.selectbox("Review", ids, index=index, format_func=lambda i: review_option_label(reviews[ids.index(i)]),
                              label_visibility="collapsed")
        if chosen != st.session_state.get(REVIEW_KEY):
            set_review_id(chosen)
        elif chosen and st.query_params.get("review_id") != chosen:
            st.query_params["review_id"] = chosen
        picked = reviews[ids.index(chosen)]
        emit_html(status_badge(picked["status"]) + (simulated_chip() if picked.get("simulated") else ""))


def sidebar_footer() -> None:
    """Sidebar footer: backend URL and the local-only reminder."""
    with st.sidebar:
        st.divider()
        st.caption(f"Backend: `{api.BASE_URL}`")
        st.caption("Local only · no authentication")


def require_review() -> dict[str, Any]:
    """Return the active review (GET /reviews/{id}) or explain how to select one and stop."""
    rid = get_review_id()
    if not rid:
        st.info("No review is selected. Pick one in the sidebar, or create a new review.", icon=":material/info:")
        if st.button("Create a new review", type="primary"):
            goto("new_review")
        st.stop()
    try:
        return api.get_review(rid)
    except api.ApiError as exc:
        if exc.status == 404:
            set_review_id(None)
            st.warning(f"Review '{rid}' was not found (the backend may have been reset). Select another review in the sidebar.")
            st.stop()
        show_api_error(exc)
        st.stop()


# --------------------------------------------------------------------------- tables
def style_frame(df: pd.DataFrame, severity: Sequence[str] = (), status: Sequence[str] = ()) -> Any:
    """pandas Styler colouring severity and status columns for ``st.dataframe``."""
    def sev(value: Any) -> str:
        color = SEVERITY_COLORS.get(str(value).upper())
        return f"color:{color};font-weight:700" if color else ""

    def stat(value: Any) -> str:
        fg, bg = STATUS_STYLES.get(str(value).upper().replace(" ", "_"), (None, None))
        return f"color:{fg};background-color:{bg};font-weight:600" if fg else ""

    styler = df.style
    if severity:
        styler = styler.map(sev, subset=[c for c in severity if c in df.columns])
    if status:
        styler = styler.map(stat, subset=[c for c in status if c in df.columns])
    return styler


def metric_cards(items: Sequence[tuple[str, Any, str]]) -> None:
    """One row of metric cards from ``(label, value, hint)`` tuples."""
    cols = st.columns(len(items))
    for col, (name, value, hint) in zip(cols, items):
        with col:
            hint_html = f'<div class="h">{esc(hint)}</div>' if hint else ""
            size = " sm" if len(str(value)) > 10 else ""
            emit_html(f'<div class="art-metric"><div class="k">{esc(name)}</div><div class="v{size}">{esc(value)}</div>{hint_html}</div>')


# --------------------------------------------------------------------------- source references
def location_text(ref: dict[str, Any]) -> str:
    """Compact location of a source reference (page, slide, section, lines, node, resource)."""
    parts: list[str] = []
    if ref.get("page") is not None:
        parts.append(f"page {ref['page']}")
    if ref.get("slide") is not None:
        parts.append(f"slide {ref['slide']}")
    if ref.get("section"):
        parts.append(f"section “{ref['section']}”")
    if ref.get("line_start") is not None:
        end = ref.get("line_end")
        parts.append(f"lines {ref['line_start']}-{end}" if end and end != ref["line_start"] else f"line {ref['line_start']}")
    if ref.get("node_id"):
        parts.append(f"node {ref['node_id']}" + (f" ({ref['label']})" if ref.get("label") else ""))
    if ref.get("resource_path"):
        parts.append(f"resource {ref['resource_path']}")
    if ref.get("tool_result_id"):
        parts.append(f"tool result {ref['tool_result_id']}")
    return " · ".join(parts)


def source_references_html(refs: Sequence[dict[str, Any]]) -> str:
    """HTML list of source references with their excerpts."""
    if not refs:
        return '<div class="art-card meta">No source references were recorded.</div>'
    out: list[str] = []
    for ref in refs:
        name = ref.get("filename") or ref.get("artifact_id") or "unknown source"
        loc = location_text(ref)
        excerpt = (ref.get("excerpt") or "").strip()
        body = f'<pre class="art-excerpt">{esc(excerpt)}</pre>' if excerpt else ""
        out.append(f'<div class="art-ref"><b>{esc(name)}</b> <span class="loc">{esc(loc)}</span>{body}</div>')
    return "".join(out)


def render_source_references(refs: Sequence[dict[str, Any]]) -> None:
    """Render source references (filename, page/slide/section, lines, node/resource path, excerpt)."""
    emit_html(source_references_html(refs))


# --------------------------------------------------------------------------- agents, events, tools
def agent_progress(agents: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """(finished, total) over agent statuses: completed + skipped + failed."""
    done = sum(1 for a in agents if a.get("status") in AGENT_DONE_STATUSES)
    return done, len(agents)


def _usage_text(usage: dict[str, Any] | None) -> str:
    if not usage:
        return "N/A"
    tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
    latency = usage.get("latency_ms")
    tokens = f"{tin} in / {tout} out tokens" if tin is not None or tout is not None else "tokens N/A"
    return f"{tokens}" + (f" · {latency} ms" if latency is not None else "")


def agent_card(agent: dict[str, Any]) -> None:
    """Card for one specialist agent built from GET /reviews/{id}/status ``agents`` entries."""
    status = agent.get("status", "PENDING")
    sim = simulated_chip() if agent.get("simulated") else ""
    rows = [
        ("Started", fmt_dt(agent.get("started_at"))), ("Completed", fmt_dt(agent.get("completed_at"))),
        ("Round", na(agent.get("round"))), ("Findings", agent.get("finding_count", 0)),
        ("Tool executions", f"{agent.get('tool_count', 0)} ({agent.get('tool_violations', 0)} violations)"),
        ("Usage", _usage_text(agent.get("usage"))),
    ]
    meta = "<br>".join(f"<b>{esc(k)}:</b> {esc(v)}" for k, v in rows)
    extra = ""
    if agent.get("skip_reason"):
        extra += f'<div class="meta"><b>Skip reason:</b> {esc(agent["skip_reason"])}</div>'
    if agent.get("error"):
        extra += f'<div class="err"><b>Error:</b> {esc(agent["error"])}</div>'
    for title, key in (("Warnings", "warnings"), ("Information gaps", "information_gaps")):
        items = agent.get(key) or []
        if items:
            lis = "".join(f"<li>{esc(i)}</li>" for i in items)
            extra += f"<details><summary>{title} ({len(items)})</summary><ul>{lis}</ul></details>"
    emit_html(f'<div class="art-card"><div class="t">{esc(agent.get("label", agent.get("agent_id")))} '
              f'{status_badge(status)}{sim}</div><div class="meta">{meta}</div>{extra}</div>')


def event_timeline(events: Sequence[dict[str, Any]], newest_first: bool = True, height: int = 360) -> None:
    """Scrollable execution timeline (timestamp, level colouring, agent, message)."""
    if not events:
        st.caption("No events recorded yet.")
        return
    ordered = list(reversed(events)) if newest_first else list(events)
    rows = []
    for ev in ordered:
        level = str(ev.get("level", "INFO")).upper()
        agent = chip(AGENT_LABELS.get(ev.get("agent_id"), ev.get("agent_id")), "#334155", "#e2e8f0") if ev.get("agent_id") else ""
        lvl = status_badge(level) if level != "INFO" else ""
        rows.append(f'<div class="art-event {esc(level)}"><span class="ts">{esc(fmt_dt(ev.get("timestamp")))}</span>'
                    f'<span>{lvl}{agent}</span><span class="msg">{esc(ev.get("message"))}</span></div>')
    with st.container(height=height, border=True):
        emit_html("".join(rows))


def tool_summary_frame(summaries: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Table of per-tool run summaries (``NOT_EXECUTED`` rows carry the reason in ``detail``)."""
    return pd.DataFrame([{
        "Tool": s.get("tool_name"), "Status": s.get("status"), "Resources checked": s.get("resources_checked", 0),
        "Violations": s.get("violations", 0), "Detail": s.get("detail") or "",
    } for s in summaries], columns=["Tool", "Status", "Resources checked", "Violations", "Detail"])


# --------------------------------------------------------------------------- findings
def finding_chips(finding: dict[str, Any]) -> str:
    """Severity, evidence, status, moderator and simulated chips for a finding."""
    chips = [severity_chip(finding["severity"]), status_badge(finding.get("evidence_level")),
             status_badge(finding.get("status")), status_badge(finding.get("moderator_status"), f"MODERATOR: {label(finding.get('moderator_status'))}")]
    if finding.get("simulated"):
        chips.append(simulated_chip())
    return "".join(chips)


def finding_card(finding: dict[str, Any]) -> None:
    """Compact finding summary card."""
    color = SEVERITY_COLORS.get(str(finding.get("severity")).upper(), "#475569")
    comps = join_or_dash(finding.get("affected_components") or [])
    domain = AGENT_LABELS.get(finding.get("agent_id") or finding.get("domain"), finding.get("agent_id") or finding.get("domain") or "")
    emit_html(f'<div class="art-card art-sev-bar" style="border-left-color:{color}"><div class="t">{esc(finding.get("title"))}</div>'
              f'<div class="meta">{finding_chips(finding)} <br>{esc(domain)} · components: {esc(comps)}</div></div>')


def _text_block(title: str, text: str | None) -> None:
    if text:
        section_label(title)
        st.markdown(md_text(text))


def _history_html(history: Sequence[dict[str, Any]]) -> str:
    rows = []
    for ev in history:
        extra = []
        if ev.get("round") is not None:
            extra.append(f"round {ev['round']}")
        if ev.get("revision") is not None:
            extra.append(f"revision {ev['revision']}")
        data = ev.get("data") or {}
        for key in ("review_date", "risk_owner", "remediation_target"):
            if data.get(key):
                extra.append(f"{key.replace('_', ' ')}: {data[key]}")
        meta = " · ".join(extra)
        detail = f'<div class="msg">{esc(ev.get("detail"))}</div>' if ev.get("detail") else ""
        rows.append(f'<div class="art-hist"><span class="dot"></span><div class="what">{esc(label(ev.get("event")))} '
                    f'<span class="when">by {esc(ev.get("actor"))} · {esc(fmt_dt(ev.get("timestamp")))}'
                    f'{" · " + esc(meta) if meta else ""}</span></div>{detail}</div>')
    return "".join(rows) or '<div class="art-card meta">No history recorded.</div>'


def decisions_frame(decisions: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Human decision history as a table."""
    cols = ["When", "Decision", "Actor", "Round", "Revision", "Justification", "Notes", "Review date", "Risk owner", "Remediation target", "Submitted"]
    return pd.DataFrame([{
        "When": fmt_dt(d.get("created_at")), "Decision": d.get("decision_type"), "Actor": d.get("actor"), "Round": d.get("round"),
        "Revision": d.get("revision"), "Justification": d.get("justification") or "", "Notes": d.get("notes") or "",
        "Review date": d.get("review_date") or "", "Risk owner": d.get("risk_owner") or "",
        "Remediation target": d.get("remediation_target") or "", "Submitted": "yes" if d.get("submitted") else "pending",
    } for d in decisions], columns=cols)


def rebuttal_view(rebuttals: Sequence[dict[str, Any]]) -> None:
    """Architect -> critic -> moderator exchanges with stance and outcome."""
    if not rebuttals:
        st.caption("No rebuttal exchanges for this finding.")
        return
    for reb in rebuttals:
        head = (f'<b>Round {esc(reb.get("round"))}</b> · critic {esc(AGENT_LABELS.get(reb.get("agent_id"), reb.get("agent_id")))} '
                f'{status_badge(reb.get("stance"), "STANCE: " + label(reb.get("stance")))}'
                f'{status_badge(reb.get("outcome"), "OUTCOME: " + label(reb.get("outcome")))}'
                f'{simulated_chip() if reb.get("simulated") else ""}')
        if reb.get("revised_severity"):
            head += f' revised severity {severity_chip(reb["revised_severity"])}'
        entries = reb.get("exchange") or []
        body = "".join(
            f'<div class="art-exchange {esc(e.get("role"))}"><div class="who">{esc(e.get("role"))} · {esc(e.get("actor"))}'
            f'{" · " + esc(label(e.get("stance"))) if e.get("stance") else ""}</div>{esc(e.get("content"))}</div>' for e in entries)
        if not entries and reb.get("moderator_determination"):
            body = f'<div class="art-exchange MODERATOR"><div class="who">MODERATOR</div>{esc(reb["moderator_determination"])}</div>'
        emit_html(f'<div class="art-card">{head}{body}</div>')


def tool_results_frame(results: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Tool observations (rule, observed vs expected, evidence level) for a finding."""
    cols = ["Rule", "Tool", "Outcome", "Observed", "Expected", "Evidence", "Severity hint", "Components", "Location", "Message"]
    rows = []
    for t in results:
        ref = t.get("evidence_reference") or {}
        loc = " · ".join(p for p in (ref.get("filename"), location_text(ref)) if p)
        rows.append({
            "Rule": t.get("rule_id"), "Tool": t.get("tool_name"), "Outcome": t.get("outcome"), "Observed": t.get("observed_value") or "",
            "Expected": t.get("expected_value") or "", "Evidence": t.get("evidence_level"), "Severity hint": t.get("severity_hint"),
            "Components": join_or_dash(t.get("affected_component_ids") or []), "Location": loc, "Message": t.get("message") or "",
        })
    return pd.DataFrame(rows, columns=cols)


def finding_detail(view: dict[str, Any], adr_titles: dict[str, str] | None = None) -> None:
    """Read-only detail of a GET /findings/{id} payload: narrative, evidence, agents, decisions and history."""
    f = view["finding"]
    color = SEVERITY_COLORS.get(f["severity"], "#475569")
    domain = AGENT_LABELS.get(f.get("agent_id"), f.get("agent_id"))
    emit_html(f'<div class="art-sev-bar" style="border-left-color:{color}"><h3 style="margin:0">{esc(f["title"])}</h3>'
              f'<div style="margin-top:.4rem">{finding_chips(f)}'
              f'<span class="art-chip" style="color:#334155;background:#e2e8f0">{esc(domain)}</span>'
              f'<span class="art-chip" style="color:#334155;background:#f1f5f9">{esc(f.get("category"))}</span></div>'
              f'<div class="art-card meta" style="border:0;padding:.3rem 0">Confidence {esc(pct(f.get("confidence")))} · '
              f'first seen revision {esc(f.get("first_seen_revision"))}, last seen revision {esc(f.get("last_seen_revision"))} · '
              f'disputed {esc(f.get("dispute_count", 0))} time(s)</div></div>')
    if f.get("simulated"):
        banner("This finding was generated from a demo fixture (simulated), not discovered by an AI model.", "sim")
    tabs = st.tabs(["Overview", "Evidence", "Agents and moderation", "Decisions and rebuttals", "History"])
    with tabs[0]:
        _text_block("Description", f.get("description"))
        _text_block("Impact", f.get("impact"))
        _text_block("Attack / failure scenario", f.get("scenario"))
        _text_block("Recommendation", f.get("recommendation"))
        section_label("Principle")
        emit_html(f'{origin_label(f.get("principle_origin"))} <b>{esc(f.get("principle") or "No principle cited")}</b>')
        _text_block("Validation method", f.get("validation_method"))
        if f.get("assumptions"):
            section_label("Assumptions")
            st.markdown("\n".join(f"- {md_text(a)}" for a in f["assumptions"]))
        section_label("Affected components")
        st.write(join_or_dash(f.get("affected_components") or []))
    with tabs[1]:
        section_label(f"Source references ({len(f.get('source_references') or [])})")
        render_source_references(f.get("source_references") or [])
        section_label(f"Tool observations ({len(view.get('tool_results') or [])})")
        results = view.get("tool_results") or []
        if results:
            st.dataframe(style_frame(tool_results_frame(results), status=["Outcome", "Evidence"]), hide_index=True, width="stretch")
        else:
            st.caption("No deterministic tool results are linked to this finding; it rests on source references and agent reasoning.")
    with tabs[2]:
        section_label("Contributing agents and original severities")
        originals = f.get("original_severities") or {}
        agents = f.get("contributing_agents") or [f.get("agent_id")]
        emit_html("".join(f'<span class="art-chip" style="color:#334155;background:#e2e8f0">{esc(AGENT_LABELS.get(a, a))}</span>'
                          f'{severity_chip(originals[a]) if a in originals else ""} ' for a in agents))
        section_label("Agent reasoning")
        by_agent = f.get("reasoning_by_agent") or {}
        if by_agent:
            for agent_id, text in by_agent.items():
                st.markdown(f"**{AGENT_LABELS.get(agent_id, agent_id)}**: {md_text(text)}")
        else:
            st.write(f.get("reasoning_summary") or "No reasoning summary recorded.")
        section_label("Moderator")
        emit_html(status_badge(f.get("moderator_status")))
        st.write(f.get("moderator_notes") or "No moderator notes.")
        if f.get("merged_from"):
            st.caption("Merged from: " + ", ".join(f["merged_from"]))
    with tabs[3]:
        section_label("Human decisions")
        decisions = view.get("decisions") or []
        if decisions:
            st.dataframe(decisions_frame(decisions), hide_index=True, width="stretch")
        else:
            st.caption("No architect decisions recorded for this finding.")
        section_label("Rebuttal exchanges")
        rebuttal_view(view.get("rebuttals") or [])
    with tabs[4]:
        section_label("Finding history")
        emit_html(_history_html(f.get("history") or []))
        adrs = view.get("adrs") or []
        if adrs:
            section_label("Linked ADRs")
            titles = adr_titles or {}
            st.markdown("\n".join(f"- {md_text(titles.get(a, a))}" for a in adrs))


def legend(items: Iterable[tuple[str, str, str]]) -> None:
    """Colour legend from ``(label, fill, border)`` tuples."""
    emit_html("".join(f'<span class="art-legend"><span class="sw" style="background:{fill};border-color:{border}"></span>{esc(text)}</span>'
                      for text, fill, border in items))

