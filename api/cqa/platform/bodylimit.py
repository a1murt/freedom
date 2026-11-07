"""Request body size limits for writes under /api/ (pure ASGI, never BaseHTTPMiddleware).

Every byte of a stored transcript can end up in every judge call of a paid evaluation, and a request body is held in
memory while it is parsed, so writes are capped:

| Path | Limit |
|---|---|
| POST /api/conversations/paste | 2 MB |
| POST /api/conversations/upload | 10 MB |
| POST /api/integrations/dynamics/transcripts | 8 MB |
| /api/checklists… | 6 MB |
| any other POST/PUT/PATCH under /api/ | 10 MB |

A declared Content-Length over the limit is answered 413 `payload_too_large` before the body is read; a body without a
declared length (chunked) is counted while the app reads it and the read fails with the same 413. GET/HEAD/OPTIONS and
everything outside /api/ pass through untouched (receive and send are not wrapped), so SSE and ranged audio stream as
before. Installed between CORS and the security middleware, so a 413 carries CORS headers.
"""
from __future__ import annotations

import json
from typing import Any

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MB = 1024 * 1024
LIMITS: tuple[tuple[str, int], ...] = (
    ("/api/conversations/paste", 2 * MB),
    ("/api/conversations/upload", 10 * MB),
    ("/api/integrations/dynamics/", 8 * MB),
    ("/api/checklists", 6 * MB),
)
DEFAULT_LIMIT = 10 * MB
WRITE_METHODS = ("POST", "PUT", "PATCH")


def limit_for(path: str) -> int:
    for prefix, limit in LIMITS:
        if path == prefix or path.startswith(prefix):
            return limit
    return DEFAULT_LIMIT


def _message(limit: int) -> str:
    return f"Слишком большой запрос: допускается не больше {limit // MB} МБ."


class BodyTooLarge(HTTPException):
    """Raised from `receive` once the body passes the limit: FastAPI re-raises HTTPException while reading a body, and
    the app's handler answers it with the error envelope."""

    def __init__(self, limit: int):
        super().__init__(413, detail={"code": "payload_too_large", "message": _message(limit), "limit_bytes": limit})
        self.limit = limit


async def _send_413(send: Send, limit: int) -> None:
    body = json.dumps({"error": {"code": "payload_too_large", "message": _message(limit), "limit_bytes": limit}},
                      ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("latin-1"))]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == b"content-length":
            try:
                return int(value.decode("latin-1").strip())
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path") or ""
        if scope.get("type") != "http" or str(scope.get("method") or "").upper() not in WRITE_METHODS or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        limit = limit_for(path)
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await _send_413(send, limit)
            return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except BodyTooLarge:
            if not started:  # raised outside a route (e.g. while a refused request was summarized)
                await _send_413(send, limit)


def install(app: Any) -> None:
    """Add the middleware (idempotent) right outside the security middleware: CORS (added later in main.py) stays outside."""
    stack = getattr(app, "user_middleware", None)
    if isinstance(stack, list) and any(getattr(m, "cls", None) is BodyLimitMiddleware for m in stack):
        return
    if isinstance(stack, list) and getattr(app, "middleware_stack", None) is None:
        from starlette.middleware import Middleware

        stack.append(Middleware(BodyLimitMiddleware))
    else:
        app.add_middleware(BodyLimitMiddleware)
