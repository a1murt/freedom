from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..ingest.adapter import IngestError, fetch_organizer, record_from_text, records_from_payload
from ..ingest.service import ON_CONFLICT, ConversationBusy, ConversationExists, import_records, ingest_record
from ..ingest.language import detect_language
from ..models import Conversation, Evaluation
from ..serializers import conversation_detail, conversation_summary, evaluation_dict, iso_utc, latest_evaluation, review_state_of, trace_dict

router = APIRouter(prefix="/api", tags=["conversations"])


class ImportBody(BaseModel):
    group_id: str


class PasteBody(BaseModel):
    conversation_id: str | None = None
    channel: str = "agent chat"
    language: str | None = None  # RU | KZ | EN; omitted or "auto" → detected from the client and operator turns
    dry_run: bool = False  # parse and report (turns, language, operator, conflicts) without saving anything
    text: str | None = None
    transcript: list[dict[str, Any]] | None = None
    audio_url: str | None = None
    on_conflict: str = "error"  # error | rename | replace | skip (an identical transcript is never a conflict)


def _conflict_error(e: ConversationExists) -> HTTPException:
    return HTTPException(409, detail={
        "code": "conversation_exists",
        "message": (f"Диалог «{e.conversation_id}» уже есть, и его текст отличается. Чтобы не потерять оценки, он не перезаписан: "
                    f"сохраните копию под именем «{e.suggested_id}» (on_conflict=rename) или замените его (on_conflict=replace)."),
        "conversation_id": e.conversation_id, "suggested_id": e.suggested_id, "options": ["rename", "replace"]})


def _busy_error(e: ConversationBusy) -> HTTPException:
    return HTTPException(409, detail={"code": "conversation_busy", "conversation_id": e.conversation_id,
                                      "message": f"Диалог «{e.conversation_id}» сейчас оценивается: замените его после завершения оценки."})


def _check_on_conflict(value: str) -> str:
    value = (value or "error").strip().lower()
    if value not in ON_CONFLICT:
        raise HTTPException(422, detail={"code": "validation_error", "message": f"on_conflict: одно из {', '.join(ON_CONFLICT)}",
                                         "errors": [{"field": "on_conflict", "message": f"одно из {', '.join(ON_CONFLICT)}"}]})
    return value


@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db)):
    """Imported datasets (one row per import) with how many conversations currently belong to each."""
    from sqlalchemy import func

    from ..models import Dataset

    counts = dict(db.execute(select(Conversation.dataset_id, func.count(Conversation.id)).group_by(Conversation.dataset_id)).all())
    rows = db.execute(select(Dataset).order_by(Dataset.id)).scalars().all()
    return [{"id": d.id, "group_id": d.group_id, "source": d.source, "imported_at": iso_utc(d.imported_at),
             "record_count": d.record_count, "conversations": counts.get(d.id, 0)} for d in rows]


@router.post("/datasets/import")
def import_dataset(body: ImportBody, db: Session = Depends(get_db)):
    try:
        payload = fetch_organizer(body.group_id.strip())
        records = records_from_payload(payload)
    except IngestError as e:
        raise HTTPException(status_code=502, detail={"code": "import_failed", "message": str(e)})
    return import_records(db, records, body.group_id.strip())


@router.post("/conversations/upload")
async def upload_conversations(file: UploadFile = File(...), on_conflict: str = Query(default="error"), db: Session = Depends(get_db)):
    """Organizer-format JSON (one or many records) or a text transcript. Existing ids with identical content are
    reported as `unchanged`; a different transcript under an existing id is refused with 409 `conversation_exists`
    unless `on_conflict` is rename | replace | skip (rename stores `<id>-v2`). A JSON file with exactly one record also
    returns `id` (the conversation that record is stored as) next to the import summary, so the web app can open it."""
    on_conflict = _check_on_conflict(on_conflict)
    raw = await file.read()
    name = (file.filename or "upload").lower()
    try:
        if name.endswith(".json"):
            records = records_from_payload(json.loads(raw.decode("utf-8")))
            summary = import_records(db, records, group_id=f"upload:{file.filename}", source="upload", on_conflict=on_conflict)
            if len(records) == 1 and summary["items"] and summary["items"][0].get("conversation_id"):
                summary["id"] = summary["items"][0]["conversation_id"]
            return summary
        text = raw.decode("utf-8")
        cid = name.rsplit(".", 1)[0][:60]
        rec = record_from_text(text, conversation_id=cid)
        rec["language"] = detect_language(rec.get("transcript") or [])["language"]
        res = ingest_record(db, rec, on_conflict=on_conflict)
        db.commit()
        return {**conversation_detail(db, res.conversation), "import_status": res.status, "requested_id": res.requested_id}
    except ConversationExists as e:
        db.rollback()
        raise _conflict_error(e)
    except ConversationBusy as e:
        db.rollback()
        raise _busy_error(e)
    except (IngestError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": "bad_upload", "message": str(e)})


@router.post("/conversations/paste")
def paste_conversation(body: PasteBody, db: Session = Depends(get_db)):
    import secrets

    cid = (body.conversation_id or "").strip() or f"manual-{secrets.token_hex(3)}"
    lang_in = (body.language or "").strip().upper()
    if lang_in in ("", "AUTO"):
        lang_in = ""
    elif lang_in not in ("RU", "KZ", "EN"):
        raise HTTPException(422, detail={"code": "validation_error", "message": "language: RU, KZ, EN или auto",
                                         "errors": [{"field": "language", "message": "RU, KZ, EN или auto"}]})
    try:
        if body.transcript:
            rec = {"conversation_id": cid, "channel": body.channel, "language": lang_in or "RU", "transcript": body.transcript, "audio_url": body.audio_url}
        elif body.text:
            rec = record_from_text(body.text, conversation_id=cid, channel=body.channel, language=lang_in or "RU")
        else:
            raise IngestError("Вставьте текст диалога (text) или реплики (transcript)")
    except IngestError as e:
        raise HTTPException(status_code=400, detail={"code": "bad_transcript", "message": str(e)})
    detected = detect_language(rec.get("transcript") or [])
    if not lang_in:
        rec["language"] = detected["language"]
    on_conflict = _check_on_conflict(body.on_conflict)
    if body.dry_run:
        return _paste_preview(db, rec, detected, language_given=bool(lang_in))
    try:
        res = ingest_record(db, rec, on_conflict=on_conflict)
    except ConversationExists as e:
        db.rollback()
        raise _conflict_error(e)
    except ConversationBusy as e:
        db.rollback()
        raise _busy_error(e)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": "bad_transcript", "message": str(e)})
    db.commit()
    conv = res.conversation
    db.refresh(conv)
    return {**conversation_detail(db, conv), "import_status": res.status, "requested_id": res.requested_id,
            "language_detected": detected, "language_warning": _language_warning(rec["language"], detected, bool(lang_in))}


def _language_warning(language: str, detected: dict[str, Any], given: bool) -> str | None:
    if given and detected["language"] != language and detected["confidence"] >= 0.8:
        return f"Указан язык {language}, но текст похож на {detected['language']}: проверьте метку языка (от неё зависят аналитика и язык обоснований)."
    return None


def _paste_preview(db: Session, rec: dict[str, Any], detected: dict[str, Any], *, language_given: bool) -> dict[str, Any]:
    """What a paste would store (CONV-04): parsed turns, language, operator, first response, warnings; nothing is written."""
    from ..ingest.normalize import normalize
    from ..ingest.service import free_id, is_busy, record_fingerprint, stored_fingerprint

    norm = normalize(rec)
    cid = rec["conversation_id"]
    existing = db.get(Conversation, cid)
    if existing is None:
        status, suggested = "created", None
    elif ((existing.meta or {}).get("content_hash") or stored_fingerprint(existing)) == record_fingerprint(rec, norm):
        status, suggested = "unchanged", None
    else:
        status, suggested = ("busy" if is_busy(db, cid) else "conflict"), free_id(db, cid)
    speakers = {t.speaker for t in norm.turns}
    warnings = []
    if "operator" not in speakers:
        warnings.append("Нет реплик оператора: оценивать нечего (проверьте метки «оператор:» / «operator:»).")
    if "client" not in speakers:
        warnings.append("Нет реплик клиента: проверьте метки «клиент:» / «client:».")
    unknown = sorted(speakers - {"client", "operator", "bot", "system"})
    if unknown:
        warnings.append(f"Неизвестные метки говорящих: {', '.join(unknown)} — они будут учтены как есть.")
    lw = _language_warning(rec["language"], detected, language_given)
    if lw:
        warnings.append(lw)
    if norm.meta.get("steering_turns"):
        warnings.append("Есть реплики, обращённые к системе оценки: модель учтёт их как часть диалога, а не как инструкцию.")
    return {"dry_run": True, "conversation_id": cid, "import_status": status, "suggested_id": suggested,
            "channel": rec.get("channel"), "language": rec["language"], "language_detected": detected,
            "operator_name": norm.operator_name, "first_response_seconds": norm.first_response_seconds,
            "terminal_event": norm.terminal_event, "turn_count": len(norm.turns),
            "speakers": {sp: sum(1 for t in norm.turns if t.speaker == sp) for sp in sorted(speakers)},
            "turns": [{"idx": t.idx, "turn": t.idx + 1, "timestamp_raw": t.timestamp_raw, "speaker": t.speaker, "text": t.text,
                       "segment": t.segment, "is_duplicate": t.is_duplicate} for t in norm.turns],
            "steering_turns": norm.meta.get("steering_turns", []), "warnings": warnings}


SORTS = ("imported", "id", "total_asc", "total_desc", "evaluated_desc", "first_response_desc")


def _is_na(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == "N/A"


def _final_answer(row) -> Any:
    """The reviewer's answer, else the AI answer ("N/A" when not applicable). None: the judge call for this criterion
    failed (applicable, no answer, flag "error"), which is neither N/A nor any answer."""
    if row.final_answer is not None:
        return row.final_answer
    return row.answer if row.applicable else "N/A"


def _loses_points(row) -> bool:
    """The criterion loses points in the final result (same rule as the scoring engine and /api/overview): an answered
    criterion with weight whose final points are below its maximum. Judged by the final answer alone: the stored
    weight_applied belongs to the AI answers and is stale after a reviewer decision to or from N/A."""
    if row.final_answer is not None:
        if _is_na(row.final_answer):
            return False
        points = row.final_score
    elif not row.applicable or row.answer is None:
        return False
    else:
        points = row.score
    if points is None or not float(row.weight or 0) > 0:
        return False
    max_points = float(row.max_points or 0)
    ratio = min(1.0, max(0.0, float(points) / max_points)) if max_points > 0 else 0.0
    return ratio < 1.0


def _attempt_brief(ev: Evaluation) -> dict[str, Any]:
    return {"id": ev.id, "status": ev.status, "error": ev.error, "created_at": iso_utc(ev.created_at)}  # UTC offset like the evaluation's own times


def _snippet(text: str, needle: str, width: int = 60) -> str:
    i = text.lower().find(needle)
    if i < 0:
        return text[: width * 2]
    lo, hi = max(0, i - width), min(len(text), i + len(needle) + width)
    return ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")


@router.get("/conversations")
def list_conversations(channel: str | None = None, language: str | None = None, status: str | None = None, min_total: float | None = None,
                       max_total: float | None = None, critical: bool | None = None, q: str | None = None, dataset_id: int | None = None, operator: str | None = None,
                       checklist_version_id: int | None = Query(None, description="Latest evaluation of this checklist version only"),
                       criterion: str | None = Query(None, description="Criterion code for `answer`, e.g. C7"),
                       answer: str | None = Query(None, description="Final answer of `criterion` (yes, no, 15, N/A) or `lost` (the criterion lost points)"),
                       sort: str = Query("imported", description="imported | id | total_asc | total_desc | evaluated_desc | first_response_desc"),
                       page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Conversations with their latest evaluation. The latest *completed* evaluation is shown when a later attempt
    failed (`last_attempt_failed` tells the UI to offer a retry; `status=failed` lists them). `status`: evaluated |
    not_evaluated | failed | under_review (an open appeal on the shown evaluation). With `checklist_version_id` the shown
    evaluation, the filters and the sorts use that version only. `q` searches the id, the operator and the transcript;
    transcript hits carry `match` {turn_idx, turn, snippet}."""
    if sort not in SORTS:
        raise HTTPException(422, detail={"code": "validation_error", "message": f"sort: одно из {', '.join(SORTS)}",
                                         "errors": [{"field": "sort", "message": f"одно из {', '.join(SORTS)}"}]})
    if answer is not None and not criterion:
        raise HTTPException(422, detail={"code": "validation_error", "message": "answer задаётся вместе с criterion",
                                         "errors": [{"field": "criterion", "message": "укажите код критерия для answer"}]})
    stmt = select(Conversation).order_by(Conversation.imported_at, Conversation.id)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if language:
        stmt = stmt.where(Conversation.language == language.upper())
    if dataset_id:
        stmt = stmt.where(Conversation.dataset_id == dataset_id)
    if operator:
        stmt = stmt.where(Conversation.operator_name == operator)
    convs = db.execute(stmt).scalars().all()
    ev_stmt = select(Evaluation).where(Evaluation.status.in_(["completed", "failed"])).order_by(Evaluation.id).options(selectinload(Evaluation.criteria))
    if checklist_version_id is not None:
        ev_stmt = ev_stmt.where(Evaluation.checklist_version_id == checklist_version_id)
    latest_completed: dict[str, Evaluation] = {}
    latest_attempt: dict[str, Evaluation] = {}
    for ev in db.execute(ev_stmt).scalars().all():
        latest_attempt[ev.conversation_id] = ev
        if ev.status == "completed":
            latest_completed[ev.conversation_id] = ev
    needle = q.strip().lower() if q and q.strip() else None
    code = criterion.strip().upper() if criterion else None
    want = answer.strip().lower() if answer is not None else None
    items = []
    for c in convs:
        attempt = latest_attempt.get(c.id)
        ev = latest_completed.get(c.id) or attempt
        failed_last = attempt is not None and attempt.status == "failed"
        if status == "evaluated" and (ev is None or ev.status != "completed"):
            continue
        if status == "not_evaluated" and ev is not None:
            continue
        if status == "failed" and not failed_last:
            continue
        if status == "under_review" and review_state_of(ev) != "under_review":  # the evaluation whose review_state the item shows
            continue
        if critical is not None and bool(ev and ev.status == "completed" and ev.critical_error) != critical:
            continue
        total = ev.final_total_score if ev is not None and ev.status == "completed" else None
        if min_total is not None and (total is None or total < min_total):
            continue
        if max_total is not None and (total is None or total > max_total):
            continue
        if code:
            row = next((r for r in (ev.criteria if ev is not None else []) if r.code.upper() == code), None)
            if row is None:
                continue
            if want == "lost":
                if not _loses_points(row):
                    continue
            elif want is not None:
                fa = _final_answer(row)
                if fa is None:  # the judge call failed: neither N/A nor an answer (the analytics rates leave it out too)
                    continue
                fa_s = "n/a" if _is_na(fa) else str(fa).lower()
                if want in ("n/a", "na") and fa_s != "n/a":
                    continue
                if want not in ("n/a", "na") and fa_s != want and not (fa_s.replace(".0", "") == want):
                    continue
        match = None
        if needle:
            if needle in c.id.lower() or needle in (c.operator_name or "").lower():
                match = {"field": "id" if needle in c.id.lower() else "operator_name"}
            else:
                hit = next((t for t in sorted(c.turns, key=lambda t: t.idx) if needle in (t.text or "").lower()), None)
                if hit is None:
                    continue
                match = {"field": "transcript", "turn_idx": hit.idx, "turn": hit.idx + 1, "speaker": hit.speaker,
                         "snippet": _snippet(hit.text or "", needle)}
        s = conversation_summary(db, c, ev, lookup=False)  # ev is final: never another checklist version's evaluation
        s["last_attempt_failed"] = bool(failed_last and ev is not attempt)
        if failed_last:
            s["last_attempt"] = _attempt_brief(attempt)
        if match is not None:
            s["match"] = match
        items.append(s)
    if sort == "id":
        items.sort(key=lambda s: s["id"])
    elif sort in ("total_asc", "total_desc"):
        def total_of(s):
            le = s.get("latest_evaluation") or {}
            return le.get("final_total_score") if le.get("status") == "completed" else None
        known = [s for s in items if total_of(s) is not None]
        unknown = [s for s in items if total_of(s) is None]
        known.sort(key=lambda s: (total_of(s), s["id"]), reverse=(sort == "total_desc"))
        items = known + unknown
    elif sort == "evaluated_desc":
        items.sort(key=lambda s: ((s.get("latest_evaluation") or {}).get("completed_at") or ""), reverse=True)
    elif sort == "first_response_desc":
        items.sort(key=lambda s: (s.get("first_response_seconds") is None, -(s.get("first_response_seconds") or 0)))
    total_n = len(items)
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": total_n}


class OperatorBody(BaseModel):
    operator_name: str | None = None  # null or "" clears a manual attribution and restores the detected name


@router.put("/conversations/{conversation_id}/operator")
def set_operator(conversation_id: str, body: OperatorBody, db: Session = Depends(get_db)):
    """Attribute a dialog to an operator by hand (OPR-06): the greeting did not name one, or named the wrong person.
    The detected name is kept in meta.operator_name_detected; a re-import of the same dialog keeps the manual name."""
    c = db.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, detail={"code": "not_found", "message": f"Диалог «{conversation_id}» не найден"})
    name = (body.operator_name or "").strip()
    if len(name) > 128:
        raise HTTPException(422, detail={"code": "validation_error", "message": "Имя оператора длиннее 128 символов",
                                         "errors": [{"field": "operator_name", "message": "не длиннее 128 символов"}]})
    meta = dict(c.meta or {})
    detected = meta.get("operator_name_detected", c.operator_name if meta.get("operator_name_source") != "manual" else None)
    if name:
        meta.update(operator_name_detected=detected, operator_name_source="manual")
        c.operator_name = name
    else:
        c.operator_name = detected
        meta.pop("operator_name_source", None)
        meta.pop("operator_name_detected", None)
    c.meta = meta
    db.commit()
    db.refresh(c)
    return {**conversation_summary(db, c), "operator_name_source": meta.get("operator_name_source", "detected"),
            "operator_name_detected": detected}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    c = db.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, detail={"code": "not_found", "reason": "conversation_not_found", "message": "Диалог не найден в CQA"})
    return conversation_detail(db, c)


@router.get("/conversations/{conversation_id}/evaluation")
def get_latest_evaluation(conversation_id: str, checklist_version_id: int | None = None, db: Session = Depends(get_db)):
    """The card's evaluation (of `checklist_version_id` when given). When the newest attempt failed and an earlier one
    completed, the completed result is returned with `last_attempt_failed: true` and the failed `last_attempt`
    {id, status, error, created_at}, like the list; otherwise the latest evaluation with `last_attempt_failed: false`."""
    ev = latest_evaluation(db, conversation_id, checklist_version_id)
    if ev is None:
        raise HTTPException(404, detail={"code": "not_found", "reason": "no_evaluation", "message": "Оценки пока нет"})
    q = select(Evaluation).where(Evaluation.conversation_id == conversation_id, Evaluation.status.in_(["completed", "failed"]))
    if checklist_version_id:  # the same version filter as latest_evaluation
        q = q.where(Evaluation.checklist_version_id == checklist_version_id)
    attempt = db.execute(q.order_by(Evaluation.id.desc()).limit(1)).scalars().first()
    if attempt is not None and attempt.status == "failed":
        done = db.execute(q.where(Evaluation.status == "completed").order_by(Evaluation.id.desc()).limit(1)).scalars().first()
        if done is not None:
            return {**evaluation_dict(db, done), "last_attempt_failed": True, "last_attempt": _attempt_brief(attempt)}
    return {**evaluation_dict(db, ev), "last_attempt_failed": False}


@router.get("/conversations/{conversation_id}/evaluations")
def list_evaluations(conversation_id: str, db: Session = Depends(get_db)):
    evs = db.execute(select(Evaluation).where(Evaluation.conversation_id == conversation_id).order_by(Evaluation.id.desc())).scalars().all()
    return [evaluation_dict(db, ev, include_criteria=False) for ev in evs]


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, detail={"code": "not_found", "reason": "evaluation_not_found", "message": "Оценка не найдена"})
    return evaluation_dict(db, ev)


@router.get("/evaluations/{evaluation_id}/trace")
def get_trace(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, detail={"code": "not_found", "reason": "evaluation_not_found", "message": "Оценка не найдена"})
    return [trace_dict(t) for t in sorted(ev.traces, key=lambda t: t.id)]
