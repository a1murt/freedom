"""Request id, timing and a one-line access log for every HTTP request (WP platform).

Pure ASGI (scope/receive/send), never BaseHTTPMiddleware: response messages are forwarded the moment they are sent, so
SSE (`/api/jobs/{id}/events`) and ranged audio (`/api/audio/{id}`) keep streaming.

Per request:
- `request_id` = a valid incoming `X-Request-ID` (or one an outer layer already put in `scope["state"]`), otherwise 12 hex
  chars. Stored in `scope["state"]["request_id"]`, so `request.state.request_id` works in handlers and inner middlewares
  (the security middleware reads it for the audit trail), and in a context variable (`get_request_id()`).
- every response carries `X-Request-ID`; cross-origin responses also list it in `Access-Control-Expose-Headers` (merged
  with what is already there) so the web app can show it in error messages.
- one INFO line on logger `cqa.access`: `GET /api/health 200 1.4ms role=demo rid=7f3a9c01b2de`.
- in-process counters for `GET /api/system/metrics` (`stats_snapshot()`).

`install(app)` places the middleware outermost (outside the error handler, CORS and the security middleware), so 403/400
answers of the security middleware, CORS preflights and 500 pages are logged and tagged too.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("cqa.access")

HEADER = "X-Request-ID"
_RID_HEADER = b"x-request-id"
_ROLE_HEADER = b"x-cqa-role"
_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+=@-]{0,63}$")
_ROLE_CHARS = re.compile(r"[^a-z0-9_.-]")

_request_id: ContextVar[str | None] = ContextVar("cqa_request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def get_request_id() -> str | None:
    """The id of the request being handled in the current context (None outside a request)."""
    return _request_id.get()


def clean_request_id(value: str | None) -> str | None:
    """Accept a client correlation id only if it is short and printable ASCII without spaces (safe for logs/headers)."""
    if not value:
        return None
    value = value.strip()
    return value if _VALID_ID.match(value) else None


def clean_role(value: str | None) -> str:
    role = _ROLE_CHARS.sub("", (value or "").strip().lower())[:32]
    return role or "demo"


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _printable(text: str) -> str:
    return "".join(ch if ch.isprintable() else "?" for ch in text)


def _path(scope: Scope) -> str:
    raw = scope.get("raw_path")
    if isinstance(raw, (bytes, bytearray)) and raw:
        return _printable(raw.decode("latin-1"))
    return _printable(str(scope.get("path") or "/"))


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class RequestStats:
    """Counters since process start. Streaming responses (SSE) are counted but kept out of the latency window."""

    def __init__(self, window: int = 1000) -> None:
        self._lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc)
        self.total = 0
        self.in_flight = 0
        self.streams = 0
        self.by_class: dict[str, int] = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0, "aborted": 0}
        self._latencies: deque[float] = deque(maxlen=window)

    def begin(self) -> None:
        with self._lock:
            self.in_flight += 1

    def end(self, status: int | None, duration_ms: float, streaming: bool) -> None:
        with self._lock:
            self.in_flight = max(self.in_flight - 1, 0)
            self.total += 1
            if status is None:
                key = "aborted"
            elif 200 <= status < 600 and status // 100 != 1:
                key = f"{status // 100}xx"
            else:
                key = "other"
            self.by_class[key] = self.by_class.get(key, 0) + 1
            if streaming:
                self.streams += 1
            else:
                self._latencies.append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            values = sorted(self._latencies)
            data = {"since": self.started_at.isoformat(), "requests": self.total, "in_flight": self.in_flight,
                    "streams": self.streams, "by_status_class": dict(self.by_class)}
        p50, p90 = _percentile(values, 0.5), _percentile(values, 0.9)
        data["latency_ms"] = {"window": len(values), "p50": round(p50, 1) if p50 is not None else None,
                              "p90": round(p90, 1) if p90 is not None else None,
                              "max": round(values[-1], 1) if values else None}
        return data


STATS = RequestStats()


def stats_snapshot() -> dict[str, Any]:
    return STATS.snapshot()


def _with_request_id(headers: Any, rid: str) -> tuple[list[tuple[bytes, bytes]], bool]:
    """Response headers with X-Request-ID set (replacing any other value) and exposed to cross-origin scripts."""
    out = [(bytes(k), bytes(v)) for k, v in (headers or ()) if bytes(k).lower() != _RID_HEADER]
    out.append((_RID_HEADER, rid.encode("latin-1")))
    names = [k.lower() for k, _ in out]
    if b"access-control-allow-origin" in names:
        if b"access-control-expose-headers" in names:
            i = names.index(b"access-control-expose-headers")
            key, value = out[i]
            if b"x-request-id" not in value.lower() and value.strip() != b"*":
                out[i] = (key, (value + b", X-Request-ID") if value.strip() else b"X-Request-ID")
        else:
            out.append((b"access-control-expose-headers", b"X-Request-ID"))
    streaming = any(k.lower() == b"content-type" and v.lower().startswith(b"text/event-stream") for k, v in out)
    return out, streaming


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp, stats: RequestStats | None = None, logger: logging.Logger | None = None) -> None:
        self.app = app
        self.stats = stats or STATS
        self.log = logger or log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        rid = clean_request_id(state.get("request_id")) or clean_request_id(_header(scope, _RID_HEADER)) or new_request_id()
        state["request_id"] = rid
        role = clean_role(_header(scope, _ROLE_HEADER))
        token = _request_id.set(rid)
        status: int | None = None
        streaming = False
        error: str | None = None
        t0 = time.perf_counter()
        self.stats.begin()

        async def send_with_id(message: Message) -> None:
            nonlocal status, streaming
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers, streaming = _with_request_id(message.get("headers"), rid)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except BaseException as exc:  # logged here, handled (or reported) by the server
            error = type(exc).__name__
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000
            _request_id.reset(token)
            try:
                self.stats.end(status, ms, streaming)
                self.log.info("%s %s %s %.1fms role=%s rid=%s%s", scope.get("method", "-"), _path(scope),
                              status if status is not None else "-", ms, role, rid, f" error={error}" if error else "")
            except Exception:  # pragma: no cover - logging must never break a response
                pass


def install(app: Any) -> None:
    """Wrap the app's whole middleware stack, so this middleware runs before every other one (idempotent)."""
    if getattr(app, "_cqa_requestlog_installed", False):
        return
    if getattr(app, "middleware_stack", None) is not None:  # already started: wrap the built stack
        app.middleware_stack = RequestLogMiddleware(app.middleware_stack)
    elif callable(getattr(app, "build_middleware_stack", None)):
        build = app.build_middleware_stack

        def build_middleware_stack() -> ASGIApp:
            return RequestLogMiddleware(build())

        app.build_middleware_stack = build_middleware_stack
    else:  # pragma: no cover - not a Starlette app
        app.add_middleware(RequestLogMiddleware)
    app._cqa_requestlog_installed = True
