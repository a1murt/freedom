from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..checklist import service as svc
from ..checklist.compiler import compile_spec
from ..db import get_db
from ..models import Checklist, ChecklistVersion

router = APIRouter(prefix="/api/checklists", tags=["checklists"])


class ChecklistBody(BaseModel):
    name: str | None = None
    description: str | None = None
    scoring_rules: dict[str, Any] | None = None
    sections: list[dict[str, Any]] | None = None
    criteria: list[dict[str, Any]]
    change_note: str | None = None
    compile_specs: bool = True


@router.get("")
def list_checklists(db: Session = Depends(get_db)):
    cls = db.execute(select(Checklist).options(selectinload(Checklist.versions).selectinload(ChecklistVersion.criteria))).scalars().all()
    return [svc.serialize_checklist(c) for c in cls]


@router.post("")
def create_checklist(body: ChecklistBody, db: Session = Depends(get_db)):
    ver = svc.create_checklist(db, body.model_dump(exclude_none=True), change_note=body.change_note or "v1", compile_missing=body.compile_specs)
    return svc.serialize_version(ver)


@router.get("/active")
def get_active(db: Session = Depends(get_db)):
    v = svc.active_version(db)
    if v is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "no checklist"})
    return svc.serialize_version(v)


@router.get("/{checklist_id}")
def get_checklist(checklist_id: int, db: Session = Depends(get_db)):
    cl = db.execute(select(Checklist).where(Checklist.id == checklist_id).options(selectinload(Checklist.versions).selectinload(ChecklistVersion.criteria))).scalar_one_or_none()
    if cl is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "checklist not found"})
    return svc.serialize_checklist(cl)


@router.post("/{checklist_id}/versions")
def create_version(checklist_id: int, body: ChecklistBody, db: Session = Depends(get_db)):
    try:
        ver = svc.create_version(db, checklist_id, body.model_dump(exclude_none=True), change_note=body.change_note, compile_missing=body.compile_specs)
    except KeyError:
        raise HTTPException(404, detail={"code": "not_found", "message": "checklist not found"})
    return svc.serialize_version(ver)


@router.get("/{checklist_id}/versions/{version_id}")
def get_version(checklist_id: int, version_id: int, db: Session = Depends(get_db)):
    v = svc.get_version(db, version_id)
    if v is None or v.checklist_id != checklist_id:
        raise HTTPException(404, detail={"code": "not_found", "message": "version not found"})
    return svc.serialize_version(v)


@router.post("/{checklist_id}/versions/{version_id}/activate")
def activate_version(checklist_id: int, version_id: int, db: Session = Depends(get_db)):
    v = svc.get_version(db, version_id)
    if v is None or v.checklist_id != checklist_id:
        raise HTTPException(404, detail={"code": "not_found", "message": "version not found"})
    svc.activate(db, v)
    return svc.serialize_version(v)


@router.get("/{checklist_id}/versions/{a}/diff/{b}")
def diff(checklist_id: int, a: int, b: int, db: Session = Depends(get_db)):
    va, vb = svc.get_version(db, a), svc.get_version(db, b)
    if va is None or vb is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "version not found"})
    return svc.diff_versions(va, vb)


@router.post("/{checklist_id}/versions/{version_id}/judge-spec/{code}/regenerate")
def regenerate_spec(checklist_id: int, version_id: int, code: str, db: Session = Depends(get_db)):
    v = svc.get_version(db, version_id)
    if v is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "version not found"})
    c = next((c for c in v.criteria if c.code == code), None)
    if c is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "criterion not found"})
    c.judge_spec = compile_spec(svc._criterion_dict(c))
    db.commit()
    return svc._criterion_dict(c)
