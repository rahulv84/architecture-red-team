"""Tests for src/scripts/package_submission.py (always against temporary directories)."""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

from backend.core.config import SRC_DIR

_spec = importlib.util.spec_from_file_location("package_submission", SRC_DIR / "scripts" / "package_submission.py")
assert _spec and _spec.loader
pkg = importlib.util.module_from_spec(_spec)
sys.modules["package_submission"] = pkg  # dataclasses resolve their module by name
_spec.loader.exec_module(pkg)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel, text in {
        "Agent.md": "# Agent", "README.md": "# Readme", "src/app.py": "print('hi')\n", "tests/test_x.py": "def test(): pass\n",
        "docs/design.md": "# Design", ".env.example": "OPENAI_API_KEY=\n", ".env": "OPENAI_API_KEY=real-secret-value-123\n",
        "data/uploads/a.txt": "state", "src/__pycache__/app.cpython-311.pyc": "x", "data/settings.json": "{}",
        "notes/private.md": "not part of the submission",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_only_submission_files_are_selected(tmp_path):
    root = _project(tmp_path)
    rel = {p.relative_to(root).as_posix() for p in pkg.candidate_files(root)}
    assert {"Agent.md", "README.md", "src/app.py", "tests/test_x.py", "docs/design.md", ".env.example"} <= rel
    assert not {".env", "data/uploads/a.txt", "data/settings.json", "notes/private.md"} & rel
    assert not any("__pycache__" in r for r in rel)


def test_values_from_real_env_block_packaging_without_being_logged(tmp_path):
    root = _project(tmp_path)
    (root / "src" / "config.py").write_text('KEY = "real-secret-value-123"\n', encoding="utf-8")
    values = pkg.env_secret_values(root / ".env")
    hits = pkg.scan(root, pkg.candidate_files(root), values)
    assert [(h.path, h.blocking) for h in hits] == [("src/config.py", True)]
    assert "real-secret-value-123" not in repr(hits)


def test_key_formats_block_but_test_placeholders_and_fake_passwords_do_not(tmp_path):
    root = _project(tmp_path)
    fake_aws_key = "AKIA" + "ABCDEFGHIJKLMNOP"  # assembled at runtime so this file itself passes the scan
    fake_google_key = "AIza" + "B" * 35
    (root / "src" / "keys.py").write_text(f'A = "{fake_aws_key}"\nB = "sk-ant-test-123456789012345"\nC = "{fake_google_key}"\n',
                                          encoding="utf-8")
    (root / "src" / "main.tf").write_text("  pass" + 'word = "Sup3rS3cret!2025"\n', encoding="utf-8")
    hits = {(h.path, h.line): h for h in pkg.scan(root, pkg.candidate_files(root), [])}
    assert hits[("src/keys.py", 1)].blocking and ("src/keys.py", 2) not in hits and hits[("src/keys.py", 3)].blocking
    assert not hits[("src/main.tf", 1)].blocking


def test_build_writes_folder_and_zip_with_named_root(tmp_path):
    root = _project(tmp_path)
    target = tmp_path / "dist" / "Jane_Doe_123456"
    archive = pkg.build(root, pkg.candidate_files(root), target, make_zip=True)
    assert (target / "Agent.md").exists() and not (target / ".env").exists()
    names = zipfile.ZipFile(archive).namelist()
    assert "Jane_Doe_123456/Agent.md" in names and all(n.startswith("Jane_Doe_123456/") for n in names)


def test_folder_name_must_follow_convention():
    assert pkg.NAME_RE.match("Rahul_Verma_694836")
    assert not pkg.NAME_RE.match("Rahul Verma") and not pkg.NAME_RE.match("Rahul_694836")
    assert pkg.main(["--name", "bad name"]) == 2
