"""Runtime settings (GET/PUT /api/settings) and the shallow health check.

PUT validates the keys that decide cost and where data goes (422 `invalid_settings`, nothing is written when one key is
invalid): models must have a configured price (an unpriced model would book $0 and escape the daily budget), an
ensemble has 1–5 members, `judge_effort` is a Claude effort level, `max_parallel_criteria` is 1–16, and
`local_endpoint` follows cqa.judge.local_endpoint (the environment endpoint, the allowlist, or — off the hosted
instance — a loopback/private address). A value equal to the stored one is accepted as is (the web app sends the whole
form back); stored values that are no longer valid are ignored or refused where they are used.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import FewshotExample, Setting

router = APIRouter(prefix="/api", tags=["settings"])

EDITABLE = {"default_provider", "default_model", "secondary_model", "explanation_language", "pii_masking", "fewshot_from_reviews",
            "ensemble_members", "local_endpoint", "local_model", "judge_effort", "max_parallel_criteria",
            "mascot_enabled", "team_name", "default_theme", "default_checklist_id"}
MAX_ENSEMBLE_MEMBERS = 5
MODEL_NAME_MAX = 200


def _err(errors: list[dict[str, str]], field: str, message: str) -> None:
    errors.append({"field": field, "message": message})


def _claude_model(value: Any, field: str, errors: list[dict[str, str]]) -> None:
    from ..judge.runner import UnknownModelError

    if value is None or (isinstance(value, str) and not value.strip()):
        return  # cleared: the environment default applies
    if not isinstance(value, str):
        _err(errors, field, "Модель задаётся строкой, например «claude-opus-5-5».")
    elif not settings.is_known_model(value):
        _err(errors, field, str(UnknownModelError(value.strip())))


def _validate(key: str, value: Any, errors: list[dict[str, str]]) -> None:
    from ..judge.local_endpoint import check_endpoint
    from ..judge.runner import EFFORTS, KNOWN_PROVIDERS, LOCAL_PROVIDERS, MAX_PARALLEL_CRITERIA

    if key == "default_provider":
        if not isinstance(value, str) or value.strip().lower() not in KNOWN_PROVIDERS:
            _err(errors, key, f"Провайдер по умолчанию: один из {', '.join(KNOWN_PROVIDERS)}.")
    elif key in ("default_model", "secondary_model"):
        _claude_model(value, key, errors)
    elif key == "ensemble_members":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_ENSEMBLE_MEMBERS:
            _err(errors, key, f"Ансамбль — список из 1–{MAX_ENSEMBLE_MEMBERS} участников вида {{\"provider\": \"anthropic\", \"model\": \"…\"}}.")
            return
        for i, m in enumerate(value):
            path = f"{key}[{i}]"
            if not isinstance(m, dict):
                _err(errors, path, "Участник ансамбля — объект {provider, model}.")
                continue
            provider = str(m.get("provider") or "anthropic").strip()
            base, _, tail = provider.partition(":")
            if base.lower() not in KNOWN_PROVIDERS or base.lower() == "ensemble":
                _err(errors, f"{path}.provider", f"Провайдер «{provider}» не поддерживается в ансамбле.")
                continue
            model = m.get("model") if m.get("model") not in (None, "") else (tail or None)
            if base.lower() == "anthropic":
                _claude_model(model, f"{path}.model", errors)
            elif model is not None and (not isinstance(model, str) or len(model) > MODEL_NAME_MAX) and base.lower() in LOCAL_PROVIDERS:
                _err(errors, f"{path}.model", "Имя локальной модели — строка не длиннее 200 символов.")
    elif key == "judge_effort":
        if not isinstance(value, str) or value.strip().lower() not in EFFORTS:
            _err(errors, key, f"Уровень effort: один из {', '.join(EFFORTS)}.")
    elif key == "max_parallel_criteria":
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PARALLEL_CRITERIA:
            _err(errors, key, f"Параллельных запросов на диалог: целое число от 1 до {MAX_PARALLEL_CRITERIA}.")
    elif key == "local_endpoint":
        if value is None or (isinstance(value, str) and not value.strip()):
            return
        if not isinstance(value, str):
            _err(errors, key, "Адрес локальной модели задаётся строкой.")
            return
        ok, reason = check_endpoint(value)
        if not ok:
            _err(errors, key, reason)
    elif key == "local_model":
        if value is not None and (not isinstance(value, str) or len(value) > MODEL_NAME_MAX):
            _err(errors, key, "Имя локальной модели — строка не длиннее 200 символов.")


def current(db: Session) -> dict[str, Any]:
    base: dict[str, Any] = {
        "default_provider": settings.default_provider, "default_model": settings.default_model, "secondary_model": settings.secondary_model,
        "explanation_language": settings.explanation_language, "pii_masking": settings.pii_masking, "fewshot_from_reviews": settings.fewshot_from_reviews,
        "ensemble_members": [{"provider": "anthropic", "model": settings.default_model}, {"provider": "anthropic", "model": settings.default_model}, {"provider": "anthropic", "model": settings.secondary_model}],
        "local_endpoint": settings.local_endpoint, "local_model": settings.local_model, "judge_effort": settings.judge_effort,
        "max_parallel_criteria": settings.max_parallel_criteria, "cost_rates": settings.cost_rates,
        "mascot_enabled": False, "default_theme": "light", "team_name": settings.team_name,
        "default_checklist_id": settings.default_checklist_id,
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
    errors: list[dict[str, str]] = []
    changes: list[tuple[str, Any]] = []
    for k, v in body.items():
        if k not in EDITABLE:
            continue
        row = db.get(Setting, k)
        if row is not None and row.value == v:
            continue  # unchanged (the web app sends the whole form back)
        _validate(k, v, errors)
        changes.append((k, v))
    if errors:
        first = errors[0]
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_settings", "errors": errors,
            "message": f"Настройки не сохранены: {first['field']} — {first['message']}"}})
    for k, v in changes:
        row = db.get(Setting, k)
        if row is None:
            db.add(Setting(key=k, value=v))
        else:
            row.value = v
    db.commit()
    return current(db)


@router.get("/health")
def health(db: Session = Depends(get_db)):
    from ..judge.local_endpoint import effective_endpoint

    row = db.get(Setting, "local_endpoint")
    local = bool(effective_endpoint(row.value if row is not None and isinstance(row.value, str) else None))
    return {"ok": True, "db": "sqlite" if settings.database_url.startswith("sqlite") else "postgres",
            "providers": {"anthropic": bool(settings.anthropic_api_key), "local": local}, "organizer_api": bool(settings.organizer_api_url)}
