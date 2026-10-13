"""Anthropic adapter request shape and response handling (offline; the SDK client is faked)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from backend.core.config import Settings
from backend.core.exceptions import ProviderError
from backend.llm.anthropic_provider import AnthropicProvider
from backend.llm.base_provider import StructuredRequest
from backend.models.domain import AgentReviewOutput


class _FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(stop_reason: str, *blocks: Any) -> Any:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks), usage=SimpleNamespace(input_tokens=10, output_tokens=20))


def _tool(data: dict[str, Any]) -> Any:
    return SimpleNamespace(type="tool_use", name="submit_result", input=data)


def _provider(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> tuple[AnthropicProvider, _FakeMessages]:
    p = AnthropicProvider(Settings(anthropic_api_key="sk-ant-test-123456789012345", llm_max_retries=0))
    fake = _FakeMessages(responses)
    monkeypatch.setattr(p, "_client", lambda: SimpleNamespace(messages=fake))
    return p, fake


def _request() -> StructuredRequest:
    return StructuredRequest(task="agent_review", system_prompt="sys", user_prompt="review", response_model=AgentReviewOutput)


def test_structured_call_uses_auto_tool_choice(monkeypatch):
    # current Claude models reject forced tool_choice ("tool"/"any") with HTTP 400
    p, fake = _provider(monkeypatch, [_resp("tool_use", _tool({"coverage_summary": "ok"}))])
    out = p.generate_structured(_request())
    assert out.parsed.coverage_summary == "ok"
    call = fake.calls[0]
    assert call["tool_choice"] == {"type": "auto"}
    assert call["tools"][0]["name"] == "submit_result" and "submit_result" in call["system"]


def test_plain_text_json_reply_is_accepted(monkeypatch):
    p, _ = _provider(monkeypatch, [_resp("end_turn", SimpleNamespace(type="text", text='{"coverage_summary": "text"}'))])
    assert p.generate_structured(_request()).parsed.coverage_summary == "text"


def test_truncated_structured_reply_fails_instead_of_empty_result(monkeypatch):
    # an empty tool input would validate as "no findings"; it must surface as an error
    p, _ = _provider(monkeypatch, [_resp("max_tokens", _tool({}))])
    with pytest.raises(ProviderError, match="truncated"):
        p.generate_structured(_request())


def test_api_error_message_is_surfaced(monkeypatch):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "tool_choice not supported"}}
    err = anthropic.BadRequestError("bad", response=httpx.Response(400, request=req), body=body)
    p, _ = _provider(monkeypatch, [err])
    with pytest.raises(ProviderError, match="tool_choice not supported") as exc:
        p.generate_structured(_request())
    assert "sk-ant" not in exc.value.message
