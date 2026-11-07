"""GET /api/export/evaluations.csv and GET /api/export/evaluations/{id}.pdf (see docs/API.md)."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..export.csv_export import evaluations_csv
from ..export.pdf_export import EvaluationNotFound, evaluation_pdf
from ..models import Evaluation

router = APIRouter(prefix="/api/export", tags=["export"])


def _download_headers(disposition: str) -> dict[str, str]:
    """A cross-origin script (the web app on another host) reads only the headers listed in Expose-Headers: list
    Content-Disposition so it can save the file under the server's name (X-Request-ID is appended by cqa.platform)."""
    return {"Content-Disposition": disposition, "Access-Control-Expose-Headers": "Content-Disposition"}


def _team_name(explicit: str | None = None) -> str:
    """team_name of results.json: the query parameter, else env TEAM_NAME, else the settings (one source for the file
    and its dry run, so the export dialog shows the name the file will carry)."""
    import os

    from ..config import settings

    return explicit or os.environ.get("TEAM_NAME") or settings.team_name


def _issues(errors: list[str], warnings: list[str], conversation_ids: Iterable[Any]) -> list[dict[str, Any]]:
    """The validator's strings as [{conversation_id, severity, message}]: a message about one record starts with
    "<conversation_id>: " (the prefix becomes the field, so the dialog can link to its card); file-level messages
    (schema, model, missing ids, record count) have conversation_id null. Errors first, each list in its own order."""
    known = {c for c in conversation_ids if isinstance(c, str) and c}
    # ids that contain ": " themselves (rare uploaded ids) are matched whole, longest first, before the plain split
    odd = sorted((c for c in known if ": " in c), key=len, reverse=True)
    out: list[dict[str, Any]] = []
    for severity, texts in (("error", errors), ("warning", warnings)):
        for text in texts:
            cid = next((c for c in odd if text.startswith(f"{c}: ")), None)
            if cid is None:
                head, sep, _ = text.partition(": ")
                cid = head if sep and head in known else None
            out.append({"conversation_id": cid, "severity": severity, "message": text[len(cid) + 2:] if cid is not None else text})
    return out


@router.get("/evaluations.csv")
def export_evaluations_csv(
    checklist_version_id: int | None = Query(default=None, description="Restrict to one checklist version"),
    db: Session = Depends(get_db),
) -> Response:
    body = evaluations_csv(db, checklist_version_id)
    suffix = f"_v{checklist_version_id}" if checklist_version_id is not None else ""
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers=_download_headers(f'attachment; filename="evaluations{suffix}.csv"'),
    )


def _csv(body: str, filename: str) -> Response:
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers=_download_headers(f'attachment; filename="{filename}"'))


@router.get("/violations.csv")
def export_violations_csv(
    checklist_version_id: int | None = Query(default=None),
    type: str | None = Query(default=None, description="insult | paid_bypass | disclosure | other"),
    language: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    operator: str | None = Query(default=None),
    include_reviewed_out: bool = Query(default=False),
    dataset_id: int | None = Query(default=None, description="Only dialogs of this imported set"),
    since: datetime | None = Query(default=None, description="Evaluated at or after (ISO 8601)"),
    until: datetime | None = Query(default=None, description="Evaluated at or before (ISO 8601)"),
    db: Session = Depends(get_db),
) -> Response:
    """Gross-violations register (same items and filters as GET /api/violations)."""
    from ..export.registers_csv import violations_csv

    return _csv(violations_csv(db, checklist_version_id=checklist_version_id, kind=type, language=language, channel=channel,
                               operator=operator, include_reviewed_out=include_reviewed_out, dataset_id=dataset_id, since=since,
                               until=until), "violations.csv")


@router.get("/decisions.csv")
def export_decisions_csv(
    status: str | None = Query(default=None, description="open | decided | withdrawn"),
    operator: str | None = Query(default=None),
    author_role: str | None = Query(default=None),
    code: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    language: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Substring of the comment, conversation id, operator or author name"),
    db: Session = Depends(get_db),
) -> Response:
    """Appeals and reviewer decisions, one row per appeal (same filters as GET /api/appeals)."""
    from ..export.registers_csv import decisions_csv

    return _csv(decisions_csv(db, status=status, operator=operator, author_role=author_role, code=code, conversation_id=conversation_id,
                              language=language, channel=channel, q=q), "decisions.csv")


@router.get("/evaluations/{evaluation_id}.pdf")
def export_evaluation_pdf(evaluation_id: int, db: Session = Depends(get_db)) -> Response:
    if db.get(Evaluation, evaluation_id) is None:
        return _not_found(evaluation_id)
    try:
        pdf = evaluation_pdf(db, evaluation_id)
    except EvaluationNotFound:
        return _not_found(evaluation_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers=_download_headers(f'inline; filename="evaluation_{evaluation_id}.pdf"'),
    )


@router.get("/results.json")
def export_results_json(
    group_id: str | None = Query(default=None, description="Dataset group_id, e.g. DEV or the control group id"),
    conversation_ids: str | None = Query(default=None, description="Comma-separated ids (overrides group_id)"),
    checklist_version_id: int | None = Query(default=None, description="Default: the organizer checklist (version 1)"),
    team_name: str | None = Query(default=None),
    expect: int | None = Query(default=None, description="Expected record count (prod: 10)"),
    download: bool = Query(default=True),
    answers: str = Query(default="ai", description="ai = the judge's answers (default); final = after reviewer decisions"),
    db: Session = Depends(get_db),
) -> Response:
    """Organizer results file (JSON 1.0, docs/ORGANIZER_UPDATES.md §1) with validation report in headers."""
    import json

    from ..export.results_json import ExportError, from_db, validate_document

    ids = [x.strip() for x in conversation_ids.split(",") if x.strip()] if conversation_ids else None
    team = _team_name(team_name)
    try:
        doc, turn_counts, channels = from_db(db, group_id=group_id, conversation_ids=ids, checklist_version_id=checklist_version_id,
                                             team_name=team, answers=answers)
    except ExportError as e:
        return JSONResponse(status_code=400, content={"error": {"code": "export_failed", "message": str(e)}})
    errors, warnings = validate_document(doc, expected_ids=list(turn_counts), expected_count=expect, turn_counts=turn_counts, channels=channels)
    body = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    headers = {
        "X-Results-Valid": "true" if not errors else "false",
        "X-Results-Errors": str(len(errors)),
        "X-Results-Warnings": str(len(warnings)),
        "Access-Control-Expose-Headers": "X-Results-Valid, X-Results-Errors, X-Results-Warnings, Content-Disposition",
    }
    if download:
        name = f"results_{(group_id or 'selection').lower()}.json"
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(content=body.encode("utf-8"), media_type="application/json; charset=utf-8", headers=headers)


@router.get("/results/validate")
def validate_results(
    group_id: str | None = Query(default=None),
    conversation_ids: str | None = Query(default=None),
    checklist_version_id: int | None = Query(default=None),
    expect: int | None = Query(default=None),
    answers: str = Query(default="ai", description="ai (default) or final (after reviewer decisions)"),
    team_name: str | None = Query(default=None, description="As in results.json (default: env TEAM_NAME, else the settings)"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Dry run of the results export: counts, errors and warnings, without the file body. The document is built with the
    team name the file will carry, so ``valid`` also covers it; ``issues`` repeats errors and warnings per dialog."""
    from ..export.results_json import ExportError, from_db, summarize, validate_document

    ids = [x.strip() for x in conversation_ids.split(",") if x.strip()] if conversation_ids else None
    try:
        doc, turn_counts, channels = from_db(db, group_id=group_id, conversation_ids=ids, checklist_version_id=checklist_version_id,
                                             team_name=_team_name(team_name), answers=answers)
    except ExportError as e:
        return JSONResponse(status_code=400, content={"error": {"code": "export_failed", "message": str(e)}})
    errors, warnings = validate_document(doc, expected_ids=list(turn_counts), expected_count=expect, turn_counts=turn_counts, channels=channels)
    res = doc["results"]
    return JSONResponse(content={
        "valid": not errors, "errors": errors, "warnings": warnings, "summary": summarize(doc),
        "records": len(res), "ok": sum(1 for r in res if r["status"] == "ok"), "error_records": [r["conversation_id"] for r in res if r["status"] == "error"],
        "model": doc["model"],
        "schema_version": doc["schema_version"], "team_name": doc["team_name"],
        "issues": _issues(errors, warnings, [*turn_counts, *(r.get("conversation_id") for r in res if isinstance(r, dict))]),
    })


def _not_found(evaluation_id: int) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": f"Оценка {evaluation_id} не найдена"}},
    )
