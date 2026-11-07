"""Checklist versions: seed, validate, create, diff, activate, serialize, export, and the single-criterion trial
(docs/arch/checklists.md)."""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, object_session, selectinload

from ..config import settings
from ..models import Checklist, ChecklistVersion, Conversation, Criterion, Evaluation, Setting
from . import compiler
from .compiler import judge_relevant_signature
from .validation import (ChecklistValidationError, export_ids_for, field_error, prepare_checklist_payload, prepare_criterion_payload,
                         summary_message)

log = logging.getLogger("cqa.checklist")

CRITERION_FIELDS = ("code", "section", "title", "instruction", "answer_type", "options", "max_points", "weight", "na_allowed",
                    "na_condition", "is_critical", "critical_value", "channel_overrides")
REJUDGE_FIELDS = ("title", "instruction", "answer_type", "options", "na_allowed", "na_condition", "channel_overrides", "is_critical", "critical_value", "judge_spec")

EXPORT_FORMAT = "cqa.checklist/1"
TEST_PROVIDERS = ("anthropic", "local", "fake", "ensemble")
FREE_PROVIDERS = ("local", "fake")
# One judge call on a typical chat with Opus (≈6–8k input tokens incl. the cached transcript, ≈1k output); ×members for ensemble.
TEST_CRITERION_ESTIMATE_USD = 0.05
_trials_running = 0
_trials_lock = threading.Lock()


class CriterionTestError(Exception):
    """A failed single-criterion trial; the router answers {"error": {"code", "message", **extra}} with `status`."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


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


def sync_seed_into_organizer_version(db: Session) -> list[str]:
    """Keep the organizer checklist (version 1 of the seeded checklist) in step with the repo seeds.

    Only judge-facing guidance is synced (judge_spec and channel_overrides, replaced as a whole); weights, options and
    the base N/A rules of version 1 are never touched. Channel overrides may carry a per-channel N/A, so an organizer
    clarification can add or remove one (the C1 call N/A was removed on 2026-11-08). This lets judge-spec improvements
    and organizer clarifications reach the hosted instance on deploy. Disable with SEED_SYNC=0.
    """
    import os

    if os.environ.get("SEED_SYNC", "1") == "0":
        return []
    seed = json.loads((settings.seeds_dir / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
    specs_path = settings.seeds_dir / "judge_specs_v1.json"
    specs = json.loads(specs_path.read_text(encoding="utf-8")) if specs_path.exists() else {}
    cl = db.execute(select(Checklist).where(Checklist.name == seed.get("name")).order_by(Checklist.id)).scalars().first()
    if cl is None:
        return []
    v1 = db.execute(select(ChecklistVersion).where(ChecklistVersion.checklist_id == cl.id, ChecklistVersion.version_no == 1)
                    .options(selectinload(ChecklistVersion.criteria))).scalar_one_or_none()
    if v1 is None:
        return []
    seed_by_code = {c["code"]: c for c in seed["criteria"]}
    changed: list[str] = []
    for c in v1.criteria:
        sc = seed_by_code.get(c.code)
        if sc is None:
            continue
        if specs.get(c.code) and c.judge_spec != specs[c.code]:
            c.judge_spec = specs[c.code]
            changed.append(f"{c.code}.judge_spec")
        if (sc.get("channel_overrides") or {}) != (c.channel_overrides or {}):
            c.channel_overrides = sc.get("channel_overrides") or {}
            changed.append(f"{c.code}.channel_overrides")
    if changed:
        db.commit()
        logging.getLogger("cqa.seed").info("organizer checklist v1 synced from seeds: %s", ", ".join(changed))
    return changed


def ensure_seed(db: Session) -> None:
    if db.execute(select(Checklist.id)).first():
        sync_seed_into_organizer_version(db)
        return
    seed = json.loads((settings.seeds_dir / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
    specs_path = settings.seeds_dir / "judge_specs_v1.json"
    specs = json.loads(specs_path.read_text(encoding="utf-8")) if specs_path.exists() else {}
    for c in seed["criteria"]:
        if c["code"] in specs and not c.get("judge_spec"):
            c["judge_spec"] = specs[c["code"]]
    note = seed.get("version_note") or "v1 (seed)"
    try:
        create_checklist(db, seed, change_note=note, compile_missing=False)
    except ChecklistValidationError as e:  # never block start-up on a fresh database
        log.warning("seed checklist does not pass validation (%s); stored as is", e.message)
        db.rollback()
        create_checklist(db, seed, change_note=note, compile_missing=False, validate=False)


# ------------------------------------------------------------------------------------------------ create


def _validated(payload: dict[str, Any], previous: ChecklistVersion | None, warnings_out: list[dict[str, str]] | None) -> dict[str, Any]:
    prepared = prepare_checklist_payload(payload, inherited_sections=(previous.sections if previous else None),
                                         inherited_scoring_rules=(previous.scoring_rules if previous else None))
    if prepared.errors:
        raise ChecklistValidationError(prepared.errors, prepared.warnings)
    if warnings_out is not None:
        warnings_out.extend(prepared.warnings)
    return prepared.payload


def create_checklist(db: Session, payload: dict[str, Any], change_note: str | None = None, compile_missing: bool = True, *,
                     activate: bool = True, validate: bool = True, warnings_out: list[dict[str, str]] | None = None) -> ChecklistVersion:
    """New checklist with version 1. Raises ChecklistValidationError (nothing is written) when the payload is invalid."""
    if validate:
        payload = _validated(payload, None, warnings_out)
    rows = _resolve_specs(db, None, None, payload.get("criteria") or [], compile_missing, warnings_out)  # LLM calls before any write
    cl = Checklist(name=payload.get("name") or "Checklist", description=payload.get("description"))
    db.add(cl)
    db.flush()
    return _create_version(db, cl, payload, version_no=1, change_note=change_note or "v1", previous=None, compile_missing=compile_missing,
                           activate=activate, rows=rows)


class VersionConflict(Exception):
    """The checklist got a newer version than the one the editor started from (CHK-12); nothing was written. The router
    answers 409 version_conflict with the latest version."""

    def __init__(self, latest_id: int, latest_no: int):
        super().__init__(f"latest version {latest_no} (id {latest_id})")
        self.latest_id = latest_id
        self.latest_no = latest_no


# The re-check and the insert of a new version run under one lock per checklist (the API is one process); the database
# lock taken in create_version also covers a second process.
_version_locks: dict[int, threading.Lock] = {}
_version_locks_guard = threading.Lock()


def _version_lock(checklist_id: int) -> threading.Lock:
    with _version_locks_guard:
        return _version_locks.setdefault(checklist_id, threading.Lock())


def _latest_version(db: Session, checklist_id: int) -> Any:
    """(id, version_no) of the newest version, read from the database rather than the session; None without versions."""
    return db.execute(select(ChecklistVersion.id, ChecklistVersion.version_no).where(ChecklistVersion.checklist_id == checklist_id)
                      .order_by(ChecklistVersion.version_no.desc(), ChecklistVersion.id.desc()).limit(1)).first()


def create_version(db: Session, checklist_id: int, payload: dict[str, Any], change_note: str | None, compile_missing: bool = True, *,
                   activate: bool = True, validate: bool = True, warnings_out: list[dict[str, str]] | None = None,
                   base_version_id: int | None = None) -> ChecklistVersion:
    """New version of a checklist. `activate=False` saves it as a draft and keeps the current active version.

    `base_version_id` is the version the editor started from (optimistic concurrency, CHK-12): when a newer version exists,
    VersionConflict is raised and nothing is written. It is checked first and again together with the insert, after the
    judge specs are compiled (the slow LLM part, no lock held), so two concurrent saves from the same base give one new
    version and one conflict. Without it the save always goes through, with the next free version number.

    Raises KeyError when the checklist does not exist and ChecklistValidationError when the payload is invalid.
    """
    cl = db.get(Checklist, checklist_id)
    if cl is None:
        raise KeyError("checklist not found")
    prev = db.execute(select(ChecklistVersion).where(ChecklistVersion.checklist_id == checklist_id)
                      .order_by(ChecklistVersion.version_no.desc(), ChecklistVersion.id.desc())
                      .options(selectinload(ChecklistVersion.criteria))).scalars().first()
    if base_version_id is not None and prev is not None and prev.id != base_version_id:
        raise VersionConflict(prev.id, prev.version_no)
    if validate:
        payload = _validated(payload, prev, warnings_out)
    rows = _resolve_specs(db, cl.id, prev, payload.get("criteria") or [], compile_missing, warnings_out)  # LLM calls before any write
    with _version_lock(cl.id):
        # re-check and insert as one step: the no-op UPDATE takes the database write lock (SQLite) or the row lock
        # (PostgreSQL) until _create_version commits, so no other save can add a version in between
        try:
            db.execute(update(Checklist).where(Checklist.id == cl.id).values(name=Checklist.name)
                       .execution_options(synchronize_session=False))
            latest = _latest_version(db, cl.id)
            if base_version_id is not None and latest is not None and latest.id != base_version_id:
                raise VersionConflict(latest.id, latest.version_no)
            if payload.get("name"):
                cl.name = payload["name"]
            if payload.get("description") is not None:
                cl.description = payload["description"]
            return _create_version(db, cl, payload, version_no=(latest.version_no + 1) if latest is not None else 1,
                                   change_note=change_note, previous=prev, compile_missing=compile_missing, activate=activate, rows=rows)
        except Exception:
            db.rollback()  # nothing half-written, and the database lock is released before the next save takes its turn
            raise


def _known_specs(db: Session, checklist_id: int | None, previous: ChecklistVersion | None) -> dict[tuple[str, str], dict[str, Any]]:
    """(code, judge-relevant signature) → judge_spec from earlier versions: the previous version first, then newest first.

    An unchanged criterion keeps its reviewed spec even when it was edited and reverted in between (v1 → v2 → v3 = v1).
    """
    ordered: list[Criterion] = list(previous.criteria) if previous is not None else []
    if checklist_id is not None:
        ordered += db.execute(select(Criterion).join(ChecklistVersion, Criterion.checklist_version_id == ChecklistVersion.id)
                              .where(ChecklistVersion.checklist_id == checklist_id)
                              .order_by(ChecklistVersion.version_no.desc(), Criterion.id)).scalars().all()
    known: dict[tuple[str, str], dict[str, Any]] = {}
    for c in ordered:
        if c.judge_spec:
            known.setdefault((c.code, judge_relevant_signature(_criterion_dict(c))), c.judge_spec)
    return known


def _compile_specs(crits: list[dict[str, Any]], db: Session | None = None,
                   warnings_out: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Compile judge specs for several criteria (one LLM call each, at most compiler.COMPILE_CONCURRENCY at once).

    The compiler never raises: on any failure (or without an API key) it returns the deterministic fallback spec. With
    an API key every compilation is a paid call: the daily budget is checked for all of them first and reserved while
    they run; when it is exhausted the deterministic fallback specs are used and a warning says so.
    """
    if not crits:
        return []
    if compiler.llm_compiles() and db is not None:
        from ..jobs import guards

        estimate = compiler.COMPILE_ESTIMATE_USD * len(crits)
        try:
            with guards.budget_reservation(db, estimate):
                return compiler.run_in_fresh_loop(lambda: compiler.compile_many_async(crits))
        except guards.GuardError as e:
            log.warning("judge specs not compiled (%s): deterministic fallback for %d criteria", e.code, len(crits))
            if warnings_out is not None:
                warnings_out.append(field_error("criteria", f"Суточный бюджет на модель исчерпан: спецификации судьи для "
                                                            f"{len(crits)} критериев собраны по шаблону, без LLM. Их можно "
                                                            "пересобрать позже кнопкой «Пересобрать спецификацию»."))
            return [compiler.fallback_spec(c) for c in crits]
    return compiler.run_in_fresh_loop(lambda: compiler.compile_many_async(crits))


def _resolve_specs(db: Session, checklist_id: int | None, previous: ChecklistVersion | None, criteria: list[dict[str, Any]],
                   compile_missing: bool, warnings_out: list[dict[str, str]] | None = None) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Normalised criteria with their judge_spec: given in the payload, reused from an earlier version with the same
    judge-relevant fields, or compiled (all missing ones concurrently). Runs before any database write, so the SQLite
    write lock is never held while the LLM compiles."""
    known = _known_specs(db, checklist_id, previous) if (previous is not None or checklist_id is not None) else {}
    rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for i, raw in enumerate(criteria):
        c = _normalize_criterion(raw, i)
        rows.append((c, c.get("judge_spec") or known.get((c["code"], judge_relevant_signature(c)))))
    missing = [k for k, (_, spec) in enumerate(rows) if not spec] if compile_missing else []
    for k, spec in zip(missing, _compile_specs([rows[k][0] for k in missing], db, warnings_out)):
        rows[k] = (rows[k][0], spec)
    return rows


def _create_version(db: Session, cl: Checklist, payload: dict[str, Any], *, version_no: int, change_note: str | None,
                    previous: ChecklistVersion | None, compile_missing: bool, activate: bool = True,
                    rows: list[tuple[dict[str, Any], dict[str, Any] | None]] | None = None) -> ChecklistVersion:
    if rows is None:
        rows = _resolve_specs(db, cl.id, previous, payload.get("criteria") or [], compile_missing)
    if activate:
        for v in db.execute(select(ChecklistVersion).where(ChecklistVersion.checklist_id == cl.id)).scalars():
            v.is_active = False
    ver = ChecklistVersion(checklist_id=cl.id, version_no=version_no, is_active=bool(activate), change_note=change_note,
                           scoring_rules=payload.get("scoring_rules") or (copy.deepcopy(previous.scoring_rules) if previous else {}),
                           sections=payload.get("sections") or (copy.deepcopy(previous.sections) if previous else []),
                           created_from_version_id=(previous.id if previous else None))
    db.add(ver)
    db.flush()
    for c, spec in rows:
        db.add(Criterion(checklist_version_id=ver.id, code=c["code"], order=c["order"], section=c.get("section"), title=c["title"],
                         instruction=c.get("instruction") or "", answer_type=c["answer_type"], options=c["options"], max_points=float(c["max_points"]),
                         weight=c["weight"], na_allowed=c["na_allowed"], na_condition=c.get("na_condition"), is_critical=c["is_critical"],
                         critical_value=c.get("critical_value") or ("yes" if c["is_critical"] else None), channel_overrides=c["channel_overrides"], judge_spec=spec))
    db.commit()
    db.expire(cl, ["versions"])
    db.refresh(ver)
    return ver


# ------------------------------------------------------------------------------------------------ serialize / export


def _criterion_dict(c: Criterion) -> dict[str, Any]:
    return {"id": c.id, "code": c.code, "order": c.order, "section": c.section, "title": c.title, "instruction": c.instruction,
            "answer_type": c.answer_type, "options": c.options, "max_points": c.max_points, "weight": c.weight, "na_allowed": c.na_allowed,
            "na_condition": c.na_condition, "is_critical": c.is_critical, "critical_value": c.critical_value,
            "channel_overrides": c.channel_overrides or {}, "judge_spec": c.judge_spec}


def _round2(x: float) -> float:
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _attr(c: Any, name: str) -> Any:
    return c.get(name) if isinstance(c, dict) else getattr(c, name)


def weights_summary(criteria: Iterable[Any]) -> dict[str, Any]:
    """{"total": Σ weights, "by_code": {code: weight}, "normalized": {code: weight scaled to 100, two decimals half-up}}.

    Weights are relative: the scoring engine always rescales them (and redistributes N/A) to a 0–100 total, so a
    checklist with weights summing to 110 scores exactly like the normalized one.
    """
    by_code = {_attr(c, "code"): float(_attr(c, "weight") or 0.0) for c in criteria}
    total = sum(by_code.values())
    normalized = {code: (_round2(w * 100.0 / total) if total > 0 else 0.0) for code, w in by_code.items()}
    return {"total": round(total, 6), "by_code": by_code, "normalized": normalized}


def evaluation_counts(db: Session, version_ids: Iterable[int | None] | None = None) -> dict[int, int]:
    """Completed evaluations per checklist version ({version_id: n}, versions without any left out) in one grouped COUNT,
    never a query per version. A version with evaluations records how they were judged: the studio opens it read-only."""
    stmt = (select(Evaluation.checklist_version_id, func.count(Evaluation.id))
            .where(Evaluation.status == "completed").group_by(Evaluation.checklist_version_id))
    if version_ids is not None:
        ids = sorted({int(i) for i in version_ids if i is not None})
        if not ids:
            return {}
        stmt = stmt.where(Evaluation.checklist_version_id.in_(ids))
    return {int(vid): int(n) for vid, n in db.execute(stmt)}


def _counts_of(obj: Any, version_ids: Iterable[int | None]) -> dict[int, int]:
    """evaluation_counts through the ORM object's own session (the serializers take objects, not a session)."""
    session = object_session(obj)
    return evaluation_counts(session, version_ids) if session is not None else {}


def serialize_version(v: ChecklistVersion, counts: dict[int, int] | None = None) -> dict[str, Any]:
    """Version JSON; ``counts`` = evaluation_counts(...) when the caller already has them (else one COUNT query here)."""
    criteria = sorted(v.criteria, key=lambda c: c.order)
    if counts is None:
        counts = _counts_of(v, [v.id])
    return {"id": v.id, "version_id": v.id, "checklist_id": v.checklist_id, "version_no": v.version_no, "is_active": v.is_active,
            "change_note": v.change_note, "scoring_rules": v.scoring_rules, "sections": v.sections,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "created_from_version_id": v.created_from_version_id, "criteria": [_criterion_dict(c) for c in criteria],
            "weights": weights_summary(criteria), "export_ids": export_ids_for([c.code for c in criteria], v.scoring_rules or {}),
            "evaluations": counts.get(v.id, 0)}


def serialize_checklist(cl: Checklist, counts: dict[int, int] | None = None) -> dict[str, Any]:
    """Checklist JSON with its versions; pass ``counts`` = evaluation_counts(db) when listing several checklists (one query
    for all of them), otherwise this checklist's versions are counted in one grouped query."""
    if counts is None:
        counts = _counts_of(cl, [v.id for v in cl.versions])
    active = next((v for v in cl.versions if v.is_active), None) or (cl.versions[-1] if cl.versions else None)
    return {"id": cl.id, "name": cl.name, "description": cl.description, "created_at": cl.created_at.isoformat() if cl.created_at else None,
            "active_version": serialize_version(active, counts) if active else None,
            "versions": [{"id": v.id, "version_id": v.id, "version_no": v.version_no,
                          "created_at": v.created_at.isoformat() if v.created_at else None, "change_note": v.change_note,
                          "is_active": v.is_active, "evaluations": counts.get(v.id, 0)} for v in cl.versions]}


def export_version(v: ChecklistVersion) -> dict[str, Any]:
    """Portable JSON of one version, accepted as is by POST /api/checklists (and by POST /api/checklists/{id}/versions).

    Criteria carry their judge_spec, so an import reuses the reviewed specs instead of compiling new ones.
    """
    cl = v.checklist
    criteria = []
    for c in sorted(v.criteria, key=lambda c: c.order):
        d = copy.deepcopy(_criterion_dict(c))
        d.pop("id", None)
        criteria.append(d)
    return {
        "format": EXPORT_FORMAT,
        "name": cl.name if cl else None,
        "description": cl.description if cl else None,
        "scoring_rules": copy.deepcopy(v.scoring_rules or {}),
        "sections": copy.deepcopy(v.sections or []),
        "criteria": criteria,
        "exported_from": {"checklist_id": v.checklist_id, "version_id": v.id, "version_no": v.version_no, "change_note": v.change_note,
                          "exported_at": datetime.now(timezone.utc).isoformat()},
    }


def import_change_note(doc: dict[str, Any]) -> str:
    src = doc.get("exported_from") if isinstance(doc.get("exported_from"), dict) else {}
    if src.get("checklist_id") is not None and src.get("version_no") is not None:
        return f"Импорт: чек-лист #{src['checklist_id']}, версия {src['version_no']}"
    return "Импорт из файла"


def activate(db: Session, version: ChecklistVersion) -> None:
    for v in db.execute(select(ChecklistVersion).where(ChecklistVersion.checklist_id == version.checklist_id)).scalars():
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


def default_checklist_id(db: Session) -> int | None:
    """The checklist evaluations default to: DB setting `default_checklist_id` (PUT /api/settings), else the
    environment (DEFAULT_CHECKLIST_ID), else None (= the lowest checklist id with an active version)."""
    row = db.get(Setting, "default_checklist_id")
    value = row.value if row is not None and row.value not in (None, "") else settings.default_checklist_id
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def active_version(db: Session) -> ChecklistVersion | None:
    """The version used when a request names none (GET /api/checklists/active, evaluation default). Imports create
    their own checklist with its own active version, so the order is explicit: the default checklist's active
    version, else the active version of the lowest checklist id (the organizer checklist), else the newest version."""
    active = select(ChecklistVersion).where(ChecklistVersion.is_active.is_(True)).options(selectinload(ChecklistVersion.criteria))
    cid = default_checklist_id(db)
    if cid is not None:
        v = db.execute(active.where(ChecklistVersion.checklist_id == cid).order_by(ChecklistVersion.id.desc())).scalars().first()
        if v is not None:
            return v
    v = db.execute(active.order_by(ChecklistVersion.checklist_id, ChecklistVersion.id.desc())).scalars().first()
    if v is None:
        v = db.execute(select(ChecklistVersion).order_by(ChecklistVersion.id.desc()).options(selectinload(ChecklistVersion.criteria))).scalars().first()
    return v


# ------------------------------------------------------------------------------------------------ single-criterion trial


def _display_verdict(verdict: dict[str, Any], max_index: int | None) -> dict[str, Any]:
    """The trial's verdict as people read it: turn references in the texts 1-based, like every other output (the judge's
    own 0-based labels stay in the raw answer)."""
    from ..serializers import claims_out
    from ..turnrefs import display_turn_refs

    out = dict(verdict)
    for key in ("explanation", "na_reason"):
        out[key] = display_turn_refs(out.get(key), max_index)
    # claim notes and step lines cite turns too: one numbering in the whole verdict (claims as on the card)
    out["claims"] = claims_out(out.get("claims"), max_index)
    out["step_results"] = [display_turn_refs(s, max_index) for s in out.get("step_results") or []]
    return out


def spent_last_24h(db: Session) -> float:
    """Spend of the rolling last 24 hours, the platform budget's definition (evaluations + single-criterion trials)."""
    from ..jobs.guards import spent_since

    return spent_since(db, datetime.now(timezone.utc) - timedelta(hours=24))


def _budget_guard(db: Session, provider_name: str, n_members: int, *, compiles: bool = False) -> None:
    """Check only (no reservation) whether a trial would be refused by the daily budget: CriterionTestError 429. The
    trial itself reserves its estimate through _reserve_trial_budget."""
    if provider_name in FREE_PROVIDERS and not compiles:
        return
    from ..jobs.guards import GuardError, check_budget

    estimate = _trial_estimate(provider_name, n_members, compiles)
    try:
        check_budget(db, estimate, free=False)
    except GuardError as e:
        raise CriterionTestError(429, "budget_exceeded", f"{e.message} Для проверки без затрат выберите провайдер local или fake.",
                                 budget={**(e.extra.get("budget") or {}), "estimate": estimate},
                                 estimate_usd=estimate, retry_after_s=e.extra.get("retry_after_s")) from e


def _trial_estimate(provider_name: str, n_members: int, compiles: bool) -> float:
    judge = TEST_CRITERION_ESTIMATE_USD * max(n_members, 1) if provider_name not in FREE_PROVIDERS else 0.0
    return round(judge + (compiler.COMPILE_ESTIMATE_USD if compiles else 0.0), 2)


async def _reserve_trial_budget(db: Session, estimate: float, paid: bool) -> int | None:
    """The platform's daily budget for a paid trial (judge calls, and the spec compilation it may need), reserved until
    the trial ends so concurrent trials cannot all pass one check (cqa.jobs.guards.reserve, in the thread pool)."""
    from starlette.concurrency import run_in_threadpool

    from ..jobs.guards import GuardError, reserve

    try:
        return await run_in_threadpool(lambda: reserve(db, estimate, free=not paid))
    except GuardError as e:
        raise CriterionTestError(429, "budget_exceeded", f"{e.message} Для проверки без затрат выберите провайдер local или fake.",
                                 budget={**(e.extra.get("budget") or {}), "estimate": estimate},
                                 estimate_usd=estimate, retry_after_s=e.extra.get("retry_after_s")) from e


class _TrialSlot:
    """At most settings.max_concurrent_trials single-criterion trials at once (429 too_many_trials otherwise)."""

    def __enter__(self) -> "_TrialSlot":
        global _trials_running
        limit = int(settings.max_concurrent_trials or 0)
        with _trials_lock:
            if limit > 0 and _trials_running >= limit:
                raise CriterionTestError(429, "too_many_trials", f"Сейчас уже выполняется проверок критерия: {_trials_running} "
                                                                 f"(не больше {limit} одновременно). Повторите через несколько секунд.",
                                         running=_trials_running, limit=limit, retry_after_s=5)
            _trials_running += 1
        return self

    def __exit__(self, *exc: Any) -> None:
        global _trials_running
        with _trials_lock:
            _trials_running = max(_trials_running - 1, 0)


def _trial_members(judge_runner: Any, rs: dict[str, Any], provider_name: str, model: str | None) -> list[Any]:
    if provider_name == "fake":  # the runner's _members() maps unknown names to anthropic, so resolve the offline judge directly
        return [judge_runner.get_provider("fake", model)]
    return judge_runner._members(rs, provider_name, model)


async def run_criterion_test(db: Session, *, criterion: Any, conversation_id: Any, provider: Any = None, model: Any = None,
                             compile_spec: bool = True) -> dict[str, Any]:
    """Judge ONE criterion (possibly unsaved) on ONE conversation with the production judge path; nothing is persisted.

    Reuses cqa.judge.runner: runtime settings, provider members, turn conversion and PII masking, the criterion pack,
    few-shots, reference facts and judge_criterion (validation, retry, ensemble vote, arbiter). Raises
    ChecklistValidationError (422) or CriterionTestError (404 / 400 / 429 / 502).
    """
    from ..ingest.normalize import pack_transcript
    from ..judge import runner as judge_runner
    from ..judge.providers import ProviderError
    from ..judge.reference import reference_facts_for
    from ..scoring.engine import answer_points
    from ..serializers import votes_out

    crit, errors, warnings = prepare_criterion_payload(criterion, path="criterion")
    cid = conversation_id.strip() if isinstance(conversation_id, str) else None
    if not cid:
        errors.append(field_error("conversation_id", "Укажите id диалога, на котором проверить критерий."))
    rs = judge_runner._runtime_settings(db)
    if provider is not None and not isinstance(provider, str):
        errors.append(field_error("provider", "Провайдер задаётся строкой: anthropic, local, fake или ensemble."))
        provider = None
    provider_name = (provider.strip().lower() if isinstance(provider, str) and provider.strip() else str(rs.get("default_provider") or "anthropic").lower())
    if provider_name not in TEST_PROVIDERS:
        errors.append(field_error("provider", f"Провайдер «{provider_name}» не поддерживается: допустимы anthropic, local, fake, ensemble."))
    if model is not None and not isinstance(model, str):
        errors.append(field_error("model", "Модель задаётся строкой, например «claude-opus-5-5»."))
    model = model.strip() if isinstance(model, str) and model.strip() else None
    if errors or crit is None:
        raise ChecklistValidationError(errors, warnings, code="invalid_criterion", message=summary_message(errors, "Критерий"))

    conv = db.execute(select(Conversation).where(Conversation.id == cid).options(selectinload(Conversation.turns))).scalar_one_or_none()
    if conv is None:
        raise CriterionTestError(404, "not_found", f"Диалог «{cid}» не найден.")
    if not conv.turns:
        raise CriterionTestError(422, "empty_conversation", f"В диалоге «{cid}» нет реплик — проверять нечего.")

    try:
        members = _trial_members(judge_runner, rs, provider_name, model)
    except ProviderError as e:
        raise CriterionTestError(400, "provider_unavailable", f"Провайдер «{provider_name}» недоступен: {e}") from e
    try:
        judge_runner.require_known_models(members)
        judge_runner.check_transcript_size(conv.id, *judge_runner.transcript_size(conv))
    except judge_runner.UnknownModelError as e:
        raise CriterionTestError(422, "unknown_model", str(e), model=e.model,
                                 errors=[{"field": "model", "message": str(e)}]) from e
    except judge_runner.TranscriptTooLong as e:
        raise CriterionTestError(422, "transcript_too_long", str(e), chars=e.chars, turns=e.turns) from e

    compiles = not crit.get("judge_spec") and provider_name != "fake" and bool(compile_spec)
    paid_compile = compiles and compiler.llm_compiles()
    paid = provider_name not in FREE_PROVIDERS or paid_compile
    estimate = _trial_estimate(provider_name, len(members), paid_compile)

    # everything read from the database is loaded now; the read transaction ends before the paid calls (no pooled
    # connection is held while the model answers)
    pack = judge_runner.conversation_pack(db, conv, provider_name, rs)  # the production pack: masking, facts, acoustics
    turns = pack.turns
    fewshots = judge_runner._load_fewshots(db, crit["code"], conv.language, conv.channel, rs.get("fewshot_from_reviews", True))
    reference = reference_facts_for(pack_transcript(turns), crit["code"])
    conv_id = conv.id
    conv_max_index = (conv.turn_count - 1) if conv.turn_count else None  # read now: conv is not touched after the judge call

    from ..jobs import ledger
    from ..jobs.guards import release

    with _TrialSlot():
        rid = await _reserve_trial_budget(db, estimate, paid)
        try:
            db.commit()
            with ledger.context(kind="trial", ref=f"conversation:{conv_id}"):
                spec_source = "provided"
                if not crit.get("judge_spec"):
                    if not compiles:
                        crit["judge_spec"], spec_source = compiler.fallback_spec(crit), "fallback"
                    else:
                        crit["judge_spec"] = await compiler.compile_spec_async(crit)
                        spec_source = "compiled" if crit["judge_spec"].get("_compiled_by") else "fallback"
                # a transient ORM row (never added to the session) so the runner's own _criterion_pack() builds the prompt input
                draft = Criterion(code=crit["code"], order=crit.get("order", 0), section=crit.get("section"), title=crit["title"],
                                  instruction=crit.get("instruction") or "", answer_type=crit["answer_type"], options=crit["options"],
                                  max_points=float(crit["max_points"]), weight=float(crit.get("weight") or 0), na_allowed=crit["na_allowed"],
                                  na_condition=crit.get("na_condition"), is_critical=crit["is_critical"], critical_value=crit.get("critical_value"),
                                  channel_overrides=crit.get("channel_overrides") or {}, judge_spec=crit["judge_spec"])
                cpack = judge_runner._criterion_pack(draft)
                t0 = time.perf_counter()
                outcome = await judge_runner.judge_criterion(members, pack, cpack, rs=rs, fewshots=fewshots, reference_facts=reference)
                latency_ms = int((time.perf_counter() - t0) * 1000)
        finally:
            release(rid)
    model_label = ",".join(f"{p.name}:{p.model}" for p in members)
    cost = round(outcome.cost_usd, 6)
    if outcome.error:
        raise CriterionTestError(502, "judge_failed", f"Модель не смогла оценить критерий {crit['code']}: {outcome.error}",
                                 cost_usd=cost, latency_ms=latency_ms, model=model_label)
    v = outcome.verdict
    points = answer_points(crit["options"], v.answer) if v.applicable else None
    return {
        "verdict": _display_verdict(v.model_dump(), conv_max_index),
        "answer": v.answer if v.applicable else "N/A",
        "applicable": v.applicable,
        "confidence": v.confidence,
        "score": points,
        "points": points,
        "max_points": crit["max_points"],
        "judge_spec": crit["judge_spec"],
        "judge_spec_source": spec_source,
        "cost_usd": cost,
        "latency_ms": latency_ms,
        "model": model_label,
        "provider": provider_name,
        "conversation_id": conv_id,
        "code": crit["code"],
        "votes": votes_out(outcome.votes, conv_max_index),  # ensemble members' explanations, numbered like the verdict
        "issues": outcome.issues,
        "input_tokens": sum(t.get("input_tokens", 0) for t in outcome.traces),
        "output_tokens": sum(t.get("output_tokens", 0) for t in outcome.traces),
        "warnings": warnings,
        "persisted": False,
    }
