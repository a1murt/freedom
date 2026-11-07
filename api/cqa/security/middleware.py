"""Pure ASGI security middleware: actor context, permissions on writes, audit trail (docs/ARCH_CONTRACTS.md §1–§2).

For every HTTP request under /api/:
- resolves the actor from X-CQA-Role / X-CQA-User (URL-encoded or raw UTF-8) and the request id (the one the platform
  request log put in `scope["state"]`, else X-Request-ID, else a new one) and stores it in `scope["state"]["actor"]`,
  i.e. `request.state.actor`; an unknown role is answered with 400 `unknown_role` (OPTIONS is never refused);
- GET/HEAD/OPTIONS are passed on untouched (receive and send are not wrapped), so SSE (`/api/jobs/{id}/events`) and
  ranged audio (`/api/audio/{id}`) stream exactly as without the middleware;
- writes (POST/PUT/PATCH/DELETE) are looked up in `roles.ROUTE_MAP`; a role without the rule's permission gets 403
  `forbidden` and the app is not called;
- an allowed write runs with a tee on `receive` (the app reads its body as usual, the first 1 MB is copied for the
  summary; nothing is read ahead or replayed) and a tap on `send` (every message is forwarded as it comes);
- when the write ends with 2xx, or with 403 (refused here or by the handler, e.g. `not_your_dialog`), one AuditEvent is
  written with its own DB session right before the last body chunk is forwarded, so a client that refetches the audit
  log after the response always sees its event. A failing audit write is logged and never changes the response.

Audit session: `app.state.audit_session_factory` (a sessionmaker) → the app's dependency override of `get_db` (tests)
→ `cqa.db.SessionLocal`.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl

import anyio
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..audit import service as audit
from ..db import get_db
from . import roles

log = logging.getLogger("cqa.security")

CAPTURE_LIMIT = 1024 * 1024  # bytes of a request / response body copied for the audit summary (writes only)
DENIED_READ_LIMIT = 64 * 1024  # bytes read from a refused request (for its summary; the app never sees it)
QUERY_SUMMARY_MAX_BYTES = 1024

_FILENAME_RE = re.compile(rb'filename="([^"\r\n]*)"')
_FIELD_RE = re.compile(rb'(?<![A-Za-z])name="([^"\r\n]*)"')
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][\w.]*)\}")


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _default_session_factory() -> Callable[[], Session]:
    from ..db import SessionLocal

    return SessionLocal


# ----------------------------------------------------------------------------- the middleware


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not is_api_path(scope.get("path") or ""):
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        state = scope.get("state")
        if state is None:
            state = scope["state"] = {}
        headers = list(scope.get("headers") or ())
        rid = (roles.clean_request_id(state.get("request_id"))
               or roles.clean_request_id(roles.header_value(headers, b"x-request-id")) or roles.new_request_id())
        state.setdefault("request_id", rid)
        try:
            actor = roles.actor_from_headers(headers, request_id=rid)
        except roles.UnknownRoleError as e:
            if method == "OPTIONS":
                await self.app(scope, receive, send)
            else:
                await send_json(send, 400, {"error": roles.unknown_role_detail(e.role)})
            return
        state["actor"] = actor
        state["actor_source"] = "middleware"
        if method in roles.SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        rule, params = roles.match_route(method, scope.get("path") or "")
        if not roles.has_permission(actor["role"], rule.permission):
            await self._deny(scope, receive, send, actor, rule, params, method)
            return
        if not rule.audit:
            await self.app(scope, receive, send)
            return
        await self._run_audited(scope, receive, send, actor, rule, params, method)

    async def _deny(self, scope: Scope, receive: Receive, send: Send, actor: dict[str, Any], rule: roles.RouteRule,
                    params: dict[str, str], method: str) -> None:
        started = time.perf_counter()
        payload = {"error": roles.forbidden_detail(actor["role"], rule.permission, rule.action)}
        body, total, complete = await read_body(receive, DENIED_READ_LIMIT)
        try:
            event = build_event(scope, actor, rule, params, method, body=body, body_total=total,
                                body_truncated=(not complete) or total > len(body), status=403, response_json=payload,
                                started=started, denied=True)
            await self._write(scope, event)
        except Exception as e:  # noqa: BLE001 - defensive: the 403 must go out  # pragma: no cover
            log.warning("audit event for a refused %s %s not built: %s", method, scope.get("path"), e)
        await send_json(send, 403, payload)

    async def _run_audited(self, scope: Scope, receive: Receive, send: Send, actor: dict[str, Any],
                           rule: roles.RouteRule, params: dict[str, str], method: str) -> None:
        started = time.perf_counter()
        tee = BodyTee(receive, CAPTURE_LIMIT)
        tap = ResponseTap(CAPTURE_LIMIT)
        finished = False

        async def send_tapped(message: Message) -> None:
            nonlocal finished
            kind = message.get("type")
            if kind == "http.response.start":
                tap.start(message)
            elif kind == "http.response.body":
                tap.feed(message.get("body") or b"")
                if not message.get("more_body", False) and not finished:
                    finished = True
                    await self._finish(scope, actor, rule, params, method, tee, tap, started)
            elif kind == "http.response.pathsend" and not finished:  # file response sent by path: also the last message
                finished = True
                await self._finish(scope, actor, rule, params, method, tee, tap, started)
            await send(message)

        await self.app(scope, tee.receive, send_tapped)

    async def _finish(self, scope: Scope, actor: dict[str, Any], rule: roles.RouteRule, params: dict[str, str], method: str,
                      tee: BodyTee, tap: ResponseTap, started: float) -> None:
        status = tap.status or 200
        if not (200 <= status < 300 or status == 403):  # errors and redirects wrote nothing
            return
        try:
            event = build_event(scope, actor, rule, params, method, body=bytes(tee.buf), body_total=tee.total,
                                body_truncated=tee.truncated, status=status, response_json=tap.json(), started=started,
                                denied=False)
        except Exception as e:  # noqa: BLE001 - defensive: never break a finished response  # pragma: no cover
            log.warning("audit event for %s %s not built: %s", method, scope.get("path"), e)
            return
        await self._write(scope, event)

    async def _write(self, scope: Scope, event: dict[str, Any]) -> None:
        try:
            await anyio.to_thread.run_sync(write_event, scope.get("app"), event)
        except Exception as e:  # noqa: BLE001 - the audit trail must never break a response
            log.warning("audit event %s not written: %s", event.get("action"), e)


def install(app: Any, session_factory: Callable[[], Session] | None = None) -> None:
    """Add the middleware (idempotent). It goes innermost, right around the routes: inside CORS and the error handlers,
    and inside the platform request log, which wraps the whole stack and therefore logs this middleware's 400/403 too.
    `session_factory` (or `app.state.audit_session_factory`) sets where audit events are written."""
    if session_factory is not None:
        app.state.audit_session_factory = session_factory
    stack = getattr(app, "user_middleware", None)
    if isinstance(stack, list) and any(getattr(m, "cls", None) is SecurityMiddleware for m in stack):
        return
    if isinstance(stack, list) and getattr(app, "middleware_stack", None) is None:
        from starlette.middleware import Middleware

        stack.append(Middleware(SecurityMiddleware))
    else:
        app.add_middleware(SecurityMiddleware)


# ----------------------------------------------------------------------------- ASGI helpers


async def send_json(send: Send, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("latin-1"))]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def read_body(receive: Receive, limit: int) -> tuple[bytes, int, bool]:
    """Read a request body the app will never see: (first `limit` bytes, bytes read, whether the body ended)."""
    buf, total = bytearray(), 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return bytes(buf), total, False
        chunk = message.get("body") or b""
        total += len(chunk)
        if len(buf) < limit:
            buf += chunk[: limit - len(buf)]
        if not message.get("more_body", False):
            return bytes(buf), total, True
        if total >= limit:
            return bytes(buf), total, False


class BodyTee:
    """Wraps `receive`: passes every message to the app unchanged and copies the first `limit` body bytes."""

    def __init__(self, receive: Receive, limit: int) -> None:
        self._receive = receive
        self.limit = limit
        self.buf = bytearray()
        self.total = 0
        self.complete = False

    async def receive(self) -> Message:
        message = await self._receive()
        if message.get("type") == "http.request":
            chunk = message.get("body") or b""
            if chunk:
                self.total += len(chunk)
                room = self.limit - len(self.buf)
                if room > 0:
                    self.buf += chunk[:room]
            if not message.get("more_body", False):
                self.complete = True
        return message

    @property
    def truncated(self) -> bool:
        return self.total > len(self.buf) or not self.complete


class ResponseTap:
    """Status, headers and the first `limit` bytes of the response (a copy; the messages themselves are forwarded)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.status: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.buf = bytearray()
        self.total = 0

    def start(self, message: Message) -> None:
        self.status = int(message.get("status") or 200)
        self.headers = [(bytes(k), bytes(v)) for k, v in message.get("headers") or ()]

    def feed(self, chunk: bytes) -> None:
        if chunk:
            self.total += len(chunk)
            room = self.limit - len(self.buf)
            if room > 0:
                self.buf += chunk[:room]

    def json(self) -> Any:
        if not self.buf or self.total > len(self.buf):
            return None
        if b"json" not in (roles.header_value(self.headers, b"content-type") or b"").lower():
            return None
        if (roles.header_value(self.headers, b"content-encoding") or b"identity").strip().lower() != b"identity":
            return None
        try:
            return json.loads(bytes(self.buf))
        except ValueError:
            return None


# ----------------------------------------------------------------------------- audit event


def open_audit_session(app: Any) -> tuple[Session, Callable[[], None]]:
    """A session for the audit write and how to close it: app.state.audit_session_factory → dependency override of
    get_db → SessionLocal. An async override cannot be used from the worker thread: set the factory instead."""
    state = getattr(app, "state", None)
    factory = getattr(state, "audit_session_factory", None) if state is not None else None
    if factory is not None:
        db = factory()
        return db, db.close
    override = (getattr(app, "dependency_overrides", None) or {}).get(get_db)
    if override is not None:
        if inspect.isasyncgenfunction(override) or inspect.iscoroutinefunction(override):
            raise RuntimeError("async get_db override: set app.state.audit_session_factory for the audit trail")
        if inspect.isgeneratorfunction(override):
            gen = override()
            db = next(gen)

            def close() -> None:
                try:
                    next(gen)
                except StopIteration:
                    pass
                finally:
                    gen.close()

            return db, close
        db = override()
        return db, getattr(db, "close", lambda: None)
    db = _default_session_factory()()
    return db, db.close


def write_event(app: Any, event: dict[str, Any]) -> None:
    db, close = open_audit_session(app)
    try:
        audit.record(db, **event)
    except Exception:
        try:
            db.rollback()
        except Exception as e:  # noqa: BLE001 - keep the original error  # pragma: no cover
            log.debug("audit rollback failed: %s", e)
        raise
    finally:
        close()


def dig(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _content_type(scope: Scope) -> str:
    raw = roles.header_value(scope.get("headers") or (), b"content-type") or b""
    return raw.decode("latin-1").split(";")[0].strip().lower()


def _looks_json(content_type: str, raw: bytes) -> bool:
    return "json" in content_type or (not content_type and raw.lstrip()[:1] in (b"{", b"["))


def parse_json_body(raw: bytes, content_type: str, truncated: bool) -> Any:
    if truncated or not raw or not _looks_json(content_type, raw):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def summarize_body(raw: bytes, content_type: str, total: int, truncated: bool, parsed: Any = None) -> Any:
    """Compact body summary (≤ 2 KB): compacted JSON, or file and field names of a multipart upload, or just the size."""
    if not total:
        return None
    if content_type.startswith("multipart/"):
        out: dict[str, Any] = {"content_type": content_type, "bytes": total}
        files = [audit.scrub_text(_decode(f))[:200] for f in _FILENAME_RE.findall(raw)][:10]
        fields: list[str] = []
        for f in _FIELD_RE.findall(raw):
            name = _decode(f)[:64]
            if name not in fields:
                fields.append(name)
        if files:
            out["files"] = files
        if fields:
            out["fields"] = fields[:20]
        return out
    if parsed is not None:
        return audit.compact_to_size(parsed, audit.BODY_SUMMARY_MAX_BYTES)
    if _looks_json(content_type, raw):
        return {"content_type": content_type or "application/json", "bytes": total,
                ("truncated" if truncated else "invalid_json"): True}
    if content_type == "application/x-www-form-urlencoded" and not truncated:
        pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return audit.compact_to_size(dict(pairs[:40]), audit.BODY_SUMMARY_MAX_BYTES)
    return {"content_type": content_type or None, "bytes": total}


def call_refine(refine: Callable[..., str | None], body_json: Any, query_string: bytes) -> str | None:
    """`refine(body)` or, for hooks that take a second parameter, `refine(body, query)` (query as a flat dict)."""
    try:
        n_params = len(inspect.signature(refine).parameters)
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        n_params = 1
    if n_params >= 2:
        query = dict(parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)) if query_string else {}
        return refine(body_json, query)
    return refine(body_json) if body_json is not None else None


def summarize_query(query_string: bytes) -> Any:
    if not query_string:
        return None
    pairs = parse_qsl(query_string.decode("latin-1"), keep_blank_values=True, encoding="utf-8", errors="replace")
    out: dict[str, Any] = {}
    for k, v in pairs[:40]:
        if k in out:
            prev = out[k]
            out[k] = (prev if isinstance(prev, list) else [prev]) + [v]
        else:
            out[k] = v
    return audit.compact_to_size(out, QUERY_SUMMARY_MAX_BYTES) if out else None


def resolve_entity(rule: roles.RouteRule, params: Mapping[str, Any], body: Any, response: Any) -> tuple[str | None, str | None]:
    """(entity_type, entity_id) from the rule's sources. A rule without sources names a singleton or a collection
    (settings, gold_labels): its type is kept. When the sources give no id (a dry run, a refused request) both are None."""
    if not rule.entity:
        return rule.entity_type, None
    roots = {"path": params, "body": body, "response": response}
    for spec in rule.entity:
        entity_type = rule.entity_type
        if "=" in spec:
            entity_type, spec = spec.split("=", 1)
        source, _, key = spec.partition(":")
        value = dig(roots.get(source), key)
        if value is None or value == "" or isinstance(value, (bool, dict, list)):
            continue
        return entity_type, str(value)
    return None, None


def capture_fields(fields: tuple[str, ...], response: Any) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name in fields:
        value = dig(response, name)
        if value is not None:
            out[name] = audit.compact(value, str_limit=200, list_limit=20, max_depth=2)
    return out


def decision_text(body: Any) -> str | None:
    if not isinstance(body, Mapping):
        return None
    if body.get("action") == "confirm":
        return "оценка подтверждена"
    if body.get("action") == "change":
        new = body.get("new_answer")
        return f"ответ изменён на «{new}»" if new is not None else "ответ изменён"
    return None


class _Missing(Exception):
    pass


def render_template(template: str, ctx: Mapping[str, Any]) -> str | None:
    """Fill `{a.b}` placeholders from `ctx`; None when any value is missing (the caller falls back to a generic text)."""

    def repl(m: re.Match[str]) -> str:
        value = dig(ctx, m.group(1))
        if value is None or value == "" or isinstance(value, Mapping) or value == []:
            raise _Missing
        if isinstance(value, (list, tuple)):
            text = ", ".join(str(v) for v in value[:10]) + ("…" if len(value) > 10 else "")
        elif isinstance(value, bool):
            text = "да" if value else "нет"
        elif isinstance(value, float):
            text = f"{value:g}"
        else:
            text = str(value)
        text = audit.scrub_text(" ".join(text.split()))
        return text if len(text) <= 120 else text[:120] + "…"

    try:
        return _PLACEHOLDER_RE.sub(repl, template)
    except _Missing:
        return None


def make_summary(action: str, ctx: Mapping[str, Any], *, status: int, denied: bool, actor: Mapping[str, Any],
                 permission: str, error: Mapping[str, Any] | None) -> str:
    label = roles.action_label(action) or action
    if status == 403:
        if denied:
            return (f"Отказано: {label} — роль «{roles.role_label(actor.get('role'))}» "
                    f"без права «{roles.permission_label(permission)}»")
        message = (error or {}).get("message")
        return f"Отказано: {label}" + (f" — {message}" if message else "")
    template = (roles.ACTIONS.get(action) or {}).get("summary")
    if template:
        text = render_template(template, ctx)
        if text:
            return text
    entity_id = ctx.get("entity_id")
    return f"{label} ({ctx.get('entity_type') or 'id'} {entity_id})" if entity_id else label


def build_event(scope: Scope, actor: dict[str, Any], rule: roles.RouteRule, params: dict[str, str], method: str, *,
                body: bytes, body_total: int, body_truncated: bool, status: int, response_json: Any, started: float,
                denied: bool) -> dict[str, Any]:
    """Keyword arguments for `cqa.audit.record` describing one finished write request."""
    content_type = _content_type(scope)
    body_json = parse_json_body(body, content_type, body_truncated)
    action = rule.action
    if rule.refine is not None:
        try:
            action = call_refine(rule.refine, body_json, scope.get("query_string") or b"") or action
        except Exception as e:  # noqa: BLE001 - a refine hook must not break the audit  # pragma: no cover
            log.debug("refine hook of %s failed: %s", rule.action, e)
    path = roles.normalize_path(scope.get("path") or "")
    response = response_json if isinstance(response_json, Mapping) else None
    ok_response = response if status < 400 else None
    entity_type, entity_id = resolve_entity(rule, params, body_json, ok_response)

    details: dict[str, Any] = {"method": method, "path": path}
    if params:
        details["path_params"] = dict(params)
    query = summarize_query(scope.get("query_string") or b"")
    if query:
        details["query"] = query
    body_summary = summarize_body(body, content_type, body_total, body_truncated, parsed=body_json)
    if body_summary is not None:
        details["body"] = body_summary
    captured = capture_fields(rule.capture, ok_response)
    if captured:
        details["response"] = captured
    error: dict[str, Any] | None = None
    if status >= 400:
        err = response.get("error") if response else None
        if isinstance(err, Mapping):
            error = {k: audit.compact(err[k], str_limit=300) for k in ("code", "message") if err.get(k) is not None}
            details["error"] = error
        if denied:
            details["permission"] = rule.permission
    details["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)

    files = body_summary.get("files") if isinstance(body_summary, Mapping) else None
    ctx = {"path": params, "body": body_json if isinstance(body_json, Mapping) else {}, "response": ok_response or {},
           "upload": {"filename": files[0]} if files else {}, "entity_id": entity_id, "entity_type": entity_type,
           "request": {"method": method, "path": path}, "decision": decision_text(body_json),
           "body_keys": sorted(map(str, body_json)) if isinstance(body_json, Mapping) else None}
    summary = make_summary(action, ctx, status=status, denied=denied, actor=actor, permission=rule.permission, error=error)
    return {"action": action, "entity_type": entity_type, "entity_id": entity_id, "summary": summary, "details": details,
            "actor": actor, "status_code": status, "request_id": actor.get("request_id")}
