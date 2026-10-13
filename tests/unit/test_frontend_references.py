"""Static guard: every ``ui.<name>`` / ``api.<name>`` used by the Streamlit pages must exist.

Pages have branches that only run in some modes (for example live-provider warnings), so a typo there is invisible to
demo-mode smoke tests.  This test walks the AST of every page and component and checks attribute references.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src"
FRONTEND = ROOT / "frontend"


def _module_attrs(modname: str) -> set[str]:
    import importlib

    module = importlib.import_module(modname)
    return set(dir(module))


def _referenced(path: Path, alias: str) -> set[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == alias:
            found.add((node.attr, node.lineno))
    return found


@pytest.mark.parametrize("page", sorted((FRONTEND / "pages").glob("*.py")), ids=lambda p: p.name)
def test_pages_only_reference_existing_ui_and_api_helpers(page: Path):
    ui_attrs = _module_attrs("frontend.components.ui")
    api_attrs = _module_attrs("frontend.api_client")
    missing = [f"ui.{n} (line {ln})" for n, ln in _referenced(page, "ui") if n not in ui_attrs]
    missing += [f"api.{n} (line {ln})" for n, ln in _referenced(page, "api") if n not in api_attrs]
    assert not missing, f"{page.name} references undefined helpers: {missing}"


def test_app_and_components_reference_existing_helpers():
    ui_attrs = _module_attrs("frontend.components.ui")
    missing = [f"ui.{n}" for n, _ in _referenced(FRONTEND / "app.py", "ui") if n not in ui_attrs]
    assert not missing, missing
