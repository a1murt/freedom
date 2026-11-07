from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from ...config import CACHE_WRITE_MULTIPLIER, settings
from ..contract import ProviderResult, Usage


class ProviderError(Exception):
    """A failed provider call. When the provider answered (and billed) before failing, e.g. a refusal or output cut off
    at max_tokens, ``usage``/``cost_usd``/``raw`` describe that billed response so callers can book its cost."""

    def __init__(self, message: str = "", *, usage: Usage | None = None, cost_usd: float = 0.0, raw: dict[str, Any] | None = None,
                 provider: str | None = None, model: str | None = None, latency_ms: int = 0):
        super().__init__(message)
        self.usage = usage
        self.cost_usd = float(cost_usd or 0.0)
        self.raw = raw
        self.provider = provider
        self.model = model
        self.latency_ms = latency_ms

    @property
    def billed(self) -> bool:
        return self.usage is not None


class ProviderRefusal(ProviderError):
    pass


class JudgeProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, messages: list[dict[str, Any]], schema: type[BaseModel], *, effort: str = "high",
                       max_tokens: int = 4096) -> ProviderResult: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """USD of one call. ``input_tokens`` is the uncached remainder; cache writes (5-minute TTL) cost 1.25x the input rate."""
    rin, rout, rcache = settings.rate_for(model)
    return ((input_tokens or 0) * rin + (output_tokens or 0) * rout + (cache_read or 0) * rcache
            + (cache_write or 0) * rin * CACHE_WRITE_MULTIPLIER) / 1_000_000


def get_provider(name: str, model: str | None = None, *, endpoint: str | None = None, api_key: str | None = None) -> JudgeProvider:
    """name: 'anthropic' | 'anthropic:<model>' | 'local' | 'ollama' | 'openai_compatible' | 'fake'.

    Local providers use ``endpoint`` (default: the environment's LOCAL_ENDPOINT) and ``api_key``; when the caller does
    not pass a key, LOCAL_API_KEY is sent only if the endpoint is a trusted one (cqa.judge.local_endpoint)."""
    if ":" in name and not name.startswith("http"):
        name, model = name.split(":", 1)
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model or settings.default_model)
    if name in ("local", "ollama", "openai_compatible", "vllm"):
        from ..local_endpoint import api_key_for
        from .openai_compatible import OpenAICompatibleProvider

        base = endpoint or settings.local_endpoint or "http://localhost:11434/v1"
        key = api_key if api_key is not None else api_key_for(base)
        return OpenAICompatibleProvider(base_url=base, model=model or settings.local_model, api_key=key, name="local")
    if name == "fake":  # offline deterministic judge for tests, CI and keyless demos (cqa/judge/providers/fake.py)
        from .fake import FakeProvider

        return FakeProvider(model)
    raise ProviderError(f"Unknown provider '{name}'")
