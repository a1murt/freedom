"""Gross violations list and operator directory (organizer live asks: gross errors with explanations; operator appeals)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..analytics.violations import operators, violations
from ..db import get_db

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/violations")
def list_violations(
    checklist_version_id: int | None = Query(default=None),
    type: str | None = Query(default=None, description="insult | paid_bypass | disclosure | other"),
    language: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    operator: str | None = Query(default=None),
    include_reviewed_out: bool = Query(default=False, description="also list AI-flagged cases a reviewer overturned"),
    db: Session = Depends(get_db),
):
    return violations(db, checklist_version_id=checklist_version_id, kind=type, language=language, channel=channel, operator=operator,
                      include_reviewed_out=include_reviewed_out)


@router.get("/operators")
def list_operators(checklist_version_id: int | None = Query(default=None), db: Session = Depends(get_db)):
    return operators(db, checklist_version_id=checklist_version_id)
