"""AWS Bedrock adapter (Converse API; structured output via forced tool use).

Authenticates with a Bedrock API key (``AWS_BEARER_TOKEN_BEDROCK``) when one is configured,
otherwise with the standard AWS credential chain / ``AWS_PROFILE`` via boto3.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from ..core.exceptions import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from .base_provider import LLMProvider, ProviderCapabilities, ProviderValidation, RawCompletion

_IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}
_TOOL_INSTRUCTION = "\n\nRespond only by calling the submit_result tool."

logger = logging.getLogger(__name__)

# Newer Claude models reject forced tool choice and/or sampling parameters. Learned per model id at runtime.
_UNSUPPORTED_PARAMS: dict[str, set[str]] = {}


def _strip_unsupported(kwargs: dict[str, Any], unsupported: set[str]) -> dict[str, Any]:
    call = dict(kwargs)
    if "temperature" in unsupported and "inferenceConfig" in call:
        call["inferenceConfig"] = {k: v for k, v in call["inferenceConfig"].items() if k != "temperature"}
    if "toolChoice" in unsupported and "toolConfig" in call:
        call["toolConfig"] = {**call["toolConfig"], "toolChoice": {"auto": {}}}
        call["system"] = [{"text": call["system"][0]["text"] + _TOOL_INSTRUCTION}, *call["system"][1:]]
    return call


_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")
_STATUS_CODES = {400: "ValidationException", 401: "UnrecognizedClientException", 403: "AccessDeniedException",
                 404: "ResourceNotFoundException", 408: "ModelTimeoutException", 429: "ThrottlingException",
                 503: "ServiceUnavailableException"}


class ApiKeyConverseClient:
    """Minimal ``bedrock-runtime`` Converse client authenticated with a Bedrock API key (bearer token).

    Calls the Converse REST endpoint directly so it works with any boto3 version, and raises
    ``botocore`` ``ClientError`` so callers handle errors exactly as for the boto3 client.
    """

    def __init__(self, api_key: str, region: str, timeout_seconds: float) -> None:
        if not _REGION_RE.match(region or ""):
            raise ProviderConfigError(f"Invalid AWS_REGION for Bedrock: {region!r}")
        self._api_key = api_key
        self._base_url = f"https://bedrock-runtime.{region}.amazonaws.com"
        self._timeout = httpx.Timeout(timeout_seconds, connect=15)

    def converse(self, *, modelId: str, **kwargs: Any) -> dict[str, Any]:  # noqa: N803 - mirrors the boto3 signature
        from botocore.exceptions import ClientError

        url = f"{self._base_url}/model/{quote(modelId, safe='')}/converse"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "Accept": "application/json"}
        try:
            resp = httpx.post(url, json=_rest_body(kwargs), headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Bedrock request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderTimeoutError("Could not reach the Bedrock endpoint.") from exc
        if resp.status_code < 400:
            return resp.json()
        code = resp.headers.get("x-amzn-ErrorType", "").split(":")[0] or _STATUS_CODES.get(resp.status_code, f"HTTP{resp.status_code}")
        try:
            body = resp.json()
            message = str(body.get("message") or body.get("Message") or "")
        except ValueError:
            message = ""
        raise ClientError({"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": resp.status_code}}, "Converse")


def _rest_body(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The REST API takes the boto3 request shape, except raw image bytes are base64 strings."""
    messages = []
    for message in kwargs.get("messages", []):
        content = []
        for block in message.get("content", []):
            source = block.get("image", {}).get("source", {})
            if isinstance(source.get("bytes"), bytes):
                block = {"image": {**block["image"], "source": {"bytes": base64.b64encode(source["bytes"]).decode("ascii")}}}
            content.append(block)
        messages.append({**message, "content": content})
    return {**kwargs, "messages": messages}


def _rejected_param(exc: Any, call: dict[str, Any], unsupported: set[str]) -> str | None:
    """Return the request parameter a ValidationException complains about, if we can drop it and retry."""
    err = exc.response.get("Error", {})
    if err.get("Code") != "ValidationException":
        return None
    message = err.get("Message", "").lower()
    if "tool_choice" in message and "toolChoice" not in unsupported and "tool" in call.get("toolConfig", {}).get("toolChoice", {}):
        return "toolChoice"
    if "temperature" in message and "temperature" not in unsupported and "temperature" in call.get("inferenceConfig", {}):
        return "temperature"
    return None


class BedrockProvider(LLMProvider):
    name = "bedrock"

    @property
    def model_id(self) -> str:
        return self.settings.bedrock_model_id

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name, model_id=self.model_id, structured_output=True, vision=True,
            notes=["Vision support assumes the configured Bedrock model accepts image input via Converse."],
        )

    def _api_key(self) -> str:
        key = self.settings.aws_bearer_token_bedrock
        return key.get_secret_value().strip() if key else ""

    def _client(self) -> Any:
        if self._api_key():
            return ApiKeyConverseClient(self._api_key(), self.settings.aws_region, self.settings.llm_timeout_seconds)
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ProviderConfigError("The 'boto3' package is not installed.") from exc
        session_kwargs: dict[str, Any] = {"region_name": self.settings.aws_region}
        if self.settings.aws_profile:
            session_kwargs["profile_name"] = self.settings.aws_profile
        try:
            session = boto3.Session(**session_kwargs)
        except Exception as exc:  # botocore raises ProfileNotFound etc.
            raise ProviderConfigError(f"Could not create AWS session: {exc.__class__.__name__}") from exc
        if session.get_credentials() is None:
            raise ProviderConfigError("No AWS credentials found (set AWS_BEARER_TOKEN_BEDROCK, AWS_PROFILE or the standard credential chain).")
        cfg = Config(read_timeout=self.settings.llm_timeout_seconds, connect_timeout=15, retries={"max_attempts": 1})
        return session.client("bedrock-runtime", config=cfg)

    def validate_configuration(self) -> ProviderValidation:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message="The 'boto3' package is not installed.")
        if not self.settings.bedrock_model_id:
            return ProviderValidation(ok=False, provider=self.name, model_id="", message="BEDROCK_MODEL_ID is not configured.")
        try:
            self._client()
        except ProviderError as exc:
            return ProviderValidation(ok=False, provider=self.name, model_id=self.model_id, message=exc.message)
        source = "Bedrock API key configured" if self._api_key() else "AWS credentials resolved"
        return ProviderValidation(ok=True, provider=self.name, model_id=self.model_id,
                                  message=f"{source} for region {self.settings.aws_region}.", capabilities=self.capabilities())

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
        from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

        if isinstance(exc, (ReadTimeoutError, ConnectTimeoutError)):
            return ProviderTimeoutError("Bedrock request timed out.")
        if isinstance(exc, EndpointConnectionError):
            return ProviderTimeoutError("Could not reach the Bedrock endpoint.")
        if isinstance(exc, ClientError):
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException", "ModelNotReadyException"):
                return ProviderRateLimitError(f"Bedrock throttled the request ({code}).")
            if code in ("AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException", "InvalidSignatureException"):
                return ProviderAuthError(f"Bedrock denied access ({code}). Check credentials and model access in this region.")
            if code == "ModelTimeoutException":
                return ProviderTimeoutError("Bedrock model timed out.")
            return ProviderError(f"Bedrock error: {code or exc.__class__.__name__}.")
        return ProviderError(f"Bedrock error: {exc.__class__.__name__}.")

    def _converse(self, **kwargs: Any) -> Any:
        from botocore.exceptions import BotoCoreError, ClientError

        client = self._client()
        unsupported = _UNSUPPORTED_PARAMS.setdefault(self.model_id, set())
        while True:
            call = _strip_unsupported(kwargs, unsupported)
            try:
                return client.converse(modelId=self.model_id, **call)
            except ClientError as exc:
                rejected = _rejected_param(exc, call, unsupported)
                if rejected is None:
                    raise self._translate(exc) from exc
                logger.info("Bedrock model %s rejected %s; retrying without it.", self.model_id, rejected)
                unsupported.add(rejected)
            except BotoCoreError as exc:
                raise self._translate(exc) from exc

    def _complete(self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None, schema_name: str) -> RawCompletion:
        kwargs: dict[str, Any] = {
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.1},
        }
        if schema is not None:
            kwargs["toolConfig"] = {
                "tools": [{"toolSpec": {"name": "submit_result", "description": "Submit the structured result.", "inputSchema": {"json": schema}}}],
                "toolChoice": {"tool": {"name": "submit_result"}},
            }
        resp = self._converse(**kwargs)
        text = ""
        for block in resp.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block:
                text = json.dumps(block["toolUse"].get("input", {}))
                break
            if "text" in block:
                text += block["text"]
        usage = resp.get("usage", {})
        return RawCompletion(text=text, input_tokens=usage.get("inputTokens"), output_tokens=usage.get("outputTokens"))

    def _complete_image(self, prompt: str, image: bytes, media_type: str, *, max_tokens: int) -> RawCompletion:
        fmt = _IMAGE_FORMATS.get(media_type)
        if fmt is None:
            raise ProviderError(f"Unsupported image type for Bedrock: {media_type}")
        resp = self._converse(
            messages=[{"role": "user", "content": [{"image": {"format": fmt, "source": {"bytes": image}}}, {"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.1},
        )
        text = "".join(b.get("text", "") for b in resp.get("output", {}).get("message", {}).get("content", []))
        usage = resp.get("usage", {})
        return RawCompletion(text=text, input_tokens=usage.get("inputTokens"), output_tokens=usage.get("outputTokens"))
