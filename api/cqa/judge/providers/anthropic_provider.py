"""Claude provider: structured outputs (output_config.format with a JSON schema), adaptive thinking, effort control.

- One AsyncAnthropic client per running event loop: every job worker runs its own loop (asyncio.run in a thread) and a
  pooled httpx connection must never be reused from another loop (it fails after the request was sent, and the SDK
  then re-sends a request the API already processed and billed). Loops that end (jobs, compilations) close their
  client with ``aclose_loop_client()``.
- The request is the one ``messages.parse(output_format=...)`` sends (the same transformed JSON schema), but through
  ``messages.create`` and our own validation, so a billed response that cannot be used (refusal, output cut off at
  max_tokens, invalid JSON) still reports its usage: the raised ProviderError carries ``usage`` and ``cost_usd``.
- Every billed response, successful or not, is booked in the spend ledger (cqa.jobs.ledger).
- Cost includes prompt-cache writes (1.25x the input rate): the system prompt and the transcript block are cached and
  parallel criterion calls each write them.
"""
from __future__ import annotations

import asyncio
import threading
import time
import weakref
from typing import Any

import anthropic
from pydantic import BaseModel, TypeAdapter, ValidationError

from ...config import settings
from ..contract import ProviderResult, Usage
from .base import ProviderError, ProviderRefusal, cost_usd

# Test seam / legacy override: when set, this client is used for every call (tests install a fake SDK client here).
_client: Any = None
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, anthropic.AsyncAnthropic]" = weakref.WeakKeyDictionary()
_clients_lock = threading.Lock()
_schemas: dict[type, dict[str, Any]] = {}


def client() -> Any:
    """The AsyncAnthropic client of the running event loop (created on first use)."""
    if _client is not None:
        return _client
    if not settings.anthropic_api_key:
        raise ProviderError("ANTHROPIC_API_KEY is not set")
    loop = asyncio.get_running_loop()
    with _clients_lock:
        c = _clients.get(loop)
        if c is None:
            c = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=180.0)
            _clients[loop] = c
        return c


async def aclose_loop_client() -> None:
    """Close the client of the running loop (call before a short-lived loop ends: job workers, compilations)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _clients_lock:
        c = _clients.pop(loop, None)
    if c is not None:
        try:
            await c.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass


def json_schema_format(schema: type[BaseModel]) -> dict[str, Any]:
    """output_config.format for a pydantic model: exactly what messages.parse(output_format=schema) sends."""
    fmt = _schemas.get(schema)
    if fmt is None:
        fmt = {"type": "json_schema", "schema": anthropic.transform_schema(TypeAdapter(schema).json_schema())}
        _schemas[schema] = fmt
    return fmt


def _usage(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _parse(schema: type[BaseModel], blocks: list[str]) -> BaseModel | None:
    for text in blocks:
        try:
            return schema.model_validate_json(text)
        except (ValidationError, ValueError):
            continue
    return None


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str):
        self.model = model

    def _book(self, usage: Usage, *, ok: bool) -> float:
        cost = self.cost(usage)
        from ...jobs import ledger

        ledger.book(cost, provider=self.name, model=self.model, usage=usage, ok=ok)
        return cost

    async def complete(self, system: str, messages: list[dict[str, Any]], schema: type[BaseModel], *, effort: str = "high",
                       max_tokens: int = 4096) -> ProviderResult:
        t0 = time.perf_counter()
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        try:
            resp = await client().messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": effort, "format": json_schema_format(schema)},
            )
        except anthropic.APIStatusError as e:  # includes rate limit after retries
            raise ProviderError(f"Anthropic API error {e.status_code}: {getattr(e, 'message', str(e))}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"Anthropic connection error: {e}") from e
        latency = int((time.perf_counter() - t0) * 1000)
        usage = _usage(resp)
        texts = [getattr(b, "text", "") or "" for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text"]
        raw: dict[str, Any] = {
            "id": getattr(resp, "id", None),
            "model": getattr(resp, "model", None),
            "stop_reason": getattr(resp, "stop_reason", None),
            "usage": usage.model_dump(),
            "text": "".join(texts)[:20000],
        }
        model = getattr(resp, "model", None) or self.model
        parsed = None if resp.stop_reason == "refusal" else _parse(schema, texts)
        cost = self._book(usage, ok=parsed is not None)
        if resp.stop_reason == "refusal":
            raise ProviderRefusal("Model declined the request (stop_reason=refusal)", usage=usage, cost_usd=cost, raw=raw,
                                  provider=self.name, model=model, latency_ms=latency)
        if parsed is None:
            raise ProviderError(f"No structured output returned (stop_reason={resp.stop_reason})", usage=usage, cost_usd=cost,
                                raw=raw, provider=self.name, model=model, latency_ms=latency)
        return ProviderResult(parsed=parsed.model_dump(), usage=usage, raw=raw, model=model, provider=self.name,
                              latency_ms=latency, stop_reason=resp.stop_reason)

    def cost(self, usage: Usage) -> float:
        return cost_usd(self.model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens)
