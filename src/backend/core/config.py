"""Application settings.

Settings are loaded from environment variables / ``.env`` (via pydantic-settings) and
can be edited at runtime from the Settings page.  Non-secret overrides are persisted to
``<data_dir>/settings.json``; API keys entered through the UI live in process memory only.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SRC_DIR = Path(__file__).resolve().parents[2]  # src/: code, knowledge/, demo/
ROOT_DIR = SRC_DIR.parent  # project root: .env, data/

SUPPORTED_PROVIDERS = ("mock", "anthropic", "openai", "bedrock")

# Fields that may be edited from the Settings page (secrets handled separately).
EDITABLE_FIELDS = (
    "llm_provider",
    "anthropic_model",
    "openai_model",
    "aws_region",
    "aws_profile",
    "bedrock_model_id",
    "demo_mode",
    "max_concurrent_agents",
    "max_review_rounds",
    "max_input_tokens",
    "max_output_tokens",
    "enable_checkov",
    "llm_timeout_seconds",
    "llm_max_retries",
)
SECRET_FIELDS = ("anthropic_api_key", "openai_api_key", "aws_bearer_token_bedrock")


class Settings(BaseSettings):
    """Typed application settings (environment variable names are the upper-cased fields)."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    llm_provider: str = "mock"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o"
    aws_region: str = "us-east-1"
    aws_profile: str | None = None
    # Bedrock API key (bearer token). When set it is used instead of the AWS credential chain.
    aws_bearer_token_bedrock: SecretStr | None = None
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    demo_mode: bool = True
    max_concurrent_agents: int = Field(default=6, ge=1, le=16)
    max_review_rounds: int = Field(default=3, ge=1, le=10)
    max_input_tokens: int = Field(default=60000, ge=1000)
    max_output_tokens: int = Field(default=16000, ge=256)
    llm_timeout_seconds: int = Field(default=120, ge=5)
    llm_max_retries: int = Field(default=3, ge=0, le=8)

    local_data_dir: str = "data"
    fastapi_port: int = 8000
    streamlit_port: int = 8501
    bind_host: str = "127.0.0.1"

    max_upload_mb: int = Field(default=25, ge=1)
    max_pdf_pages: int = Field(default=200, ge=1)
    max_files_per_project: int = 60
    enable_checkov: bool = True
    checkov_timeout_seconds: int = 120
    max_disputes_per_finding: int = 2
    mock_latency_seconds: float = Field(default=1.0, ge=0.0, le=10.0)
    snapshot_debounce_seconds: float = 0.4

    @property
    def data_dir(self) -> Path:
        path = Path(self.local_data_dir)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def knowledge_dir(self) -> Path:
        return SRC_DIR / "knowledge"

    @property
    def demo_dir(self) -> Path:
        return SRC_DIR / "demo"

    def public_view(self) -> dict[str, Any]:
        """Settings safe to return from the API (secrets reduced to a configured flag)."""
        data = self.model_dump(mode="json", exclude=set(SECRET_FIELDS))
        data["anthropic_api_key_configured"] = bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())
        data["openai_api_key_configured"] = bool(self.openai_api_key and self.openai_api_key.get_secret_value())
        data["bedrock_api_key_configured"] = bool(self.aws_bearer_token_bedrock and self.aws_bearer_token_bedrock.get_secret_value())
        data["data_dir"] = str(self.data_dir)
        return data


class SettingsStore:
    """Thread-safe holder of the current :class:`Settings` with local persistence."""

    def __init__(self, base: Settings | None = None, persist: bool = True) -> None:
        self._lock = threading.RLock()
        self._persist = persist
        self._settings = base or Settings()
        if persist:
            self._load_overrides()

    @property
    def path(self) -> Path:
        return self._settings.data_dir / "settings.json"

    def _load_overrides(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        allowed = {k: v for k, v in raw.items() if k in EDITABLE_FIELDS}
        # Environment variables win for provider-critical values only when explicitly set at
        # startup; persisted UI overrides apply on top of ``.env`` defaults.
        try:
            self._settings = Settings.model_validate({**self._settings.model_dump(), **allowed})
        except ValueError:
            return

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def update(self, changes: dict[str, Any]) -> Settings:
        """Apply edits.  Unknown keys raise ``KeyError``; invalid values raise ``ValueError``."""
        with self._lock:
            allowed = set(EDITABLE_FIELDS) | set(SECRET_FIELDS)
            unknown = set(changes) - allowed
            if unknown:
                raise KeyError(f"Settings not editable: {sorted(unknown)}")
            merged = self._settings.model_dump()
            merged.update({k: v for k, v in changes.items() if v is not None or k in EDITABLE_FIELDS})
            for key in SECRET_FIELDS:
                if key in changes and changes[key]:
                    merged[key] = SecretStr(changes[key])
                elif key not in changes:
                    merged[key] = getattr(self._settings, key)
            if merged["llm_provider"] not in SUPPORTED_PROVIDERS:
                raise ValueError(f"llm_provider must be one of {SUPPORTED_PROVIDERS}")
            self._settings = Settings.model_validate(merged)
            if self._persist:
                self._save_overrides()
            return self._settings

    def _save_overrides(self) -> None:
        payload = {k: getattr(self._settings, k) for k in EDITABLE_FIELDS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
