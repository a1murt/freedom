"""Audit log: GET /api/audit (newest first, cursor pagination), /api/audit/facets, /api/audit/export.csv (admin)."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..audit import service as audit
from ..db import get_db
from ..security.roles import require_permission

router = APIRouter(prefix="/api/audit", tags=["audit"])

CSV_COLUMNS = ("id", "ts", "actor_role", "actor_name", "action", "action_label", "entity_type", "entity_id", "summary",
               "status_code", "outcome", "request_id", "details")


class _BadParams(Exception):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("bad params")
        self.errors = errors


def _invalid(errors: list[dict[str, str]]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error",
                                                            "message": "Некорректные параметры запроса журнала действий.",
                                                            "errors": errors}})


def _int(name: str, value: str | None, errors: list[dict[str, str]], *, minimum: int = 0) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except ValueError:
        errors.append({"field": name, "message": f"Ожидается целое число, получено «{value[:40]}»."})
        return None
    if n < minimum:
        errors.append({"field": name, "message": f"Значение должно быть не меньше {minimum}."})
        return None
    return n


def _dt(name: str, value: str | None, errors: list[dict[str, str]]) -> datetime | None:
    if value in (None, ""):
        return None
    text = value.strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "+")):  # an unencoded "+05:00" arrives as " 05:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    errors.append({"field": name, "message": "Ожидается дата и время в формате ISO 8601, например 2026-11-07T12:00:00Z."})
    return None


def _filters(request: Request) -> tuple[dict[str, Any], int | None, int | None]:
    qp = request.query_params
    errors: list[dict[str, str]] = []
    filters: dict[str, Any] = {k: qp.get(k) for k in ("action", "entity_type", "entity_id", "actor_role", "actor_name", "q")
                               if qp.get(k) not in (None, "")}
    status_code = _int("status_code", qp.get("status_code"), errors, minimum=100)
    if status_code is not None:
        filters["status_code"] = status_code
    for name in ("since", "until"):
        value = _dt(name, qp.get(name), errors)
        if value is not None:
            filters[name] = value
    limit = _int("limit", qp.get("limit"), errors, minimum=1)
    before_id = _int("before_id", qp.get("before_id"), errors, minimum=1)
    if errors:
        raise _BadParams(errors)
    return filters, limit, before_id


@router.get("")
def list_audit(request: Request, db: Session = Depends(get_db)):
    """`?action=&entity_type=&entity_id=&actor_role=&actor_name=&status_code=&q=&since=&until=&limit=100&before_id=`
    → `{"items": [AuditEvent], "next_before_id": int | null}`, newest first; limit is clamped to 1..500."""
    try:
        filters, limit, before_id = _filters(request)
    except _BadParams as e:
        return _invalid(e.errors)
    return audit.list_events(db, filters, limit=limit or audit.DEFAULT_LIMIT, before_id=before_id)


@router.get("/facets")
def audit_facets(db: Session = Depends(get_db)):
    """Distinct actions, entity types, roles and names with counts (filters of the audit log view)."""
    return audit.facets(db)


@router.get("/export.csv")
def export_audit_csv(request: Request, db: Session = Depends(get_db), _actor: dict = Depends(require_permission("admin"))):
    """The filtered audit log as CSV (same filters as GET /api/audit, `limit` up to 20000, default 5000). Needs `admin`."""
    try:
        filters, limit, _before = _filters(request)
    except _BadParams as e:
        return _invalid(e.errors)
    rows = audit.iter_events(db, filters, limit=min(limit or 5000, 20000))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for ev in rows:
        d = audit.serialize_event(ev)
        d["details"] = json.dumps(d["details"], ensure_ascii=False)
        writer.writerow([d.get(c) if d.get(c) is not None else "" for c in CSV_COLUMNS])
    return Response(content=("﻿" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="audit.csv"'})
