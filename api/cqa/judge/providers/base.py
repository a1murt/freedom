from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from ...config import settings
from ..contract import ProviderResult


class ProviderError(Exception):
    pass


class ProviderRefusal(ProviderError):
    pass


class JudgeProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, messages: list[dict[str, Any]], schema: type[BaseModel], *, effort: str = "high",
                       max_tokens: int = 4096) -> ProviderResult: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float:
    rin, rout, rcache = settings.rate_for(model)
    return (input_tokens * rin + output_tokens * rout + cache_read * rcache) / 1_000_000


def get_provider(name: str, model: str | None = None) -> JudgeProvider:
    """name: 'anthropic' | 'anthropic:<model>' | 'local' | 'ollama' | 'openai_compatible'."""
    if ":" in name and not name.startswith("http"):
        name, model = name.split(":", 1)
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model or settings.default_model)
    if name in ("local", "ollama", "openai_compatible", "vllm"):
        from .openai_compatible import OpenAICompatibleProvider

        base = settings.local_endpoint or "http://localhost:11434/v1"
        return OpenAICompatibleProvider(base_url=base, model=model or settings.local_model, api_key=settings.local_api_key, name="local")
    raise ProviderError(f"Unknown provider '{name}'")
