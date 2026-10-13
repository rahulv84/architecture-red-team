"""Locally editable settings (secrets are write-only and never returned)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...core.exceptions import ValidationFailed
from ...llm.base_provider import LIMITER
from ...models.schemas import SettingsUpdate
from ...tools.checkov_adapter import checkov_available
from ..deps import Container

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings(c: Container) -> dict[str, Any]:
    return c.settings_store.get().public_view()


@router.put("/settings")
def update_settings(payload: SettingsUpdate, c: Container) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise ValidationFailed("No settings supplied.")
    try:
        settings = c.settings_store.update(changes)
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(str(exc)) from exc
    LIMITER.resize(settings.max_concurrent_agents)
    return settings.public_view()


@router.get("/settings/tools")
def tool_availability(c: Container) -> dict[str, Any]:
    exe = checkov_available()
    s = c.settings_store.get()
    return {
        "builtin": {"available": True, "description": "Deterministic security/resilience/cost/configuration/dependency checks"},
        "checkov": {"available": exe is not None, "enabled": s.enable_checkov,
                    "detail": "Detected on PATH / venv." if exe else "Not installed; its coverage is reported as NOT_EXECUTED."},
    }
