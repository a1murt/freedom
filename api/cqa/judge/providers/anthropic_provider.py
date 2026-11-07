"""Claude provider: structured outputs via messages.parse, adaptive thinking, effort control."""
from __future__ import annotations

import time
from typing import Any

import anthropic
from pydantic import BaseModel

from ...config import settings
from ..contract import ProviderResult, Usage
from .base import ProviderError, ProviderRefusal, cost_usd

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=180.0)
    return _client


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str):
        self.model = model

    async def complete(self, system: str, messages: list[dict[str, Any]], schema: type[BaseModel], *, effort: str = "high",
                       max_tokens: int = 4096) -> ProviderResult:
        t0 = time.perf_counter()
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        try:
            resp = await client().messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
                output_format=schema,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
            )
        except anthropic.APIStatusError as e:  # includes rate limit after retries
            raise ProviderError(f"Anthropic API error {e.status_code}: {getattr(e, 'message', str(e))}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"Anthropic connection error: {e}") from e
        latency = int((time.perf_counter() - t0) * 1000)
        if resp.stop_reason == "refusal":
            raise ProviderRefusal("Model declined the request (stop_reason=refusal)")
        parsed = resp.parsed_output
        if parsed is None:
            raise ProviderError(f"No structured output returned (stop_reason={resp.stop_reason})")
        u = resp.usage
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        raw: dict[str, Any] = {
            "id": resp.id,
            "model": resp.model,
            "stop_reason": resp.stop_reason,
            "usage": usage.model_dump(),
            "text": "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")[:20000],
        }
        return ProviderResult(parsed=parsed.model_dump() if isinstance(parsed, BaseModel) else dict(parsed), usage=usage, raw=raw,
                              model=resp.model or self.model, provider=self.name, latency_ms=latency, stop_reason=resp.stop_reason)

    def cost(self, usage: Usage) -> float:
        return cost_usd(self.model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens)
