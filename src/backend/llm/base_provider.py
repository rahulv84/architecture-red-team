"""Provider-neutral LLM interface.

Agents and services depend only on :class:`LLMProvider`.  Provider SDK details, credential
handling, retries, concurrency limiting and structured-output validation live here and in
the concrete adapters - never in the agents.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..core.config import Settings
from ..core.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from ..models.domain import UsageRecord

log = logging.getLogger(__name__)


class ProviderCapabilities(BaseModel):
    provider: str
    model_id: str
    structured_output: bool = True
    vision: bool = False
    simulated: bool = False
    notes: list[str] = []


class ProviderValidation(BaseModel):
    ok: bool
    provider: str
    model_id: str
    message: str
    capabilities: ProviderCapabilities | None = None
    network_checked: bool = False


@dataclass
class StructuredRequest:
    """One structured-generation call.

    ``context`` carries machine-readable simulation hints that are *only* consumed by the
    deterministic mock provider; live providers ignore it and see the prompts alone.
    """

    task: str
    system_prompt: str
    user_prompt: str
    response_model: type[BaseModel]
    agent_id: str | None = None
    max_output_tokens: int | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredResponse:
    parsed: BaseModel
    raw_text: str
    usage: UsageRecord


@dataclass
class RawCompletion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ConcurrencyLimiter:
    """Process-wide bound on simultaneous provider calls."""

    def __init__(self, limit: int = 6) -> None:
        self._lock = threading.Lock()
        self._limit = limit
        self._sem = threading.BoundedSemaphore(limit)

    def resize(self, limit: int) -> None:
        with self._lock:
            if limit != self._limit:
                self._limit = limit
                self._sem = threading.BoundedSemaphore(limit)

    def __enter__(self) -> None:
        self._sem.acquire()

    def __exit__(self, *exc: object) -> None:
        self._sem.release()


LIMITER = ConcurrencyLimiter()


def extract_json(text: str) -> Any:
    """Parse the first JSON object in ``text`` (tolerates markdown fences and prose)."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except ValueError:
                        break
        start = cleaned.find("{", start + 1)
    raise ValueError("No JSON object found in model output")


class LLMProvider(ABC):
    """Abstract provider.  Subclasses implement ``_complete`` (and ``_complete_image``)."""

    name = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---- identity / capabilities ---------------------------------------
    @property
    @abstractmethod
    def model_id(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def validate_configuration(self) -> ProviderValidation:
        """Check local configuration only (keys present, SDK importable); no network."""

    def check_connectivity(self) -> ProviderValidation:
        """Perform a tiny live call.  Default: configuration validation only."""
        return self.validate_configuration()

    # ---- primitive completion implemented per provider -------------------
    @abstractmethod
    def _complete(self, system: str, user: str, *, max_tokens: int, schema: dict[str, Any] | None, schema_name: str) -> RawCompletion: ...

    def _complete_image(self, prompt: str, image: bytes, media_type: str, *, max_tokens: int) -> RawCompletion:
        raise ProviderError(f"Provider '{self.name}' does not support image analysis.")

    # ---- public operations ------------------------------------------------
    def analyze_text(self, system: str, prompt: str, *, max_tokens: int | None = None, agent_id: str | None = None) -> tuple[str, UsageRecord]:
        started = time.monotonic()
        comp, retries = self._call_with_retries(
            lambda: self._complete(system, prompt, max_tokens=max_tokens or self.settings.max_output_tokens, schema=None, schema_name="text")
        )
        return comp.text, self._usage(comp, started, retries, agent_id, "analyze_text")

    def analyze_image(self, prompt: str, image: bytes, media_type: str, *, agent_id: str | None = None) -> tuple[str, UsageRecord]:
        if not self.capabilities().vision:
            raise ProviderError(f"Provider '{self.name}' / model '{self.model_id}' does not support image input.")
        started = time.monotonic()
        comp, retries = self._call_with_retries(
            lambda: self._complete_image(prompt, image, media_type, max_tokens=self.settings.max_output_tokens)
        )
        return comp.text, self._usage(comp, started, retries, agent_id, "analyze_image")

    def generate_structured(self, request: StructuredRequest) -> StructuredResponse:
        schema = request.response_model.model_json_schema()
        system = (
            request.system_prompt
            + "\n\nRespond with a single JSON object that conforms to this JSON Schema. "
            "Output JSON only - no prose, no markdown fences.\n"
            + json.dumps(schema)
        )
        started = time.monotonic()
        max_tokens = request.max_output_tokens or self.settings.max_output_tokens
        total_retries = 0
        user = request.user_prompt
        last_error: str | None = None
        total_in = total_out = 0
        for attempt in range(2):  # one validation-repair attempt
            comp, retries = self._call_with_retries(
                lambda u=user: self._complete(system, u, max_tokens=max_tokens, schema=schema, schema_name=request.response_model.__name__)
            )
            total_retries += retries
            total_in += comp.input_tokens or 0
            total_out += comp.output_tokens or 0
            try:
                data = extract_json(comp.text)
                parsed = request.response_model.model_validate(data)
                usage = self._usage(comp, started, total_retries, request.agent_id, request.task)
                usage.input_tokens, usage.output_tokens = total_in or None, total_out or None
                return StructuredResponse(parsed=parsed, raw_text=comp.text, usage=usage)
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:1500]
                log.warning("Structured output invalid (task=%s attempt=%d): %s", request.task, attempt + 1, last_error[:300])
                user = (
                    request.user_prompt
                    + "\n\nYour previous reply was rejected by schema validation:\n"
                    + last_error
                    + "\nReturn corrected JSON only."
                )
        raise StructuredOutputError(
            f"Model returned output that does not satisfy the '{request.response_model.__name__}' schema.",
            details={"validation_error": last_error},
        )

    # ---- helpers ---------------------------------------------------------
    def _usage(self, comp: RawCompletion, started: float, retries: int, agent_id: str | None, task: str) -> UsageRecord:
        return UsageRecord(
            provider=self.name, model_id=self.model_id, agent_id=agent_id, task=task,
            input_tokens=comp.input_tokens, output_tokens=comp.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000), retry_count=retries, status="OK",
        )

    def _call_with_retries(self, fn: Any) -> tuple[RawCompletion, int]:
        max_retries = self.settings.llm_max_retries
        retries = 0
        while True:
            try:
                with LIMITER:
                    return fn(), retries
            except (ProviderRateLimitError, ProviderTimeoutError) as exc:
                if retries >= max_retries:
                    raise
                delay = min(30.0, (2**retries) * 1.0) + random.uniform(0, 0.5)
                log.warning("Provider %s transient error (%s); retry %d/%d in %.1fs", self.name, exc.code, retries + 1, max_retries, delay)
                time.sleep(delay)
                retries += 1


