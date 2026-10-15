"""Gemini adapter request shape and response handling (offline; the SDK client is faked)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors

from backend.core.config import Settings
from backend.core.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError, ProviderTimeoutError
from backend.llm.base_provider import StructuredRequest
from backend.llm.gemini_provider import GEMINI_MID_MODEL, GeminiProvider
from backend.llm.provider_factory import create_provider, provider_catalog
from backend.models.domain import AgentReviewOutput

_KEY = "gemini-test-key-1234567890"


class _FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(finish: str, *texts: str, thought: str | None = None, block: str | None = None) -> Any:
    parts = ([SimpleNamespace(text=thought, thought=True)] if thought else []) + [SimpleNamespace(text=t, thought=None) for t in texts]
    candidate = SimpleNamespace(finish_reason=SimpleNamespace(name=finish), content=SimpleNamespace(parts=parts))
    return SimpleNamespace(
        candidates=[candidate] if not block else [],
        prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name=block)) if block else None,
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=20, thoughts_token_count=5),
    )


def _provider(monkeypatch: pytest.MonkeyPatch, responses: list[Any], **settings: Any) -> tuple[GeminiProvider, _FakeModels]:
    settings.setdefault("gemini_model", GEMINI_MID_MODEL)
    p = GeminiProvider(Settings(gemini_api_key=_KEY, llm_max_retries=0, **settings))
    fake = _FakeModels(responses)
    monkeypatch.setattr(p, "_client", lambda: SimpleNamespace(models=fake))
    return p, fake


def _request() -> StructuredRequest:
    return StructuredRequest(task="agent_review", system_prompt="sys", user_prompt="review", response_model=AgentReviewOutput)


def _api_error(code: int, status: str, message: str) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "status": status, "message": message}})


def test_default_model_is_mid_tier_and_top_tier_can_be_configured():
    # read the declared default; Settings() would pick up a developer's .env
    assert Settings.model_fields["gemini_model"].default == GEMINI_MID_MODEL == "gemini-3.8-flash"
    p = create_provider("gemini", Settings(gemini_api_key=_KEY, gemini_model="gemini-3.1-pro-preview"))
    assert p.model_id == "gemini-3.1-pro-preview" and p.validate_configuration().ok


def test_missing_key_is_reported_without_network():
    v = create_provider("gemini", Settings(gemini_api_key=None)).validate_configuration()
    assert v.ok is False and "GEMINI_API_KEY" in v.message
    cat = {p["provider"]: p for p in provider_catalog(Settings(gemini_api_key=_KEY))}
    assert cat["gemini"]["configured"] and cat["gemini"]["vision"] and _KEY not in str(cat)


def test_structured_call_uses_json_mode_and_system_instruction(monkeypatch):
    p, fake = _provider(monkeypatch, [_resp("STOP", '{"coverage_summary": "ok"}', thought="thinking...")])
    out = p.generate_structured(_request())
    assert out.parsed.coverage_summary == "ok"
    call = fake.calls[0]
    assert call["model"] == GEMINI_MID_MODEL and call["contents"] == "review"
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["system_instruction"].startswith("sys") and "JSON Schema" in call["config"]["system_instruction"]
    # thinking is counted as output, thought parts are not part of the answer
    assert out.usage.input_tokens == 10 and out.usage.output_tokens == 25 and "thinking" not in out.raw_text


def test_plain_text_call_has_no_json_mode(monkeypatch):
    p, fake = _provider(monkeypatch, [_resp("STOP", "hello ", "world")])
    text, _ = p.analyze_text("sys", "hi")
    assert text == "hello world" and "response_mime_type" not in fake.calls[0]["config"]


def test_invalid_json_gets_one_repair_attempt(monkeypatch):
    p, fake = _provider(monkeypatch, [_resp("STOP", "not json"), _resp("STOP", '```json\n{"coverage_summary": "fixed"}\n```')])
    assert p.generate_structured(_request()).parsed.coverage_summary == "fixed"
    assert "rejected by schema validation" in fake.calls[1]["contents"]


def test_truncated_structured_reply_fails_instead_of_empty_result(monkeypatch):
    p, _ = _provider(monkeypatch, [_resp("MAX_TOKENS", "{}")])
    with pytest.raises(ProviderError, match="truncated"):
        p.generate_structured(_request())


@pytest.mark.parametrize("resp", [_resp("SAFETY", ""), _resp("STOP", block="SAFETY")])
def test_safety_blocks_surface_as_errors(monkeypatch, resp):
    p, _ = _provider(monkeypatch, [resp])
    with pytest.raises(ProviderError, match="SAFETY"):
        p.generate_structured(_request())


@pytest.mark.parametrize(("exc", "expected"), [
    (_api_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), ProviderAuthError),
    (_api_error(403, "PERMISSION_DENIED", "denied"), ProviderAuthError),
    (_api_error(429, "RESOURCE_EXHAUSTED", "quota"), ProviderRateLimitError),
    (_api_error(503, "UNAVAILABLE", "overloaded"), ProviderTimeoutError),
    (httpx.ReadTimeout("slow"), ProviderTimeoutError),
    (httpx.ConnectError("down"), ProviderTimeoutError),
])
def test_errors_are_translated(monkeypatch, exc, expected):
    p, _ = _provider(monkeypatch, [exc])
    with pytest.raises(expected) as info:
        p.generate_structured(_request())
    assert _KEY not in info.value.message


def test_api_error_message_is_surfaced(monkeypatch):
    p, _ = _provider(monkeypatch, [_api_error(400, "INVALID_ARGUMENT", "Unsupported mime type")])
    with pytest.raises(ProviderError, match="Unsupported mime type"):
        p.generate_structured(_request())


def test_transient_errors_are_retried(monkeypatch):
    monkeypatch.setattr("backend.llm.base_provider.time.sleep", lambda _s: None)
    p, fake = _provider(monkeypatch, [_api_error(429, "RESOURCE_EXHAUSTED", "quota"), _resp("STOP", '{"coverage_summary": "ok"}')])
    p.settings = p.settings.model_copy(update={"llm_max_retries": 1})
    out = p.generate_structured(_request())
    assert out.parsed.coverage_summary == "ok" and out.usage.retry_count == 1 and len(fake.calls) == 2


def _quota_error(quota_id: str, value: str, delay: str) -> errors.APIError:
    details = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": quota_id, "quotaValue": value, "quotaDimensions": {"model": GEMINI_MID_MODEL}}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]
    return errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": details}})


@pytest.mark.parametrize("exc", [
    _quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "20", "64200s"),
    _quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "0", "30s"),
])
def test_daily_or_zero_quota_fails_fast_without_retries(monkeypatch, exc):
    p, fake = _provider(monkeypatch, [exc, _resp("STOP", "{}")])
    p.settings = p.settings.model_copy(update={"llm_max_retries": 3})
    with pytest.raises(ProviderError, match="quota exhausted") as info:
        p.generate_structured(_request())
    assert not isinstance(info.value, ProviderRateLimitError) and len(fake.calls) == 1 and "billing" in info.value.message


def test_per_minute_quota_is_retried(monkeypatch):
    monkeypatch.setattr("backend.llm.base_provider.time.sleep", lambda _s: None)
    exc = _quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "10", "12s")
    p, fake = _provider(monkeypatch, [exc, _resp("STOP", '{"coverage_summary": "ok"}')])
    p.settings = p.settings.model_copy(update={"llm_max_retries": 1})
    assert p.generate_structured(_request()).usage.retry_count == 1 and len(fake.calls) == 2


def test_image_call_sends_inline_bytes(monkeypatch):
    p, fake = _provider(monkeypatch, [_resp("STOP", "a diagram")])
    text, _ = p.analyze_image("describe", b"\x89PNG", "image/png")
    part, prompt = fake.calls[0]["contents"]
    assert text == "a diagram" and prompt == "describe"
    assert part.inline_data.data == b"\x89PNG" and part.inline_data.mime_type == "image/png"


def test_real_client_is_built_with_timeout_and_no_sdk_retries():
    client = GeminiProvider(Settings(gemini_api_key=_KEY, llm_timeout_seconds=30))._client()
    opts = client._api_client._http_options
    assert opts.timeout == 30000 and opts.retry_options.attempts == 1
