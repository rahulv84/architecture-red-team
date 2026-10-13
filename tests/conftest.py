"""Shared pytest fixtures: isolated containers (in-memory DB, temp data dir, zero mock latency)."""
from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.core.config import Settings  # noqa: E402
from backend.core.lifecycle import AppContainer, set_container  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.models.schemas import ReviewCreate  # noqa: E402


def make_container(tmp_path: Path, **overrides: object) -> AppContainer:
    settings = Settings(local_data_dir=str(tmp_path / "data"), mock_latency_seconds=0.0, **overrides)  # type: ignore[arg-type]
    c = AppContainer(settings, persist_settings=False, autosave=False)
    set_container(c)
    c.startup()
    return c


@pytest.fixture()
def container(tmp_path: Path) -> Iterator[AppContainer]:
    c = make_container(tmp_path)
    yield c
    c.tasks.shutdown()
    c.db.dispose()
    set_container(None)


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[tuple[TestClient, AppContainer]]:
    c = make_container(tmp_path)
    app = create_app(c)
    with TestClient(app) as tc:
        yield tc, c
    set_container(None)


def start_demo_review(c: AppContainer, scenario: str = "aws-ecommerce", agents: list[str] | None = None, max_rounds: int | None = None):
    """Create a demo project + review and run it to the first human-review interrupt."""
    project = c.tables.projects.put(c.demo.new_project(scenario))
    meta, files = c.demo.scenario_files(scenario)
    result = c.ingestion.add_documents(project.project_id, files)
    assert not result.rejected
    review = c.reviews.create_review(ReviewCreate(
        project_id=project.project_id, name=meta["name"], demo_mode=True, scenario_id=scenario, enabled_agents=agents, max_rounds=max_rounds,
    ))
    c.workflow.start(review.review_id)
    wait_idle(c, review.review_id)
    return c.reviews.get(review.review_id)


def wait_idle(c: AppContainer, review_id: str, timeout: float = 90.0) -> None:
    c.tasks.wait(review_id, timeout=timeout)
    deadline = time.time() + 5
    while c.tasks.is_active(review_id) and time.time() < deadline:
        time.sleep(0.05)


def finding_by_title(c: AppContainer, review_id: str, fragment: str):
    for f in c.reviews.findings(review_id):
        if fragment.lower() in f.title.lower():
            return f
    raise AssertionError(f"No finding containing '{fragment}'")
