"""GET /api/analytics/summary and /recording-gaps (see docs/API.md)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..analytics.recording import recording_gaps
from ..analytics.service import BASES, BASIS_MESSAGE, summary
from ..db import get_db

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
def get_summary(
    checklist_version_id: int | None = Query(default=None, description="Restrict to one checklist version"),
    language: str | None = Query(default=None, description="RU | KZ | EN"),
    channel: str | None = Query(default=None, description="agent chat | voice call"),
    dataset_id: int | None = Query(default=None, description="Only dialogs of this imported set"),
    operator: str | None = Query(default=None, description="Only this operator's dialogs"),
    basis: str = Query(default="ai", description="Answers of the heatmap and N/A rates: ai = the judge's (default); final = reviewer decisions applied"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if basis not in BASES:
        raise HTTPException(422, detail={"code": "validation_error", "message": BASIS_MESSAGE,
                                         "errors": [{"field": "basis", "message": BASIS_MESSAGE}]})
    return summary(db, checklist_version_id, language=language, channel=channel, dataset_id=dataset_id, operator=operator, basis=basis)


@router.get("/recording-gaps")
def get_recording_gaps(
    checklist_version_id: int | None = Query(default=None, description="Evaluations of this checklist version (default: any)"),
    dataset_id: int | None = Query(default=None, description="Only calls of this imported set"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Calls whose audio recording started after the conversation: seconds lost, whose dialogs, criteria decided by rule."""
    return recording_gaps(db, checklist_version_id=checklist_version_id, dataset_id=dataset_id)
