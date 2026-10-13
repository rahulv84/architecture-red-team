"""OpenAI adapter (Chat Completions with JSON-object response format)."""
from __future__ import annotations

import base64
from typing import Any

from ..core.exceptions import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .base_provider import LLMProvider, ProviderCapabilities, ProviderValidation, RawCompletion


class OpenAIProvider(LLMProvider):
    name = "openai"

    @property
    def model_id(self) -> str:
        return self.settings.openai_model

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name, model_id=self.model_id, structured_output=True, vision=True,
            notes=["Vision support assumes the configured model accepts image input."],
        )

    def _api_key(self) -> str:
        key = self.settings.openai_api_key.get_secret_value() if self.settings.openai_api_key else ""
        if not key:
            raise ProviderConfigError("OPENAI_API_KEY is not configured.")
        return key

    def _client(self) -> Any:
        try:
            import openai
        except ImportError as exc:
            raise ProviderConfigError("The 'openai' package is not installed.") from exc
        return openai.OpenAI(api_key=self._api_key(), timeout=float(self.settings.llm_timeout_seconds), max_retries=0)

    def validate_configuration(self) -> ProviderValidation:
        try:
            self._api_key()
            import openai  # noqa: F401
        except ProviderError as exc:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message=exc.message)
        except ImportError:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message="The 'openai' package is not installed.")
        return ProviderValidation(ok=True, provider=self.name, model_id=self.model_id, message="API key present.", capabilities=self.capabilities())

    def check_connectivity(self) -> ProviderValidation:
        base = self.validate_configuration()
        if not base.ok:
            return base
        try:
            self._complete("Reply with the single word: ok", "ping", max_tokens=8, schema=None, schema_name="ping")
        except ProviderError as exc:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message=f"{exc.code}: {exc.message}", network_checked=True)
        return ProviderValidation(ok=True, provider=self.name, model_id=self.model_id, message="Connectivity check succeeded.",
                                  capabilities=self.capabilities(), network_checked=True)

    def _translate(self, exc: Exception) -> ProviderError:
        import openai

        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return ProviderAuthError("OpenAI rejected the API key or permissions.")
        if isinstance(exc, openai.RateLimitError):
            return ProviderRateLimitError("OpenAI rate limit reached.")
        if isinstance(exc, openai.APITimeoutError):
            return ProviderTimeoutError("OpenAI request timed out.")
        if isinstance(exc, openai.APIConnectionError):
            return ProviderTimeoutError("Could not reach the OpenAI API (connection error).")
        return ProviderError(f"OpenAI API error: {exc.__class__.__name__} (HTTP {getattr(exc, 'status_code', None)}).")

    def _complete(self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None, schema_name: str) -> RawCompletion:
        import openai

        client = self._client()
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": max_tokens,
        }
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            raise self._translate(exc) from exc
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return RawCompletion(text=text, input_tokens=getattr(usage, "prompt_tokens", None), output_tokens=getattr(usage, "completion_tokens", None))

    def _complete_image(self, prompt: str, image: bytes, media_type: str, *, max_tokens: int) -> RawCompletion:
        import openai

        client = self._client()
        data_url = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
        content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_url}}]
        try:
            resp = client.chat.completions.create(
                model=self.model_id, messages=[{"role": "user", "content": content}], max_completion_tokens=max_tokens
            )
        except openai.OpenAIError as exc:
            raise self._translate(exc) from exc
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return RawCompletion(text=text, input_tokens=getattr(usage, "prompt_tokens", None), output_tokens=getattr(usage, "completion_tokens", None))
