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


def _not_found(evaluation_id: int) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": f"evaluation {evaluation_id} not found"}},
    )
