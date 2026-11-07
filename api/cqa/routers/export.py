"""GET /api/export/evaluations.csv and GET /api/export/evaluations/{id}.pdf (see docs/API.md)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..export.csv_export import evaluations_csv
from ..export.pdf_export import EvaluationNotFound, evaluation_pdf
from ..models import Evaluation

router = APIRouter(prefix="/api/export", tags=["export"])


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
        headers={"Content-Disposition": f'attachment; filename="evaluations{suffix}.csv"'},
    )


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
        headers={"Content-Disposition": f'inline; filename="evaluation_{evaluation_id}.pdf"'},
    )


@router.get("/results.json")
def export_results_json(
    group_id: str | None = Query(default=None, description="Dataset group_id, e.g. DEV or the control group id"),
    conversation_ids: str | None = Query(default=None, description="Comma-separated ids (overrides group_id)"),
    checklist_version_id: int | None = Query(default=None, description="Default: the organizer checklist (version 1)"),
    team_name: str | None = Query(default=None),
    expect: int | None = Query(default=None, description="Expected record count (prod: 10)"),
    download: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> Response:
    """Organizer results file (JSON 1.0, docs/ORGANIZER_UPDATES.md §1) with validation report in headers."""
    import json
    import os

    from ..export.results_json import ExportError, from_db, validate_document

    ids = [x.strip() for x in conversation_ids.split(",") if x.strip()] if conversation_ids else None
    team = team_name or os.environ.get("TEAM_NAME") or "Я Тебя Могну"
    try:
        doc, turn_counts, channels = from_db(db, group_id=group_id, conversation_ids=ids, checklist_version_id=checklist_version_id, team_name=team)
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
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Dry run of the results export: counts, errors and warnings, without the file body."""
    from ..export.results_json import ExportError, from_db, summarize, validate_document

    ids = [x.strip() for x in conversation_ids.split(",") if x.strip()] if conversation_ids else None
    try:
        doc, turn_counts, channels = from_db(db, group_id=group_id, conversation_ids=ids, checklist_version_id=checklist_version_id, team_name="validation")
    except ExportError as e:
        return JSONResponse(status_code=400, content={"error": {"code": "export_failed", "message": str(e)}})
    errors, warnings = validate_document(doc, expected_ids=list(turn_counts), expected_count=expect, turn_counts=turn_counts, channels=channels)
    res = doc["results"]
    return JSONResponse(content={
        "valid": not errors, "errors": errors, "warnings": warnings, "summary": summarize(doc),
        "records": len(res), "ok": sum(1 for r in res if r["status"] == "ok"), "error_records": [r["conversation_id"] for r in res if r["status"] == "error"],
        "model": doc["model"],
    })


def _not_found(evaluation_id: int) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": f"evaluation {evaluation_id} not found"}},
    )
