"""GET /api/analytics/summary (see docs/API.md)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..analytics.service import summary
from ..db import get_db

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
def get_summary(
    checklist_version_id: int | None = Query(default=None, description="Restrict to one checklist version"),
    language: str | None = Query(default=None, description="RU | KZ | EN"),
    channel: str | None = Query(default=None, description="agent chat | voice call"),
    dataset_id: int | None = Query(default=None, description="Only dialogs of this imported set"),
    operator: str | None = Query(default=None, description="Only this operator's dialogs"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return summary(db, checklist_version_id, language=language, channel=channel, dataset_id=dataset_id, operator=operator)
