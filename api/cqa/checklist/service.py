"""Checklist versions: seed, create, diff, activate, serialize."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Checklist, ChecklistVersion, Criterion
from .compiler import compile_spec, judge_relevant_signature

CRITERION_FIELDS = ("code", "section", "title", "instruction", "answer_type", "options", "max_points", "weight", "na_allowed",
                    "na_condition", "is_critical", "critical_value", "channel_overrides")
REJUDGE_FIELDS = ("title", "instruction", "answer_type", "options", "na_allowed", "na_condition", "channel_overrides", "is_critical", "critical_value", "judge_spec")


def _normalize_criterion(c: dict[str, Any], order: int) -> dict[str, Any]:
    out = {k: c.get(k) for k in CRITERION_FIELDS}
    out["order"] = c.get("order", order)
    out["options"] = out.get("options") or ([{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}] if out.get("answer_type", "binary") == "binary" else [])
    out["answer_type"] = out.get("answer_type") or "binary"
    if out.get("max_points") is None:
        out["max_points"] = max((float(o.get("points", 0)) for o in out["options"]), default=1.0)
    out["weight"] = float(out.get("weight") or 0)
    out["na_allowed"] = bool(out.get("na_allowed"))
    out["is_critical"] = bool(out.get("is_critical"))
    out["channel_overrides"] = out.get("channel_overrides") or {}
    out["judge_spec"] = c.get("judge_spec")
    return out


def ensure_seed(db: Session) -> None:
    if db.execute(select(Checklist.id)).first():
        return
    seed = json.loads((settings.seeds_dir / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
    specs_path = settings.seeds_dir / "judge_specs_v1.json"
    specs = json.loads(specs_path.read_text(encoding="utf-8")) if specs_path.exists() else {}
    for c in seed["criteria"]:
        if c["code"] in specs and not c.get("judge_spec"):
            c["judge_spec"] = specs[c["code"]]
    create_checklist(db, seed, change_note=seed.get("version_note") or "v1 (seed)", compile_missing=False)


def create_checklist(db: Session, payload: dict[str, Any], change_note: str | None = None, compile_missing: bool = True) -> ChecklistVersion:
    cl = Checklist(name=payload.get("name") or "Checklist", description=payload.get("description"))
    db.add(cl)
    db.flush()
    return _create_version(db, cl, payload, version_no=1, change_note=change_note or "v1", previous=None, compile_missing=compile_missing)


def create_version(db: Session, checklist_id: int, payload: dict[str, Any], change_note: str | None, compile_missing: bool = True) -> ChecklistVersion:
    cl = db.get(Checklist, checklist_id)
    if cl is None:
        raise KeyError("checklist not found")
    prev = db.execute(select(ChecklistVersion).where(ChecklistVersion.checklist_id == checklist_id).order_by(ChecklistVersion.version_no.desc())
                      .options(selectinload(ChecklistVersion.criteria))).scalars().first()
    version_no = (prev.version_no + 1) if prev else 1
    if payload.get("name"):
        cl.name = payload["name"]
    if payload.get("description") is not None:
        cl.description = payload["description"]
    return _create_version(db, cl, payload, version_no=version_no, change_note=change_note, previous=prev, compile_missing=compile_missing)


def _create_version(db: Session, cl: Checklist, payload: dict[str, Any], *, version_no: int, change_note: str | None,
                    previous: ChecklistVersion | None, compile_missing: bool) -> ChecklistVersion:
    for v in cl.versions:
        v.is_active = False
    ver = ChecklistVersion(checklist_id=cl.id, version_no=version_no, is_active=True, change_note=change_note,
                           scoring_rules=payload.get("scoring_rules") or (previous.scoring_rules if previous else {}),
                           sections=payload.get("sections") or (previous.sections if previous else []),
                           created_from_version_id=(previous.id if previous else None))
    db.add(ver)
    db.flush()
    prev_by_code = {c.code: c for c in previous.criteria} if previous else {}
    for i, raw in enumerate(payload.get("criteria") or []):
        c = _normalize_criterion(raw, i)
        spec = c.get("judge_spec")
        if not spec and c["code"] in prev_by_code:
            pc = prev_by_code[c["code"]]
            if judge_relevant_signature(_criterion_dict(pc)) == judge_relevant_signature(c):
                spec = pc.judge_spec
        if not spec and compile_missing:
            spec = compile_spec(c)
        db.add(Criterion(checklist_version_id=ver.id, code=c["code"], order=c["order"], section=c.get("section"), title=c["title"],
                         instruction=c.get("instruction") or "", answer_type=c["answer_type"], options=c["options"], max_points=float(c["max_points"]),
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c.get("na_condition"), is_critical=c["is_critical"],
                         critical_value=c.get("critical_value") or ("yes" if c["is_critical"] else None), channel_overrides=c["channel_overrides"], judge_spec=spec))
    db.commit()
    db.refresh(ver)
    return ver


def _criterion_dict(c: Criterion) -> dict[str, Any]:
    return {"id": c.id, "code": c.code, "order": c.order, "section": c.section, "title": c.title, "instruction": c.instruction,
            "answer_type": c.answer_type, "options": c.options, "max_points": c.max_points, "weight": c.weight, "na_allowed": c.na_allowed,
            "na_condition": c.na_condition, "is_critical": c.is_critical, "critical_value": c.critical_value,
            "channel_overrides": c.channel_overrides or {}, "judge_spec": c.judge_spec}


def serialize_version(v: ChecklistVersion) -> dict[str, Any]:
    return {"id": v.id, "checklist_id": v.checklist_id, "version_no": v.version_no, "is_active": v.is_active, "change_note": v.change_note,
            "scoring_rules": v.scoring_rules, "sections": v.sections, "created_at": v.created_at.isoformat() if v.created_at else None,
            "created_from_version_id": v.created_from_version_id, "criteria": [_criterion_dict(c) for c in sorted(v.criteria, key=lambda c: c.order)]}


def serialize_checklist(cl: Checklist) -> dict[str, Any]:
    active = next((v for v in cl.versions if v.is_active), None) or (cl.versions[-1] if cl.versions else None)
    return {"id": cl.id, "name": cl.name, "description": cl.description, "created_at": cl.created_at.isoformat() if cl.created_at else None,
            "active_version": serialize_version(active) if active else None,
            "versions": [{"id": v.id, "version_no": v.version_no, "created_at": v.created_at.isoformat() if v.created_at else None,
                          "change_note": v.change_note, "is_active": v.is_active} for v in cl.versions]}


def activate(db: Session, version: ChecklistVersion) -> None:
    for v in version.checklist.versions:
        v.is_active = v.id == version.id
    db.commit()


def diff_versions(a: ChecklistVersion, b: ChecklistVersion) -> dict[str, Any]:
    ca = {c.code: _criterion_dict(c) for c in a.criteria}
    cb = {c.code: _criterion_dict(c) for c in b.criteria}
    added = [code for code in cb if code not in ca]
    removed = [code for code in ca if code not in cb]
    changed = []
    for code in cb:
        if code not in ca:
            continue
        fields = [f for f in ("section", "title", "instruction", "answer_type", "options", "max_points", "weight", "na_allowed", "na_condition",
                              "is_critical", "critical_value", "channel_overrides", "judge_spec", "order") if ca[code].get(f) != cb[code].get(f)]
        if fields:
            changed.append({"code": code, "fields": fields, "requires_rejudge": any(f in REJUDGE_FIELDS for f in fields)})
    rules_changed = a.scoring_rules != b.scoring_rules
    return {"from_version_id": a.id, "to_version_id": b.id, "added": added, "removed": removed, "changed": changed,
            "scoring_rules_changed": rules_changed,
            "rejudge_codes": added + [c["code"] for c in changed if c["requires_rejudge"]]}


def get_version(db: Session, version_id: int) -> ChecklistVersion | None:
    return db.execute(select(ChecklistVersion).where(ChecklistVersion.id == version_id).options(selectinload(ChecklistVersion.criteria))).scalar_one_or_none()


def active_version(db: Session) -> ChecklistVersion | None:
    v = db.execute(select(ChecklistVersion).where(ChecklistVersion.is_active.is_(True)).options(selectinload(ChecklistVersion.criteria))).scalars().first()
    if v is None:
        v = db.execute(select(ChecklistVersion).order_by(ChecklistVersion.id.desc()).options(selectinload(ChecklistVersion.criteria))).scalars().first()
    return v
