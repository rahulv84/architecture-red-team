"""Tests for scripts/demo_reset.py (always against temporary directories)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from backend.core.config import ROOT_DIR, SRC_DIR, Settings

_spec = importlib.util.spec_from_file_location("demo_reset", SRC_DIR / "scripts" / "demo_reset.py")
assert _spec and _spec.loader
demo_reset = importlib.util.module_from_spec(_spec)
sys.modules["demo_reset"] = demo_reset  # dataclasses resolve their module by name
_spec.loader.exec_module(demo_reset)


def _data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    for name in demo_reset.STATE_DIRS:
        (data / name).mkdir(parents=True)
        (data / name / ".gitkeep").write_text("")
    (data / "README.md").write_text("docs")
    (data / "uploads" / "prj-1" / "art-1").mkdir(parents=True)
    (data / "uploads" / "prj-1" / "art-1" / "diagram.puml").write_text("@startuml\n@enduml")
    (data / "checkpoints" / "langgraph.sqlite").write_bytes(b"x" * 10)
    (data / "settings.json").write_text(json.dumps({"bedrock_model_id": "old-model"}))
    return data


def test_clean_removes_state_but_keeps_placeholders_and_readme(tmp_path):
    data = _data_dir(tmp_path)
    plan = demo_reset.plan_clean(data, keep_settings=False)
    assert plan.total_bytes > 0
    assert demo_reset.execute_clean(plan) == []
    for name in demo_reset.STATE_DIRS:
        assert [p.name for p in (data / name).iterdir()] == [".gitkeep"]
    assert (data / "README.md").exists() and not (data / "settings.json").exists()


def test_keep_settings_and_plan_is_side_effect_free(tmp_path):
    data = _data_dir(tmp_path)
    plan = demo_reset.plan_clean(data, keep_settings=True)
    assert data / "settings.json" not in plan.targets
    assert (data / "uploads" / "prj-1").exists()  # planning alone deletes nothing


@pytest.mark.parametrize("target", ["repo", "missing", "unrelated"])
def test_refuses_unsafe_data_dirs(tmp_path, target):
    unrelated = tmp_path / "photos"
    unrelated.mkdir()
    path = {"repo": ROOT_DIR, "missing": tmp_path / "nope", "unrelated": unrelated}[target]
    assert demo_reset.unsafe_data_dir_reason(path) is not None
    assert demo_reset.unsafe_data_dir_reason(_data_dir(tmp_path)) is None


def test_fixture_check_flags_parse_failures_and_stray_files(tmp_path):
    folder = tmp_path / "fixtures" / "demo1"
    folder.mkdir(parents=True)
    (folder / "scenario.json").write_text(json.dumps({"files": [{"filename": "diagram.puml"}, {"filename": "main.tf"}]}))
    (folder / "diagram.puml").write_text('@startuml\ncomponent "API" as api\n@enduml\n')
    (folder / "main.tf").write_text("resource {{{ broken")
    (folder / "old.mmd").write_text("graph TD\nA-->B")
    checks = {c.name: c for c in demo_reset.check_fixtures(tmp_path)}
    assert not checks["fixtures/demo1"].ok and "main.tf" in checks["fixtures/demo1"].detail
    extra = checks["fixtures/demo1 extra files"]
    assert extra.ok and extra.warning and "old.mmd" in extra.detail


def test_persisted_settings_that_override_env_are_reported(tmp_path):
    data = _data_dir(tmp_path)
    check = demo_reset.check_persisted_settings(Settings(local_data_dir=str(data), bedrock_model_id="new-model"), data)
    assert check is not None and check.warning and "bedrock_model_id" in check.detail


def test_mock_provider_is_a_warning_not_a_failure(tmp_path):
    check = demo_reset.check_provider(Settings(local_data_dir=str(tmp_path), llm_provider="mock"), connectivity=False)
    assert check.ok and check.warning
