"""Offline tests for the Bedrock adapter's handling of parameters newer models reject."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError

from backend.core.config import Settings
from backend.core.exceptions import ProviderAuthError, ProviderError, ProviderRateLimitError
from backend.llm import bedrock_provider
from backend.llm.bedrock_provider import BedrockProvider

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


class FakeClient:
    def __init__(self, rejects: dict[str, str]) -> None:
        self.rejects = rejects
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if "temperature" in self.rejects and "temperature" in kwargs["inferenceConfig"]:
            raise _validation(self.rejects["temperature"])
        if "toolChoice" in self.rejects and "tool" in kwargs.get("toolConfig", {}).get("toolChoice", {}):
            raise _validation(self.rejects["toolChoice"])
        return {"output": {"message": {"content": [{"toolUse": {"input": {"ok": True}}}]}}, "usage": {}}


def _validation(message: str) -> ClientError:
    return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "Converse")


def _provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, model: str, client: FakeClient) -> BedrockProvider:
    monkeypatch.setattr(bedrock_provider, "_UNSUPPORTED_PARAMS", {})
    provider = BedrockProvider(Settings(local_data_dir=str(tmp_path), bedrock_model_id=model))
    monkeypatch.setattr(provider, "_client", lambda: client)
    return provider


def test_retries_without_rejected_params_and_remembers_them(monkeypatch, tmp_path):
    client = FakeClient({"temperature": "temperature is not supported", "toolChoice": 'tool_choice: type "tool" and "any" are not supported'})
    provider = _provider(monkeypatch, tmp_path, "eu.anthropic.claude-sonnet-5-5", client)

    assert provider._complete("sys", "user", max_tokens=50, schema=SCHEMA, schema_name="t").text == '{"ok": true}'
    final = client.calls[-1]
    assert "temperature" not in final["inferenceConfig"]
    assert final["toolConfig"]["toolChoice"] == {"auto": {}}
    assert "submit_result" in final["system"][0]["text"]

    client.calls.clear()
    provider._complete("sys", "user", max_tokens=50, schema=SCHEMA, schema_name="t")
    assert len(client.calls) == 1  # learned: no failed attempts on the second call


def test_supported_model_keeps_forced_tool_and_temperature(monkeypatch, tmp_path):
    client = FakeClient({})
    provider = _provider(monkeypatch, tmp_path, "eu.anthropic.claude-sonnet-4-6", client)
    provider._complete("sys", "user", max_tokens=50, schema=SCHEMA, schema_name="t")
    assert len(client.calls) == 1
    assert client.calls[0]["inferenceConfig"]["temperature"] == 0.1
    assert client.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "submit_result"}}


def test_other_validation_errors_are_not_retried(monkeypatch, tmp_path):
    client = FakeClient({"temperature": "Invocation of model ID x with on-demand throughput isn't supported"})
    provider = _provider(monkeypatch, tmp_path, "anthropic.claude-sonnet-5-5", client)
    with pytest.raises(ProviderError):
        provider._complete("sys", "user", max_tokens=50, schema=SCHEMA, schema_name="t")
    assert len(client.calls) == 1


# --------------------------------------------------------------------------- Bedrock API key (bearer token)
def _api_key_provider(tmp_path: Any, **overrides: Any) -> BedrockProvider:
    values: dict[str, Any] = {"bedrock_model_id": "eu.anthropic.claude-sonnet-4-6", "aws_region": "eu-central-1",
                              "aws_bearer_token_bedrock": "test-bedrock-key", **overrides}
    return BedrockProvider(Settings(local_data_dir=str(tmp_path), **values))


def test_api_key_calls_converse_rest_endpoint_with_bearer_token(monkeypatch, tmp_path):
    seen: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict, headers: dict, timeout: Any) -> httpx.Response:
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={"output": {"message": {"content": [{"toolUse": {"input": {"ok": True}}}]}},
                                         "usage": {"inputTokens": 3, "outputTokens": 2}})

    monkeypatch.setattr(bedrock_provider.httpx, "post", fake_post)
    provider = _api_key_provider(tmp_path)
    assert provider.validate_configuration().ok
    result = provider._complete("sys", "user", max_tokens=50, schema=SCHEMA, schema_name="t")

    assert result.text == '{"ok": true}' and result.input_tokens == 3
    assert seen["url"] == "https://bedrock-runtime.eu-central-1.amazonaws.com/model/eu.anthropic.claude-sonnet-4-6/converse"
    assert seen["headers"]["Authorization"] == "Bearer test-bedrock-key"
    assert seen["body"]["toolConfig"]["toolChoice"] == {"tool": {"name": "submit_result"}}


def test_api_key_image_bytes_are_base64_encoded(monkeypatch, tmp_path):
    seen: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict, headers: dict, timeout: Any) -> httpx.Response:
        seen["body"] = json
        return httpx.Response(200, json={"output": {"message": {"content": [{"text": "a diagram"}]}}, "usage": {}})

    monkeypatch.setattr(bedrock_provider.httpx, "post", fake_post)
    assert _api_key_provider(tmp_path)._complete_image("describe", b"\x89PNG", "image/png", max_tokens=50).text == "a diagram"
    assert seen["body"]["messages"][0]["content"][0]["image"] == {"format": "png", "source": {"bytes": "iVBORw=="}}


@pytest.mark.parametrize(("status", "error_type", "expected"), [
    (403, "AccessDeniedException:http://internal.amazon.com/coral/", ProviderAuthError),
    (429, "", ProviderRateLimitError),
])
def test_api_key_http_errors_map_to_provider_errors(monkeypatch, tmp_path, status, error_type, expected):
    headers = {"x-amzn-ErrorType": error_type} if error_type else {}
    monkeypatch.setattr(bedrock_provider.httpx, "post", lambda *a, **k: httpx.Response(status, headers=headers, json={"message": "no"}))
    with pytest.raises(expected):
        _api_key_provider(tmp_path)._complete("sys", "user", max_tokens=50, schema=None, schema_name="t")


def test_api_key_validation_errors_still_trigger_parameter_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(bedrock_provider, "_UNSUPPORTED_PARAMS", {})
    bodies: list[dict] = []

    def fake_post(url: str, *, json: dict, headers: dict, timeout: Any) -> httpx.Response:
        bodies.append(json)
        if "temperature" in json["inferenceConfig"]:
            return httpx.Response(400, headers={"x-amzn-ErrorType": "ValidationException"}, json={"message": "temperature is not supported"})
        return httpx.Response(200, json={"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {}})

    monkeypatch.setattr(bedrock_provider.httpx, "post", fake_post)
    assert _api_key_provider(tmp_path)._complete("sys", "user", max_tokens=50, schema=None, schema_name="t").text == "ok"
    assert len(bodies) == 2 and "temperature" not in bodies[1]["inferenceConfig"]


def test_api_key_rejects_malformed_region(tmp_path):
    validation = _api_key_provider(tmp_path, aws_region="evil.example.com/x").validate_configuration()
    assert not validation.ok and "AWS_REGION" in validation.message


def test_bedrock_api_key_is_never_exposed(tmp_path):
    settings = Settings(local_data_dir=str(tmp_path), aws_bearer_token_bedrock="test-bedrock-key")
    public = settings.public_view()
    assert public["bedrock_api_key_configured"] is True
    assert "aws_bearer_token_bedrock" not in public and "test-bedrock-key" not in str(public)
