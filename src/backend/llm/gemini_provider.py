"""Google Gemini adapter (``google-genai`` SDK, ``generate_content``; JSON mode for structured output).

Structured calls set ``response_mime_type=application/json`` and rely on the schema that
``LLMProvider.generate_structured`` already places in the system instruction, followed by Pydantic
validation and one repair attempt.  The native ``response_json_schema`` parameter is deliberately
not used: Gemini accepts only a subset of JSON Schema and may reject large Pydantic schemas
(``$defs``/``$ref``, ``default``), which would fail every review instead of one call.

Defaults: ``gemini-3.8-flash`` (mid tier, stable); ``gemini-3.1-pro-preview`` is the top tier.
Thinking tokens count toward ``max_output_tokens``; a ``MAX_TOKENS`` finish on a structured call
raises instead of returning partial JSON.
"""
from __future__ import annotations

from typing import Any

from ..core.exceptions import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .base_provider import LLMProvider, ProviderCapabilities, ProviderValidation, RawCompletion

GEMINI_MID_MODEL = "gemini-3.8-flash"
GEMINI_TOP_MODEL = "gemini-3.1-pro-preview"

_BLOCKED_FINISH = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


class GeminiProvider(LLMProvider):
    name = "gemini"

    @property
    def model_id(self) -> str:
        return self.settings.gemini_model

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name, model_id=self.model_id, structured_output=True, vision=True,
            notes=["Vision support assumes the configured model accepts image input.",
                   f"Mid tier: {GEMINI_MID_MODEL}; top tier: {GEMINI_TOP_MODEL}."],
        )

    def _api_key(self) -> str:
        key = self.settings.gemini_api_key.get_secret_value() if self.settings.gemini_api_key else ""
        if not key:
            raise ProviderConfigError("GEMINI_API_KEY is not configured.")
        return key

    def _client(self) -> Any:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ProviderConfigError("The 'google-genai' package is not installed.") from exc
        # SDK retries are disabled; LLMProvider._call_with_retries owns backoff.  Timeout is in milliseconds.
        http_options = types.HttpOptions(timeout=int(self.settings.llm_timeout_seconds) * 1000,
                                         retry_options=types.HttpRetryOptions(attempts=1))
        return genai.Client(api_key=self._api_key(), http_options=http_options)

    def validate_configuration(self) -> ProviderValidation:
        try:
            self._api_key()
            from google import genai  # noqa: F401
        except ProviderError as exc:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message=exc.message)
        except ImportError:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message="The 'google-genai' package is not installed.")
        return ProviderValidation(ok=True, provider=self.name, model_id=self.model_id, message="API key present.", capabilities=self.capabilities())

    def check_connectivity(self) -> ProviderValidation:
        base = self.validate_configuration()
        if not base.ok:
            return base
        try:
            # Thinking models spend part of the budget before answering, so allow more than a few tokens.
            self._complete("Reply with the single word: ok", "ping", max_tokens=256, schema=None, schema_name="ping")
        except ProviderError as exc:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message=f"{exc.code}: {exc.message}", network_checked=True)
        return ProviderValidation(ok=True, provider=self.name, model_id=self.model_id, message="Connectivity check succeeded.",
                                  capabilities=self.capabilities(), network_checked=True)

    def _translate(self, exc: Exception) -> ProviderError:
        import httpx
        from google.genai import errors

        if isinstance(exc, httpx.TimeoutException):
            return ProviderTimeoutError("Gemini request timed out.")
        if isinstance(exc, httpx.TransportError):
            return ProviderTimeoutError("Could not reach the Gemini API (connection error).")
        if not isinstance(exc, errors.APIError):
            return ProviderError(f"Gemini client error: {exc.__class__.__name__}.")
        code, status = exc.code, exc.status or ""
        detail = (exc.message or "")[:300]
        # An invalid key is reported as HTTP 400 INVALID_ARGUMENT ("API key not valid").
        if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED") or "api key" in detail.lower():
            return ProviderAuthError("Gemini rejected the API key or permissions.")
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            hard = self._hard_quota(exc.details)
            if hard:
                return ProviderError(hard)  # not retryable: retries would only burn more of the quota
            return ProviderRateLimitError("Gemini rate limit or quota reached.")
        if code in (503, 504) or status in ("UNAVAILABLE", "DEADLINE_EXCEEDED"):
            return ProviderTimeoutError(f"Gemini temporarily unavailable (HTTP {code}).")
        return ProviderError(f"Gemini API error: {status or exc.__class__.__name__} (HTTP {code}){': ' + detail if detail else '.'}")

    def _hard_quota(self, details: Any) -> str | None:
        """Message for a daily or zero quota (e.g. the free tier); ``None`` for a per-minute limit worth retrying."""
        error = details.get("error", {}) if isinstance(details, dict) else {}
        items = error.get("details", []) if isinstance(error, dict) else []
        violations = [v for d in items if isinstance(d, dict) for v in d.get("violations", []) if isinstance(v, dict)]
        retry_delay = next((str(d.get("retryDelay", "")) for d in items if isinstance(d, dict) and "retryDelay" in d), "")
        try:
            long_wait = float(retry_delay.rstrip("s") or 0) > 300
        except ValueError:
            long_wait = False
        for v in violations:
            quota_id, value = str(v.get("quotaId", "")), str(v.get("quotaValue", ""))
            if "PerDay" in quota_id or value == "0" or long_wait:
                tier = " (free tier)" if "FreeTier" in quota_id else ""
                limit = f" limit {value} -" if value else ""
                return (f"Gemini quota exhausted for model '{self.model_id}'{tier}:{limit} {quota_id or 'quota'}. "
                        "Enable billing on the Google AI project, wait for the quota to reset, or choose another model.")
        return None

    def _generate(self, contents: Any, config: dict[str, Any]) -> Any:
        import httpx
        from google.genai import errors

        client = self._client()
        try:
            return client.models.generate_content(model=self.model_id, contents=contents, config=config)
        except (errors.APIError, httpx.HTTPError) as exc:
            raise self._translate(exc) from exc

    @staticmethod
    def _finish_reason(resp: Any) -> str:
        candidates = getattr(resp, "candidates", None) or []
        reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        return str(getattr(reason, "name", reason or ""))

    def _result(self, resp: Any, *, structured: bool, schema_name: str, max_tokens: int) -> RawCompletion:
        feedback = getattr(resp, "prompt_feedback", None)
        block = getattr(feedback, "block_reason", None) if feedback else None
        if block:
            raise ProviderError(f"Gemini blocked the prompt ({getattr(block, 'name', block)}).")
        finish = self._finish_reason(resp)
        if finish in _BLOCKED_FINISH:
            raise ProviderError(f"Gemini stopped the response for '{schema_name}' ({finish}).")
        if structured and finish == "MAX_TOKENS":
            # Partial JSON may still validate (all fields default) and silently become "no findings".
            raise ProviderError(
                f"Gemini response for '{schema_name}' was truncated at max_tokens={max_tokens} "
                "(the model's thinking counts toward this budget). Increase MAX_OUTPUT_TOKENS."
            )
        candidates = getattr(resp, "candidates", None) or []
        content = getattr(candidates[0], "content", None) if candidates else None
        parts = getattr(content, "parts", None) or []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        usage = getattr(resp, "usage_metadata", None)
        out_tokens = getattr(usage, "candidates_token_count", None)
        thoughts = getattr(usage, "thoughts_token_count", None)
        if out_tokens is not None or thoughts is not None:
            out_tokens = (out_tokens or 0) + (thoughts or 0)  # thinking is billed as output, as with Claude
        return RawCompletion(text=text, input_tokens=getattr(usage, "prompt_token_count", None), output_tokens=out_tokens)

    def _complete(self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None, schema_name: str) -> RawCompletion:
        config: dict[str, Any] = {"system_instruction": system, "max_output_tokens": max_tokens}
        if schema is not None:
            config["response_mime_type"] = "application/json"
        resp = self._generate(user, config)
        return self._result(resp, structured=schema is not None, schema_name=schema_name, max_tokens=max_tokens)

    def _complete_image(self, prompt: str, image: bytes, media_type: str, *, max_tokens: int) -> RawCompletion:
        from google.genai import types

        contents = [types.Part.from_bytes(data=image, mime_type=media_type), prompt]
        resp = self._generate(contents, {"max_output_tokens": max_tokens})
        return self._result(resp, structured=False, schema_name="image", max_tokens=max_tokens)
