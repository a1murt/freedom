"""OpenAI-compatible provider for local models (vLLM on the GPU cluster, Ollama on a laptop).
Uses response_format json_schema where supported; falls back to prompting for JSON and parsing the first object."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel

from ..contract import ProviderResult, Usage
from .base import ProviderError


def _flatten_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        out.append({"role": m["role"], "content": content})
    return out


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ProviderError("Local model returned no JSON object")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:  # a ProviderError, so the runner reports it per criterion instead of crashing
        raise ProviderError(f"Local model returned invalid JSON: {e}") from e


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str = "none", name: str = "local"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.name = name

    async def complete(self, system: str, messages: list[dict[str, Any]], schema: type[BaseModel], *, effort: str = "high",
                       max_tokens: int = 4096) -> ProviderResult:
        t0 = time.perf_counter()
        json_schema = schema.model_json_schema()
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _flatten_messages(system, messages) + [{"role": "user", "content": "Return only the JSON object matching the schema: " + json.dumps(json_schema, ensure_ascii=False)[:6000]}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": json_schema}},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=300.0) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                if r.status_code == 400 and "response_format" in r.text:
                    body.pop("response_format", None)
                    r = await c.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:  # "connection" in the message: the runner's _call_once retries it as transient
            raise ProviderError(f"Local provider connection error: {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"Local provider HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"Local provider returned an unexpected response: {r.text[:300]}") from e
        parsed = _extract_json(text)
        u = data.get("usage", {})
        usage = Usage(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0))
        latency = int((time.perf_counter() - t0) * 1000)
        return ProviderResult(parsed=parsed, usage=usage, raw={"model": data.get("model"), "text": text[:20000], "usage": u},
                              model=data.get("model") or self.model, provider=self.name, latency_ms=latency,
                              stop_reason=data["choices"][0].get("finish_reason"))

    def cost(self, usage: Usage) -> float:
        return 0.0
