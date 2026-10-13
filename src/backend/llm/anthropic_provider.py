"""Anthropic Claude adapter (Messages API; structured output via a ``submit_result`` tool).

Current Claude models (Opus 5.5, Sonnet 5.5, Fable 5.1) reject forced ``tool_choice``
(``{"type": "tool"}`` / ``{"type": "any"}``) with HTTP 400, so the tool is offered with
``tool_choice: auto`` plus an explicit instruction.  If the model answers in plain text
instead, ``LLMProvider.generate_structured`` still extracts and validates the JSON.
"""
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

_TOOL_INSTRUCTION = (
    "\n\nDeliver your answer by calling the `submit_result` tool exactly once with the JSON object as its input. "
    "Do not answer in plain text."
)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    @property
    def model_id(self) -> str:
        return self.settings.anthropic_model

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name, model_id=self.model_id, structured_output=True, vision=True,
            notes=["Vision support assumes the configured model accepts image input."],
        )

    def _api_key(self) -> str:
        key = self.settings.anthropic_api_key.get_secret_value() if self.settings.anthropic_api_key else ""
        if not key:
            raise ProviderConfigError("ANTHROPIC_API_KEY is not configured.")
        return key

    def _client(self) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderConfigError("The 'anthropic' package is not installed.") from exc
        return anthropic.Anthropic(api_key=self._api_key(), timeout=float(self.settings.llm_timeout_seconds), max_retries=0)

    def validate_configuration(self) -> ProviderValidation:
        try:
            self._api_key()
            import anthropic  # noqa: F401
        except ProviderError as exc:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message=exc.message)
        except ImportError:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message="The 'anthropic' package is not installed.")
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
        import anthropic

        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return ProviderAuthError("Anthropic rejected the API key or permissions.")
        if isinstance(exc, anthropic.RateLimitError):
            return ProviderRateLimitError("Anthropic rate limit reached.")
        if isinstance(exc, anthropic.APITimeoutError):
            return ProviderTimeoutError("Anthropic request timed out.")
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderTimeoutError("Could not reach the Anthropic API (connection error).")
        status = getattr(exc, "status_code", None)
        if status in (429, 529):
            return ProviderRateLimitError(f"Anthropic temporarily unavailable (HTTP {status}).")
        detail = self._error_detail(exc)
        return ProviderError(f"Anthropic API error: {exc.__class__.__name__} (HTTP {status}){': ' + detail if detail else '.'}")

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        """The API's own error message (e.g. an unsupported parameter); never contains the key."""
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                return err["message"][:300]
        return ""

    def _complete(self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None, schema_name: str) -> RawCompletion:
        import anthropic

        client = self._client()
        kwargs: dict[str, Any] = {
            "model": self.model_id, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if schema is not None:
            kwargs["system"] = system + _TOOL_INSTRUCTION
            kwargs["tools"] = [{"name": "submit_result", "description": "Submit the structured result.", "input_schema": schema}]
            kwargs["tool_choice"] = {"type": "auto"}
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise self._translate(exc) from exc
        if schema is not None and getattr(resp, "stop_reason", None) == "max_tokens":
            # A truncated tool call arrives as an empty/partial input that can still validate (all fields
            # default), which would silently turn into "no findings".  Fail loudly instead.
            raise ProviderError(
                f"Anthropic response for '{schema_name}' was truncated at max_tokens={max_tokens} "
                "(the model's thinking counts toward this budget). Increase MAX_OUTPUT_TOKENS."
            )
        text = ""
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                import json

                text = json.dumps(block.input)
                break
            if getattr(block, "type", "") == "text":
                text += block.text
        usage = getattr(resp, "usage", None)
        return RawCompletion(text=text, input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None))

    def _complete_image(self, prompt: str, image: bytes, media_type: str, *, max_tokens: int) -> RawCompletion:
        import anthropic

        client = self._client()
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image).decode("ascii")}},
            {"type": "text", "text": prompt},
        ]
        try:
            resp = client.messages.create(model=self.model_id, max_tokens=max_tokens, messages=[{"role": "user", "content": content}])
        except anthropic.APIError as exc:
            raise self._translate(exc) from exc
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return RawCompletion(text=text, input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None))
