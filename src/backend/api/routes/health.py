"""Platform endpoints: health, configuration and provider validation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...core.exceptions import ValidationFailed
from ...llm.provider_factory import create_provider, provider_catalog
from ...models.domain import ALL_AGENT_IDS, utcnow
from ...models.schemas import ProviderValidateRequest
from ...tools.checkov_adapter import checkov_available
from ..deps import Container

router = APIRouter(tags=["platform"])
VERSION = "1.0.0"


@router.get("/health")
def health(c: Container) -> dict[str, Any]:
    s = c.settings_store.get()
    return {
        "status": "ok", "version": VERSION, "time": utcnow().isoformat(), "database": "sqlite-in-memory",
        "provider": s.llm_provider, "demo_mode_default": s.demo_mode, "snapshot_restore": c.restore_info,
        "checkpoint_store": str(c.paths.checkpoint_db.name),
    }


@router.get("/config")
def config(c: Container) -> dict[str, Any]:
    s = c.settings_store.get()
    return {
        "settings": s.public_view(),
        "tools": {"checkov_installed": checkov_available() is not None, "checkov_enabled": s.enable_checkov},
        "agents": list(ALL_AGENT_IDS),
        "local_only_notice": "This POC binds to loopback and has no authentication; do not expose it publicly.",
        "live_mode_warning": "Live-model analysis sends relevant document content to the selected model provider.",
    }


@router.get("/providers")
def providers(c: Container) -> list[dict[str, Any]]:
    return provider_catalog(c.settings_store.get())


@router.post("/providers/validate")
def validate_provider(payload: ProviderValidateRequest, c: Container) -> dict[str, Any]:
    s = c.settings_store.get()
    try:
        provider = create_provider(payload.provider, s)
    except Exception as exc:
        raise ValidationFailed(str(exc)) from exc
    result = provider.check_connectivity() if payload.check_connectivity else provider.validate_configuration()
    return result.model_dump()
