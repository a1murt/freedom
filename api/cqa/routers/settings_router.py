from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import FewshotExample, Setting

router = APIRouter(prefix="/api", tags=["settings"])

EDITABLE = {"default_provider", "default_model", "secondary_model", "explanation_language", "pii_masking", "fewshot_from_reviews",
            "ensemble_members", "local_endpoint", "local_model", "judge_effort", "max_parallel_criteria"}


def current(db: Session) -> dict[str, Any]:
    base: dict[str, Any] = {
        "default_provider": settings.default_provider, "default_model": settings.default_model, "secondary_model": settings.secondary_model,
        "explanation_language": settings.explanation_language, "pii_masking": settings.pii_masking, "fewshot_from_reviews": settings.fewshot_from_reviews,
        "ensemble_members": [{"provider": "anthropic", "model": settings.default_model}, {"provider": "anthropic", "model": settings.secondary_model}],
        "local_endpoint": settings.local_endpoint, "local_model": settings.local_model, "judge_effort": settings.judge_effort,
        "max_parallel_criteria": settings.max_parallel_criteria, "cost_rates": settings.cost_rates,
    }
    for row in db.execute(select(Setting)).scalars():
        base[row.key] = row.value
    base["fewshot_count"] = db.execute(select(FewshotExample.id)).all().__len__()
    base["anthropic_configured"] = bool(settings.anthropic_api_key)
    return base


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return current(db)


@router.put("/settings")
def put_settings(body: dict[str, Any], db: Session = Depends(get_db)):
    for k, v in body.items():
        if k not in EDITABLE:
            continue
        row = db.get(Setting, k)
        if row is None:
            db.add(Setting(key=k, value=v))
        else:
            row.value = v
    db.commit()
    return current(db)


@router.get("/health")
def health(db: Session = Depends(get_db)):
    local = bool(settings.local_endpoint or any(r.key == "local_endpoint" and r.value for r in db.execute(select(Setting)).scalars()))
    return {"ok": True, "db": "sqlite" if settings.database_url.startswith("sqlite") else "postgres",
            "providers": {"anthropic": bool(settings.anthropic_api_key), "local": local}, "organizer_api": bool(settings.organizer_api_url)}
