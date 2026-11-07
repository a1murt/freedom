"""Checklists, versions, diff, judge specs, portable export and the single-criterion trial (docs/arch/checklists.md).

Errors use the API envelope {"error": {"code", "message", ...}}; validation failures are 422 with
`errors: [{"field": "criteria[2].weight", "message": "..."}]` (and `warnings`). Permissions are enforced by the
security middleware: GET → read; POST → checklists.write.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..checklist import service as svc
from ..checklist.compiler import compile_spec
from ..checklist.validation import ChecklistValidationError
from ..db import get_db
from ..models import Checklist, ChecklistVersion

router = APIRouter(prefix="/api/checklists", tags=["checklists"])


class ChecklistBody(BaseModel):
    """Body of POST /api/checklists and POST /api/checklists/{id}/versions. Shapes are checked by
    cqa.checklist.validation (422 invalid_checklist), not by pydantic, so every problem is reported in one answer."""

    model_config = ConfigDict(extra="ignore")

    name: Any = Field(default=None, description="Checklist name (new checklist; renames on a new version)")
    description: Any = None
    scoring_rules: Any = Field(default=None, description="total_scale, rounding, critical, export_ids; omitted → inherited")
    sections: Any = Field(default=None, description="[{code, title}]; omitted → inherited; missing referenced sections are created")
    criteria: Any = Field(default=None, description="[Criterion without id]; judge_spec optional (reused or compiled when missing)")
    change_note: Any = None
    compile_specs: bool = True
    activate: bool = Field(default=True, description="false = save as a draft and keep the current active version")
    base_version_id: Any = Field(default=None, description="The version the editor started from; 409 version_conflict when a newer one exists")


class ChecklistImportBody(ChecklistBody):
    """The file of GET /api/checklists/{id}/versions/{vid}/export (or any checklist JSON with criteria)."""

    format: Any = Field(default=None, description=f"{svc.EXPORT_FORMAT} when the file comes from the export endpoint")
    exported_from: Any = None


class ImpactBody(ChecklistBody):
    """Draft checklist (same shape as a new version) plus the scope of the preview."""

    conversation_ids: Any = Field(default=None, description="Only these dialogs (default: every dialog evaluated with the base version)")
    dataset_id: Any = None
    basis: Any = Field(default="final", description="final (reviewer decisions applied) | ai (judge answers only)")
    limit: Any = 500


class CriterionTestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    criterion: Any = Field(default=None, description="Criterion JSON as in a checklist version (saved or not)")
    conversation_id: Any = None
    provider: Any = Field(default=None, description="anthropic | local | fake | ensemble (default: settings)")
    model: Any = None
    compile_spec: bool = Field(default=True, description="Compile a judge_spec with the LLM when the criterion has none")


def _note(value: Any, default: str | None = None) -> str | None:
    return str(value) if value not in (None, "") else default


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})


def _invalid(e: ChecklistValidationError) -> JSONResponse:
    return _error(422, **e.to_error())


def _checklist_not_found(checklist_id: int) -> JSONResponse:
    return _error(404, "not_found", f"Чек-лист {checklist_id} не найден.")


def _version_not_found(version_id: int) -> JSONResponse:
    return _error(404, "not_found", f"Версия чек-листа {version_id} не найдена.")


def _version_conflict(e: svc.VersionConflict) -> JSONResponse:
    return _error(409, "version_conflict",
                  f"Пока вы редактировали, сохранена версия {e.latest_no}. Откройте её и перенесите свои правки, "
                  "чтобы не перезаписать чужие изменения.", latest_version_id=e.latest_id, latest_version_no=e.latest_no)


@router.get("")
def list_checklists(db: Session = Depends(get_db)):
    cls = db.execute(select(Checklist).options(selectinload(Checklist.versions).selectinload(ChecklistVersion.criteria))).scalars().all()
    return [svc.serialize_checklist(c) for c in cls]


@router.post("")
def create_checklist(body: ChecklistBody, db: Session = Depends(get_db)):
    warnings: list[dict[str, str]] = []
    try:
        ver = svc.create_checklist(db, body.model_dump(exclude_none=True), change_note=_note(body.change_note, "v1"), compile_missing=body.compile_specs,
                                   activate=body.activate, warnings_out=warnings)
    except ChecklistValidationError as e:
        return _invalid(e)
    return svc.serialize_version(ver) | {"warnings": warnings}


@router.post("/import")
def import_checklist(body: ChecklistImportBody, db: Session = Depends(get_db)):
    """New checklist from a portable export; judge specs in the file are kept, missing ones are compiled."""
    if body.format is not None and body.format != svc.EXPORT_FORMAT:
        return _error(422, "invalid_checklist", f"Неизвестный формат файла «{body.format}»: ожидается {svc.EXPORT_FORMAT}.",
                      errors=[{"field": "format", "message": f"Ожидается {svc.EXPORT_FORMAT} — файл из «Экспорт JSON» версии чек-листа."}],
                      warnings=[])
    payload = body.model_dump(exclude_none=True, exclude={"format", "exported_from"})
    note = _note(body.change_note) or svc.import_change_note(body.model_dump())
    warnings: list[dict[str, str]] = []
    try:
        ver = svc.create_checklist(db, payload, change_note=note, compile_missing=body.compile_specs, activate=body.activate, warnings_out=warnings)
    except ChecklistValidationError as e:
        return _invalid(e)
    return svc.serialize_version(ver) | {"warnings": warnings}


@router.get("/active")
def get_active(db: Session = Depends(get_db)):
    v = svc.active_version(db)
    if v is None:
        return _error(404, "not_found", "Чек-лист ещё не создан.")
    return svc.serialize_version(v)


@router.post("/{checklist_id}/versions/{version_id}/impact")
def version_impact(checklist_id: int, version_id: int, body: ImpactBody, db: Session = Depends(get_db)):
    """What-if over stored answers: new totals per dialog for a draft of this checklist, without model calls or writes."""
    from ..checklist.impact import impact

    base = svc.get_version(db, version_id)
    if base is None or base.checklist_id != checklist_id:
        return _version_not_found(version_id)
    draft = body.model_dump(exclude={"conversation_ids", "dataset_id", "basis", "limit", "compile_specs", "activate", "change_note"})
    ids = body.conversation_ids
    if ids is not None and not (isinstance(ids, list) and all(isinstance(x, (str, int)) for x in ids)):
        return _error(422, "validation_error", "conversation_ids: список идентификаторов диалогов",
                      errors=[{"field": "conversation_ids", "message": "список идентификаторов диалогов"}])
    try:
        dataset_id = int(body.dataset_id) if body.dataset_id not in (None, "") else None
        limit = max(0, min(int(body.limit if body.limit not in (None, "") else 500), 5000))
    except (TypeError, ValueError):
        return _error(422, "validation_error", "dataset_id и limit — целые числа",
                      errors=[{"field": "dataset_id", "message": "целое число"}])
    try:
        return impact(db, base, draft, conversation_ids=[str(x) for x in ids] if ids is not None else None,
                      dataset_id=dataset_id, basis=str(body.basis or "final"), limit=limit)
    except ChecklistValidationError as e:
        return _invalid(e)


@router.post("/test-criterion")
async def criterion_test(body: CriterionTestBody, db: Session = Depends(get_db)):
    """Judge one criterion (saved or draft) on one conversation; no evaluation is stored."""
    try:
        return await svc.run_criterion_test(db, criterion=body.criterion, conversation_id=body.conversation_id, provider=body.provider,
                                            model=body.model, compile_spec=body.compile_spec)
    except ChecklistValidationError as e:
        return _invalid(e)
    except svc.CriterionTestError as e:
        return _error(e.status, e.code, e.message, **e.extra)


@router.get("/{checklist_id}")
def get_checklist(checklist_id: int, db: Session = Depends(get_db)):
    cl = db.execute(select(Checklist).where(Checklist.id == checklist_id).options(selectinload(Checklist.versions).selectinload(ChecklistVersion.criteria))).scalar_one_or_none()
    if cl is None:
        return _checklist_not_found(checklist_id)
    return svc.serialize_checklist(cl)


@router.post("/{checklist_id}/versions")
def create_version(checklist_id: int, body: ChecklistBody, db: Session = Depends(get_db)):
    warnings: list[dict[str, str]] = []
    base_id: int | None = None
    if body.base_version_id not in (None, ""):  # optimistic concurrency (CHK-12): two editors, the second save is stale
        try:
            base_id = int(body.base_version_id)
        except (TypeError, ValueError):
            return _error(422, "validation_error", "base_version_id — целое число", errors=[{"field": "base_version_id", "message": "целое число"}])
    try:
        # the service checks the base before compiling the judge specs and again atomically with the insert, so two
        # concurrent saves from the same base give one new version and one 409
        ver = svc.create_version(db, checklist_id, body.model_dump(exclude_none=True, exclude={"base_version_id"}), change_note=_note(body.change_note), compile_missing=body.compile_specs,
                                 activate=body.activate, warnings_out=warnings, base_version_id=base_id)
    except KeyError:
        return _checklist_not_found(checklist_id)
    except ChecklistValidationError as e:
        return _invalid(e)
    except svc.VersionConflict as e:
        return _version_conflict(e)
    return svc.serialize_version(ver) | {"warnings": warnings}


@router.get("/{checklist_id}/versions/{version_id}")
def get_version(checklist_id: int, version_id: int, db: Session = Depends(get_db)):
    v = svc.get_version(db, version_id)
    if v is None or v.checklist_id != checklist_id:
        return _version_not_found(version_id)
    return svc.serialize_version(v)


@router.get("/{checklist_id}/versions/{version_id}/export")
def export_version(checklist_id: int, version_id: int, download: bool = Query(default=False, description="Send as an attachment"),
                   db: Session = Depends(get_db)):
    """Portable JSON (criteria with judge_spec) accepted as is by POST /api/checklists."""
    v = svc.get_version(db, version_id)
    if v is None or v.checklist_id != checklist_id:
        return _version_not_found(version_id)
    headers = {"Content-Disposition": f'attachment; filename="checklist-{checklist_id}-v{v.version_no}.json"'} if download else None
    return JSONResponse(content=svc.export_version(v), headers=headers)


@router.post("/{checklist_id}/versions/{version_id}/activate")
def activate_version(checklist_id: int, version_id: int, db: Session = Depends(get_db)):
    v = svc.get_version(db, version_id)
    if v is None or v.checklist_id != checklist_id:
        return _version_not_found(version_id)
    svc.activate(db, v)
    return svc.serialize_version(v)


@router.get("/{checklist_id}/versions/{a}/diff/{b}")
def diff(checklist_id: int, a: int, b: int, db: Session = Depends(get_db)):
    va, vb = svc.get_version(db, a), svc.get_version(db, b)
    if va is None or vb is None:
        return _version_not_found(a if va is None else b)
    out = svc.diff_versions(va, vb)
    # old → new per changed field (CHK-13), so the diff view can show "вес 5 → 15" without fetching both versions
    ca = {c.code: svc._criterion_dict(c) for c in va.criteria}
    cb = {c.code: svc._criterion_dict(c) for c in vb.criteria}
    for ch in out.get("changed", []):
        ch["values"] = {f: {"old": ca.get(ch["code"], {}).get(f), "new": cb.get(ch["code"], {}).get(f)} for f in ch.get("fields", [])
                        if f != "judge_spec"}
    out["added_criteria"] = [{k: cb[c].get(k) for k in ("code", "title", "weight", "answer_type")} for c in out.get("added", []) if c in cb]
    out["removed_criteria"] = [{k: ca[c].get(k) for k in ("code", "title", "weight", "answer_type")} for c in out.get("removed", []) if c in ca]
    out["from_version_no"], out["to_version_no"] = va.version_no, vb.version_no
    out["weights"] = {"from": svc.weights_summary(va.criteria), "to": svc.weights_summary(vb.criteria)}
    return out


@router.post("/{checklist_id}/versions/{version_id}/judge-spec/{code}/regenerate")
def regenerate_spec(checklist_id: int, version_id: int, code: str, force: bool = Query(default=False), db: Session = Depends(get_db)):
    v = svc.get_version(db, version_id)
    if v is None:
        return _version_not_found(version_id)
    c = next((c for c in v.criteria if c.code == code), None)
    if c is None:
        return _error(404, "not_found", f"Критерий «{code}» не найден в версии {version_id}.")
    # CHK-15: a version that already has evaluations is a record of how they were judged (results.json is pinned to the
    # organizer v1); rewriting its judge spec in place would make those verdicts unreproducible. Save a new version instead.
    from ..models import Evaluation

    used = db.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.checklist_version_id == version_id,
                                                                     Evaluation.status == "completed")) or 0
    if used and not force:
        return _error(409, "version_in_use",
                      f"По версии {v.version_no} уже есть {used} оценок: спецификацию критерия нельзя пересобрать на месте, "
                      "иначе прошлые оценки станут невоспроизводимыми. Сохраните новую версию чек-листа (критерий без judge_spec "
                      "соберётся заново) или повторите с force=true.", evaluations=int(used))
    c.judge_spec = compile_spec(svc._criterion_dict(c))
    db.commit()
    return svc._criterion_dict(c)
