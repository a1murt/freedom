"""FastAPI application: routers, middlewares, error envelope, start-up hooks (docs/ARCHITECTURE_V2.md §3).

Request pipeline (outermost first): cqa.platform.requestlog (request id, access log; wraps the whole stack) →
Starlette ServerErrorMiddleware (500 handler below) → CORS → cqa.security.middleware (actor, permissions, audit) →
exception handlers (HTTPException, validation) → routes. Every middleware is pure ASGI, so SSE and ranged audio stream.
"""
from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .db import SessionLocal, init_db

log = logging.getLogger("cqa")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .checklist.service import ensure_seed

    db = SessionLocal()
    try:
        ensure_seed(db)
    finally:
        db.close()
    # optional start-up hooks owned by other modules (each must be idempotent and never raise)
    for hook in ("cqa.jobs.runner:recover_stale_jobs", "cqa.ingest.renormalize:renormalize_call_offsets"):
        mod_name, fn_name = hook.split(":")
        try:
            fn = getattr(importlib.import_module(mod_name), fn_name, None)
            if fn is not None:
                fn()
        except Exception as e:  # pragma: no cover - defensive
            log.warning("startup hook %s failed: %s", hook, e)
    yield


app = FastAPI(title="CQA API", version="0.1.0", lifespan=lifespan)
from .platform import install as install_platform  # noqa: E402

install_platform(app)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


# Starlette's own string details (unknown route, wrong method, ...) get a code and a Russian message.
_STATUS_ERRORS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Некорректный запрос."),
    401: ("unauthorized", "Требуется авторизация."),
    403: ("forbidden", "Недостаточно прав."),
    404: ("not_found", "Адрес не найден."),
    405: ("method_not_allowed", "Этот метод не поддерживается для данного адреса."),
    413: ("payload_too_large", "Слишком большой запрос."),
    415: ("unsupported_media_type", "Неподдерживаемый тип содержимого."),
    429: ("too_many_requests", "Слишком много запросов."),
}
# Russian texts for the most common pydantic error types (anything else keeps pydantic's English message).
_VALIDATION_MESSAGES: dict[str, str] = {
    "missing": "Обязательное поле.",
    "json_invalid": "Некорректный JSON в теле запроса.",
    "model_attributes_type": "Ожидается объект.",
    "dict_type": "Ожидается объект.",
    "list_type": "Ожидается список.",
    "string_type": "Ожидается строка.",
    "int_type": "Ожидается целое число.",
    "int_parsing": "Ожидается целое число.",
    "float_type": "Ожидается число.",
    "float_parsing": "Ожидается число.",
    "bool_type": "Ожидается true или false.",
    "bool_parsing": "Ожидается true или false.",
    "extra_forbidden": "Лишнее поле.",
}


def _field_path(loc: Any) -> str:
    """("body", "criteria", 2, "weight") → "criteria[2].weight" (the contract's field-path style)."""
    parts = list(loc or ())
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        source, parts = parts[0], parts[1:]
    else:
        source = "body"
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or source


def _cors_headers(request: Request) -> dict[str, str]:
    """CORS headers for responses built outside CORSMiddleware (the 500 handler runs in ServerErrorMiddleware)."""
    origin = request.headers.get("origin")
    if not origin:
        return {}
    if "*" in settings.cors_origins:
        return {"Access-Control-Allow-Origin": "*"}
    if origin in settings.cors_origins:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


@app.exception_handler(StarletteHTTPException)
async def http_exc(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        detail = exc.detail
    else:
        text = str(exc.detail) if exc.detail is not None else ""
        try:
            phrase = HTTPStatus(exc.status_code).phrase
        except ValueError:
            phrase = ""
        code, message = _STATUS_ERRORS.get(exc.status_code, ("error", text or phrase))
        detail = {"code": code, "message": message if text in ("", phrase) else text}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=getattr(exc, "headers", None))


def _guard_error(request: Request, exc: Exception):
    """cqa.jobs.guards.GuardError raised by a route (e.g. a judge-spec regeneration with the daily budget exhausted):
    429 with the guard's envelope and Retry-After, like the evaluation run endpoint."""
    retry = getattr(exc, "retry_after_s", None)
    headers = {"Retry-After": str(retry)} if retry else None
    return JSONResponse(status_code=getattr(exc, "status", 429), content=exc.envelope(), headers=headers)  # type: ignore[attr-defined]


try:
    from .jobs.guards import GuardError as _GuardError

    app.add_exception_handler(_GuardError, _guard_error)
except Exception as e:  # pragma: no cover - never block the API start
    log.warning("guard error handler not installed: %s", e)


@app.exception_handler(RequestValidationError)
async def validation_exc(request: Request, exc: RequestValidationError):
    """422 in the contract envelope (docs/ARCH_CONTRACTS.md §4). FastAPI's `detail` list is kept next to it for old clients."""
    raw = exc.errors()
    errors = [{"field": "body" if e.get("type") == "json_invalid" else _field_path(e.get("loc")),  # loc = ("body", <char position>)
               "message": _VALIDATION_MESSAGES.get(str(e.get("type")), str(e.get("msg") or ""))} for e in raw]
    first = errors[0] if errors else None
    message = "Некорректный запрос: проверьте формат JSON и типы полей."
    if first:
        message += f" Первая ошибка — {first['field']}: {first['message']}"
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": message, "errors": errors},
                                                  "detail": jsonable_encoder(raw)})


@app.exception_handler(Exception)
async def any_exc(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", None)
    log.exception("unhandled error (request %s)", rid)
    from .audit.service import scrub_text  # masks keys and tokens that an exception text might carry

    message = "Внутренняя ошибка сервера. Повторите запрос; если ошибка повторится, сообщите команде код запроса"
    message += f" {rid}." if rid else "."
    return JSONResponse(status_code=500, headers=_cors_headers(request),
                        content={"error": {"code": "internal", "message": message, "request_id": rid, "type": type(exc).__name__,
                                           "detail": scrub_text(str(exc))[:500]}})


for mod in ("conversations", "checklists", "evaluations", "appeals", "settings_router", "audio", "analytics", "export", "insights",
            "system", "admin", "integrations", "calibration", "audit", "roles"):
    try:
        m = importlib.import_module(f"cqa.routers.{mod}")
        app.include_router(m.router)
    except Exception as e:  # a missing optional router must not take the API down
        log.warning("router %s not loaded: %s", mod, e)
