"""Provider selection."""
from __future__ import annotations

from ..core.config import SUPPORTED_PROVIDERS, Settings
from ..core.exceptions import ProviderConfigError
from .anthropic_provider import AnthropicProvider
from .base_provider import LIMITER, LLMProvider
from .bedrock_provider import BedrockProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "mock": MockProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "bedrock": BedrockProvider,
}


def create_provider(name: str, settings: Settings) -> LLMProvider:
    """Instantiate provider ``name`` (``mock`` for the offline demonstration)."""
    key = (name or "").lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise ProviderConfigError(f"Unknown LLM provider '{name}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}")
    LIMITER.resize(settings.max_concurrent_agents)
    return cls(settings)


def provider_catalog(settings: Settings) -> list[dict[str, object]]:
    """Describe every provider with its configuration status (no secrets, no network)."""
    out: list[dict[str, object]] = []
    for name in SUPPORTED_PROVIDERS:
        provider = _PROVIDERS[name](settings)
        validation = provider.validate_configuration()
        caps = provider.capabilities()
        out.append({
            "provider": name,
            "model_id": provider.model_id,
            "configured": validation.ok,
            "message": validation.message,
            "vision": caps.vision,
            "simulated": caps.simulated,
            "active": settings.llm_provider == name,
        })
    return out
