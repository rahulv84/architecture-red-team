"""Settings: provider, model and workflow limits, write-only API keys, provider validation and tool availability.

Endpoints: GET/PUT /settings, GET /providers, POST /providers/validate, GET /settings/tools.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from frontend import api_client as api  # noqa: E402
from frontend.components import ui  # noqa: E402

PROVIDERS = ("mock", "anthropic", "openai", "gemini", "bedrock")
READ_ONLY_KEYS = (("max_upload_mb", "Maximum upload size (MB)"), ("max_pdf_pages", "Maximum PDF pages"),
                  ("max_files_per_project", "Maximum files per project"), ("max_disputes_per_finding", "Maximum disputes per finding"),
                  ("checkov_timeout_seconds", "Checkov timeout (seconds)"), ("mock_latency_seconds", "Mock provider latency per agent (seconds)"))


def _norm(value: object) -> object:
    return "" if value is None else value


def _changed(current: dict, proposed: dict) -> dict:
    """Entries of ``proposed`` that differ from the stored settings (None and empty string count as equal)."""
    return {k: v for k, v in proposed.items() if _norm(current.get(k)) != _norm(v)}


def _key_status(settings: dict) -> None:
    anth = settings.get("anthropic_api_key_configured")
    oai = settings.get("openai_api_key_configured")
    gem = settings.get("gemini_api_key_configured")
    brk = settings.get("bedrock_api_key_configured")
    ui.emit_html(f'Anthropic API key {ui.status_badge("COMPLETED" if anth else "PENDING", "CONFIGURED" if anth else "NOT CONFIGURED")}'
                 f' &nbsp; OpenAI API key {ui.status_badge("COMPLETED" if oai else "PENDING", "CONFIGURED" if oai else "NOT CONFIGURED")}'
                 f' &nbsp; Gemini API key {ui.status_badge("COMPLETED" if gem else "PENDING", "CONFIGURED" if gem else "NOT CONFIGURED")}'
                 f' &nbsp; Bedrock API key {ui.status_badge("COMPLETED" if brk else "PENDING", "CONFIGURED" if brk else "NOT CONFIGURED")}')


def _general_form(settings: dict) -> None:
    with st.form("settings_form"):
        ui.section_label("Model provider")
        a, b = st.columns(2)
        provider = a.selectbox("Default provider", PROVIDERS, index=PROVIDERS.index(settings["llm_provider"]) if settings["llm_provider"] in PROVIDERS else 0)
        demo_mode = b.toggle("Offline demo mode by default", value=settings["demo_mode"],
                             help="New reviews default to the deterministic mock provider; live mode sends document content to the provider.")
        c, d, gm = st.columns(3)
        anthropic_model = c.text_input("Anthropic model id", value=settings["anthropic_model"])
        openai_model = d.text_input("OpenAI model id", value=settings["openai_model"])
        gemini_model = gm.text_input("Gemini model id", value=settings["gemini_model"],
                                     help="Mid tier: gemini-3.8-flash (default). Top tier: gemini-3.1-pro-preview.")
        e, f, g = st.columns(3)
        bedrock_model = e.text_input("Bedrock model id", value=settings["bedrock_model_id"])
        aws_region = f.text_input("AWS region", value=settings["aws_region"])
        aws_profile = g.text_input("AWS profile (optional)", value=settings.get("aws_profile") or "")

        ui.section_label("Workflow limits")
        h, i, j = st.columns(3)
        concurrent = h.number_input("Max concurrent agents", 1, 16, int(settings["max_concurrent_agents"]), step=1)
        rounds = i.number_input("Max review rounds", 1, 10, int(settings["max_review_rounds"]), step=1)
        retries = j.number_input("LLM max retries", 0, 8, int(settings["llm_max_retries"]), step=1)
        k, n, m = st.columns(3)
        max_in = k.number_input("Max input tokens", 1000, 1_000_000, int(settings["max_input_tokens"]), step=1000)
        max_out = n.number_input("Max output tokens", 256, 200_000, int(settings["max_output_tokens"]), step=256)
        timeout = m.number_input("LLM timeout (seconds)", 5, 3600, int(settings["llm_timeout_seconds"]), step=5)
        checkov = st.toggle("Enable Checkov when installed", value=settings["enable_checkov"])

        if st.form_submit_button("Save settings", type="primary"):
            proposed = {
                "llm_provider": provider, "demo_mode": demo_mode, "anthropic_model": anthropic_model.strip(),
                "openai_model": openai_model.strip(), "gemini_model": gemini_model.strip(), "bedrock_model_id": bedrock_model.strip(), "aws_region": aws_region.strip(),
                "aws_profile": aws_profile.strip(), "max_concurrent_agents": int(concurrent), "max_review_rounds": int(rounds),
                "llm_max_retries": int(retries), "max_input_tokens": int(max_in), "max_output_tokens": int(max_out),
                "llm_timeout_seconds": int(timeout), "enable_checkov": checkov,
            }
            changes = _changed(settings, proposed)
            if not changes:
                st.info("No settings were changed.")
            elif ui.attempt(api.update_settings, changes) is not None:
                st.toast("Settings saved.")
                st.rerun()


def _keys_form(settings: dict) -> None:
    ui.section_label("API keys (write-only)")
    _key_status(settings)
    st.caption("Keys are sent to the local backend and held in its memory only. They are never persisted, returned or displayed. "
               "Alternatively set ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY or AWS_BEARER_TOKEN_BEDROCK in the backend environment. "
               "A Bedrock API key takes precedence over the AWS profile / credential chain.")
    with st.form("keys_form", clear_on_submit=True):
        a, b, g, c = st.columns(4)
        anthropic = a.text_input("Anthropic API key", type="password", autocomplete="off")
        openai = b.text_input("OpenAI API key", type="password", autocomplete="off")
        gemini = g.text_input("Gemini API key", type="password", autocomplete="off")
        bedrock = c.text_input("Bedrock API key", type="password", autocomplete="off")
        if st.form_submit_button("Save API keys"):
            keys = {k: v.strip() for k, v in (("anthropic_api_key", anthropic), ("openai_api_key", openai), ("gemini_api_key", gemini),
                                                ("aws_bearer_token_bedrock", bedrock)) if v and v.strip()}
            if not keys:
                st.info("Enter at least one key to save.")
            elif ui.attempt(api.update_settings, keys) is not None:
                st.toast("API key(s) stored in backend memory.")
                st.rerun()


def _providers_section() -> None:
    ui.section_label("Provider catalog")
    catalog = ui.fetch(api.providers)
    st.dataframe(ui.style_frame(pd.DataFrame([{
        "Provider": p["provider"], "Model": p["model_id"], "Configured": "yes" if p["configured"] else "no", "Active": "yes" if p["active"] else "",
        "Vision": "yes" if p["vision"] else "no", "Simulated": "yes" if p["simulated"] else "", "Status": p["message"],
    } for p in catalog])), hide_index=True, width="stretch")
    connectivity = st.checkbox("Also test live connectivity (makes a small network request to the provider)", value=False, key="validate_net")
    cols = st.columns(len(catalog))
    results = st.session_state.setdefault("provider_validation", {})
    for col, p in zip(cols, catalog):
        if col.button(f"Validate {p['provider']}", key=f"validate_{p['provider']}", width="stretch"):
            with st.spinner(f"Validating {p['provider']} ..."):
                result = ui.attempt(api.validate_provider, p["provider"], connectivity)
            if result is not None:
                results[p["provider"]] = result
    for name, result in results.items():
        (st.success if result.get("ok") else st.warning)(f"**{name}**: {result.get('message', '')}",
                                                         icon=":material/check_circle:" if result.get("ok") else ":material/warning:")


def _tools_section() -> None:
    ui.section_label("Deterministic tools")
    tools = ui.fetch(api.tool_availability)
    rows = []
    for key, info in tools.items():
        rows.append({"Tool": key, "Available": "yes" if info.get("available") else "no",
                     "Enabled": "" if "enabled" not in info else ("yes" if info["enabled"] else "no"),
                     "Detail": info.get("detail") or info.get("description") or ""})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if not tools.get("checkov", {}).get("available"):
        st.caption("Checkov coverage is reported as NOT_EXECUTED while it is not installed; built-in checks still run.")


ui.ensure_shell("settings")
ui.page_header("Settings", "Provider, model and workflow limits for this local installation.")
ui.local_only_notice()
settings = ui.fetch(api.get_settings)
if settings.get("llm_provider") != "mock" and not settings.get("demo_mode"):
    ui.live_mode_warning()

_general_form(settings)
st.divider()
_keys_form(settings)
st.divider()
_providers_section()
st.divider()
_tools_section()
st.divider()
ui.section_label("Working directory")
st.text_input("Data directory (read-only)", value=settings.get("data_dir", ""), disabled=True)
st.caption("Uploads, checkpoints, snapshots, reports and logs are stored here. Non-secret setting changes are saved to settings.json in this directory.")
with st.expander("Read-only runtime values"):
    st.dataframe(pd.DataFrame([{"Setting": text, "Value": str(settings.get(key, "N/A"))} for key, text in READ_ONLY_KEYS]),
                 hide_index=True, width="stretch")
