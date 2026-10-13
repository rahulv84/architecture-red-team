"""Streamlit entry point: page configuration, global CSS, navigation and the sidebar review picker.

Run with ``streamlit run src/frontend/app.py`` (or ``python src/run.py`` from the repository root).  The app is a
pure REST client; the backend URL comes from ``ART_BACKEND_URL``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make ``src`` importable so that ``frontend.*`` resolves however Streamlit was launched.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

from frontend.components import ui  # noqa: E402

st.set_page_config(page_title="Architecture Red Team", page_icon=":material/shield:", layout="wide", initial_sidebar_state="expanded")
ui.inject_css()

pages = {
    key: st.Page(spec.path, title=spec.title, icon=spec.icon, url_path=spec.url_path, default=(key == "dashboard"))
    for key, spec in ui.PAGES.items()
}
navigation = st.navigation(list(pages.values()))
st.session_state[ui.SHELL_KEY] = True
deep_link = st.session_state.pop(ui.DEEPLINK_KEY, None)
if deep_link in pages:
    st.switch_page(pages[deep_link])
ui.sidebar_review_picker()
ui.sidebar_footer()
navigation.run()
