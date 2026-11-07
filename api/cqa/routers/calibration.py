"""Calibration API (docs/arch/calibration.md): human gold labels, AI-vs-human agreement, run-to-run stability.

GET  /api/calibration/labels        list labels (+ per-labeler summary)
PUT  /api/calibration/labels        bulk create / update / remove labels        (permission calibration.write)
POST /api/calibration/import        import the teammate's eval/gold.json         (permission calibration.write)
GET  /api/calibration/export        export one labeler in the gold.json format
GET  /api/calibration/agreement     AI vs human per criterion, by language / channel, disagreements
GET  /api/calibration/stability     run-to-run stability (two latest runs, majority share, |Δtotal|, flips)

Errors use the envelope {"error": {"code", "message", ...}}; validation errors are 422 with
errors: [{"field", "message"}]. Responses are built here (not via HTTPException) so the envelope holds in any app.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..calibration import service as svc
from ..db import get_db

router = APIRouter(prefix="/api/calibration", tags=["calibration"])

_PUT_EXAMPLES = {
    "bulk": {
        "summary": "Разметить диалог (значения Да/Нет, шаг шкалы, N/A; итог и заметка необязательны)",
        "value": {
            "labeler": "team",
            "items": [
                {"conversation_id": "p1-ru-029", "criterion_code": "C1", "value": "yes", "split": "train"},
                {"conversation_id": "p1-ru-029", "criterion_code": "C2", "value": "N/A"},
                {"conversation_id": "p1-ru-029", "criterion_code": "C4", "value": 15, "note": "не назван срок зачисления"},
                {"conversation_id": "p1-ru-029", "criterion_code": "_note", "note": "сверено с PDF, стр. 3"},
            ],
        },
    },
    "remove": {
        "summary": "Удалить ошибочную метку",
        "value": {"items": [{"conversation_id": "p1-ru-029", "criterion_code": "C4", "labeler": "team", "remove": True}]},
    },
}

_IMPORT_EXAMPLES = {
    "wrapped": {
        "summary": "Файл eval/gold.json внутри поля doc",
        "value": {
            "labeler": "team",
            "replace": False,
            "dry_run": False,
            "doc": {
                "split": {"train": ["p1-ru-029"], "validation": []},
                "labels": {"p1-ru-029": {"C1": "yes", "C2": "N/A", "C3": "N/A", "C4": 20, "C5": "no", "C6": 7, "C7": "no", "C8": "yes"}},
            },
        },
    },
    "raw": {
        "summary": "Сам файл gold.json как тело запроса (labeler, replace, dry_run — в query)",
        "value": {"split": {"train": ["p1-ru-029"]}, "labels": {"p1-ru-029": {"C1": "yes", "C4": 20}}},
    },
}


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})


def _from_exc(e: svc.CalibrationError) -> JSONResponse:
    return _error(e.status, e.code, e.message, **e.extra)


def _invalid(field: str, message: str) -> JSONResponse:
    return _error(422, "validation_error", message, errors=[{"field": field, "message": message}])


def _conflict(db: Session) -> JSONResponse:
    db.rollback()
    return _error(409, "conflict", "Метки одновременно изменил другой запрос. Обновите данные и повторите.")


def _opt_int(value: Any, field: str) -> tuple[int | None, JSONResponse | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, _invalid(field, f"{field} должен быть целым числом")
    if isinstance(value, int):
        return value, None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()), None
    return None, _invalid(field, f"{field} должен быть целым числом")


def _flag(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _attachment(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename).strip("_") or "gold.json"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/labels")
def get_labels(
    conversation_id: str | None = Query(default=None, description="Только этот диалог"),
    labeler: str | None = Query(default=None, description="Только этот разметчик (team, имя проверяющего)"),
    criterion_code: str | None = Query(default=None, description="C1…C8, total или _note"),
    split: str | None = Query(default=None, description="train | validation | all"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Gold labels as flat rows ({conversation_id, criterion_code, kind, value, labeler, note, split}) plus who labeled what."""
    items = svc.list_labels(db, conversation_id=conversation_id, labeler=labeler, criterion_code=criterion_code, split=split)
    return {"items": items, "total": len(items), "labelers": svc.labelers(db)}


@router.put("/labels", response_model=None)
def put_labels(body: Any = Body(..., openapi_examples=_PUT_EXAMPLES), db: Session = Depends(get_db)) -> Any:
    """Bulk upsert: every item is validated against the checklist (organizer version 1 unless checklist_version_id);
    one invalid item rejects the whole batch with 422. ``remove: true`` deletes a label; ``dry_run: true`` saves nothing."""
    if not isinstance(body, dict):
        return _invalid("body", "Ожидается объект {labeler, items: [{conversation_id, criterion_code, value}]}")
    version_id, err = _opt_int(body.get("checklist_version_id"), "checklist_version_id")
    if err is not None:
        return err
    try:
        return svc.upsert_labels(db, body.get("items"), labeler=body.get("labeler"), checklist_version_id=version_id,
                                 dry_run=body.get("dry_run") is True)
    except svc.CalibrationError as e:
        db.rollback()
        return _from_exc(e)
    except IntegrityError:
        return _conflict(db)


@router.post("/import", response_model=None)
def import_gold(
    body: Any = Body(..., openapi_examples=_IMPORT_EXAMPLES),
    labeler: str | None = Query(default=None, description="Разметчик, если тело запроса — сам файл gold.json"),
    replace: bool | None = Query(default=None, description="Файл становится полным набором меток разметчика"),
    dry_run: bool | None = Query(default=None, description="Только показать, что изменится"),
    checklist_version_id: int | None = Query(default=None, description="Версия для проверки значений (по умолчанию версия 1)"),
    db: Session = Depends(get_db),
) -> Any:
    """Import eval/gold.json: ``{labeler, doc, replace?, dry_run?, checklist_version_id?}`` or the file itself as the body.
    Unknown dialogs are skipped and listed; any invalid value rejects the file with 422."""
    if not isinstance(body, dict):
        return _invalid("body", "Ожидается объект {labeler, doc} или сам файл gold.json")
    wrapped = "doc" in body
    doc = body.get("doc") if wrapped else (body if "labels" in body else None)
    if doc is None:
        return _invalid("doc", "Передайте файл gold.json в поле doc: {split, labels}")
    version_id, err = _opt_int(body.get("checklist_version_id") if wrapped and "checklist_version_id" in body else checklist_version_id,
                               "checklist_version_id")
    if err is not None:
        return err
    lab = body.get("labeler") if wrapped and body.get("labeler") is not None else labeler
    do_replace = _flag(body.get("replace"), False) if wrapped and "replace" in body else bool(replace)
    do_dry = _flag(body.get("dry_run"), False) if wrapped and "dry_run" in body else bool(dry_run)
    try:
        return svc.import_gold(db, doc, lab, replace=do_replace, dry_run=do_dry, checklist_version_id=version_id)
    except svc.CalibrationError as e:
        db.rollback()
        return _from_exc(e)
    except IntegrityError:
        return _conflict(db)


@router.get("/export")
def export_gold(
    labeler: str = Query(default=svc.DEFAULT_LABELER, description="Чью разметку выгрузить"),
    include_notes: bool = Query(default=False, description="Добавить _note и _notes (харнесс считает их критериями)"),
    download: bool = Query(default=False, description="Отдать файлом gold_<labeler>.json"),
    db: Session = Depends(get_db),
) -> Response:
    """One labeler's labels in the gold.json format: ``python -m cqa.eval.harness --gold <file>`` reads it as is."""
    doc = svc.export_gold(db, labeler, include_notes=include_notes)
    body = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    headers: dict[str, str] = {}
    if download:
        name = (labeler or svc.DEFAULT_LABELER).strip() or svc.DEFAULT_LABELER
        headers["Content-Disposition"] = _attachment(f"gold_{name}.json")
        headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return Response(content=body.encode("utf-8"), media_type="application/json; charset=utf-8", headers=headers)


@router.get("/agreement", response_model=None)
def get_agreement(
    labeler: str | None = Query(default=None, description="Эталон (по умолчанию team, иначе самый полный набор)"),
    split: str | None = Query(default=None, description="train | validation | all"),
    checklist_version_id: int | None = Query(default=None, description="По умолчанию версия 1 (чек-лист организаторов)"),
    source: str = Query(default="ai", description="ai — ответы модели; final — после решений по апелляциям"),
    provider: str | None = Query(default=None, description="Только оценки этого провайдера (anthropic, ensemble, local)"),
    db: Session = Depends(get_db),
) -> Any:
    """AI vs human per criterion: n, exact, kappa (binary, N/A as a class), qwk and mae (scale), N/A agreement,
    confusion; overall exact and total MAE; by language and channel; the list of disagreements with explanations."""
    try:
        return svc.agreement(db, labeler=labeler, checklist_version_id=checklist_version_id, split=split, source=source,
                             provider=provider)
    except svc.CalibrationError as e:
        return _from_exc(e)


@router.get("/stability", response_model=None)
def get_stability(
    checklist_version_id: int | None = Query(default=None, description="По умолчанию версия 1 (чек-лист организаторов)"),
    provider: str | None = Query(default=None, description="Сравнивать только прогоны этого провайдера"),
    db: Session = Depends(get_db),
) -> Any:
    """Run-to-run stability over dialogs evaluated at least twice: agreement of the two latest runs per criterion,
    majority share over all runs, mean |Δtotal|, and every flip with its values."""
    try:
        return svc.stability(db, checklist_version_id=checklist_version_id, provider=provider)
    except svc.CalibrationError as e:
        return _from_exc(e)
