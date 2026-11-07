"""Gross violations, operator directory, executive overview and operator scorecards.

Organizer live asks: gross errors with explanations; operator appeals. Executive panels for directors and QA leads
(docs/arch/overview.md). Every endpoint here is a GET (permission ``read``).
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any
from urllib.parse import unquote, unquote_plus

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ..analytics.coaching import OperatorNotFound, operator_scorecard
from ..analytics.overview import DEFAULT_DAYS, MAX_DAYS, overview
from ..analytics.violations import operators, violations
from ..db import get_db
from ..models import ChecklistVersion

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/violations")
def list_violations(
    checklist_version_id: int | None = Query(default=None),
    type: str | None = Query(default=None, description="insult | paid_bypass | disclosure | other"),
    language: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    operator: str | None = Query(default=None),
    include_reviewed_out: bool = Query(default=False, description="also list AI-flagged cases a reviewer overturned"),
    dataset_id: int | None = Query(default=None, description="Only dialogs of this imported set"),
    since: datetime | None = Query(default=None, description="Evaluated at or after (ISO 8601)"),
    until: datetime | None = Query(default=None, description="Evaluated at or before (ISO 8601)"),
    db: Session = Depends(get_db),
):
    return violations(db, checklist_version_id=checklist_version_id, kind=type, language=language, channel=channel, operator=operator,
                      include_reviewed_out=include_reviewed_out, dataset_id=dataset_id, since=since, until=until)


@router.get("/violations/candidates")
def list_near_misses(checklist_version_id: int | None = Query(default=None), language: str | None = Query(default=None),
                     channel: str | None = Query(default=None), db: Session = Depends(get_db)):
    """Dialogs cleared of gross errors despite a warning sign, with the judge's reason (GV-08)."""
    from ..analytics.violations import near_misses

    return near_misses(db, checklist_version_id=checklist_version_id, language=language, channel=channel)


@router.get("/operators")
def list_operators(checklist_version_id: int | None = Query(default=None), language: str | None = Query(default=None),
                   channel: str | None = Query(default=None), dataset_id: int | None = Query(default=None), db: Session = Depends(get_db)):
    return operators(db, checklist_version_id=checklist_version_id, language=language, channel=channel, dataset_id=dataset_id)


# --------------------------------------------------------------------------- overview and scorecards (package "overview")

_VALIDATION_MESSAGES = {
    "int_parsing": "ожидается целое число",
    "int_type": "ожидается целое число",
    "int_from_float": "ожидается целое число",
    "greater_than_equal": "значение должно быть не меньше {ge}",
    "less_than_equal": "значение должно быть не больше {le}",
    "missing": "обязательный параметр",
}


def _validation_error(errors: list[dict[str, str]]) -> dict[str, Any]:
    details = "; ".join(f"{e['field']}: {e['message']}" for e in errors)
    return {"code": "validation_error", "message": f"Некорректные параметры запроса ({details})", "errors": errors}


class _EnvelopeRoute(APIRoute):
    """Query/path validation errors in the project envelope (docs/ARCH_CONTRACTS.md §4) instead of FastAPI's default."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = []
                for e in exc.errors():
                    field = ".".join(str(p) for p in e.get("loc", ()) if p not in ("query", "path", "body")) or "request"
                    template = _VALIDATION_MESSAGES.get(e.get("type", ""), "некорректное значение")
                    try:
                        message = template.format(**(e.get("ctx") or {}))
                    except (KeyError, IndexError, ValueError):
                        message = template
                    errors.append({"field": field, "message": message})
                return JSONResponse(status_code=422, content={"error": _validation_error(errors)})

        return route_handler


def _ensure_version(db: Session, checklist_version_id: int | None) -> None:
    if checklist_version_id is not None and db.get(ChecklistVersion, checklist_version_id) is None:
        raise HTTPException(404, detail={"code": "checklist_version_not_found",
                                         "message": f"Версия чек-листа {checklist_version_id} не найдена"})


def get_overview(
    checklist_version_id: int | None = Query(default=None, description="Restrict to one checklist version (default: every version)"),
    days: int = Query(default=DEFAULT_DAYS, description=f"Trend window in days, 1..{MAX_DAYS}"),
    language: str | None = Query(default=None, description="RU | KZ | EN"),
    channel: str | None = Query(default=None, description="agent chat | voice call"),
    dataset_id: int | None = Query(default=None, description="Only dialogs of this imported set"),
    operator: str | None = Query(default=None, description="Only this operator's dialogs"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(422, detail=_validation_error([{"field": "days", "message": f"период должен быть от 1 до {MAX_DAYS} дней"}]))
    _ensure_version(db, checklist_version_id)
    return overview(db, checklist_version_id=checklist_version_id, days=days, language=language, channel=channel,
                    dataset_id=dataset_id, operator=operator)


def _name_candidates(name: str) -> list[str]:
    """The path is URL-decoded once by the server; also accept double-encoded names and '+' for spaces."""
    out: list[str] = []
    for candidate in (name, unquote(name), unquote_plus(name)):
        candidate = candidate.strip()
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def get_operator_scorecard(
    name: str,
    checklist_version_id: int | None = Query(default=None, description="Restrict to one checklist version (default: every version)"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _ensure_version(db, checklist_version_id)
    for candidate in _name_candidates(name):
        try:
            return operator_scorecard(db, candidate, checklist_version_id)
        except OperatorNotFound:
            continue
    message = f"Оператор «{name.strip()}» не найден" if name.strip() else "Не указано имя оператора"
    raise HTTPException(404, detail={"code": "operator_not_found", "message": message})


# Registered on the same router (no nested router) with the envelope route class: GET /api/overview and
# GET /api/operators/{name}; the list GET /api/operators above stays as it was.
router.add_api_route("/overview", get_overview, methods=["GET"], route_class_override=_EnvelopeRoute)
router.add_api_route("/operators/{name}", get_operator_scorecard, methods=["GET"], route_class_override=_EnvelopeRoute)
