from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..ingest.adapter import IngestError, fetch_organizer, record_from_text, records_from_payload
from ..ingest.service import import_records, upsert_record
from ..models import Conversation, Evaluation
from ..serializers import conversation_detail, conversation_summary, evaluation_dict, latest_evaluation, trace_dict

router = APIRouter(prefix="/api", tags=["conversations"])


class ImportBody(BaseModel):
    group_id: str


class PasteBody(BaseModel):
    conversation_id: str | None = None
    channel: str = "agent chat"
    language: str = "RU"
    text: str | None = None
    transcript: list[dict[str, Any]] | None = None
    audio_url: str | None = None


@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db)):
    """Imported datasets (one row per import) with how many conversations currently belong to each."""
    from sqlalchemy import func

    from ..models import Dataset

    counts = dict(db.execute(select(Conversation.dataset_id, func.count(Conversation.id)).group_by(Conversation.dataset_id)).all())
    rows = db.execute(select(Dataset).order_by(Dataset.id)).scalars().all()
    return [{"id": d.id, "group_id": d.group_id, "source": d.source, "imported_at": d.imported_at.isoformat() if d.imported_at else None,
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
async def upload_conversations(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    name = (file.filename or "upload").lower()
    try:
        if name.endswith(".json"):
            records = records_from_payload(json.loads(raw.decode("utf-8")))
            res = import_records(db, records, group_id=f"upload:{file.filename}", source="upload")
            ids = [str(r.get("conversation_id")) for r in records]
            return {**res, "conversation_ids": ids}
        text = raw.decode("utf-8")
        cid = name.rsplit(".", 1)[0][:60]
        rec = record_from_text(text, conversation_id=cid)
        conv = upsert_record(db, rec)
        db.commit()
        return conversation_detail(db, conv)
    except (IngestError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail={"code": "bad_upload", "message": str(e)})


@router.post("/conversations/paste")
def paste_conversation(body: PasteBody, db: Session = Depends(get_db)):
    import secrets

    cid = body.conversation_id or f"manual-{secrets.token_hex(3)}"
    try:
        if body.transcript:
            rec = {"conversation_id": cid, "channel": body.channel, "language": body.language, "transcript": body.transcript, "audio_url": body.audio_url}
        elif body.text:
            rec = record_from_text(body.text, conversation_id=cid, channel=body.channel, language=body.language)
        else:
            raise IngestError("Provide 'text' or 'transcript'")
    except IngestError as e:
        raise HTTPException(status_code=400, detail={"code": "bad_transcript", "message": str(e)})
    conv = upsert_record(db, rec)
    db.commit()
    db.refresh(conv)
    return conversation_detail(db, conv)


@router.get("/conversations")
def list_conversations(channel: str | None = None, language: str | None = None, status: str | None = None, min_total: float | None = None,
                       max_total: float | None = None, critical: bool | None = None, q: str | None = None, dataset_id: int | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    stmt = select(Conversation).order_by(Conversation.imported_at, Conversation.id)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if language:
        stmt = stmt.where(Conversation.language == language.upper())
    if dataset_id:
        stmt = stmt.where(Conversation.dataset_id == dataset_id)
    convs = db.execute(stmt).scalars().all()
    # latest evaluations in one query
    evs = db.execute(select(Evaluation).where(Evaluation.status.in_(["completed", "failed"])).order_by(Evaluation.id).options(selectinload(Evaluation.criteria))).scalars().all()
    latest: dict[str, Evaluation] = {}
    for ev in evs:
        latest[ev.conversation_id] = ev
    items = []
    for c in convs:
        ev = latest.get(c.id)
        s = conversation_summary(db, c, ev)
        if status == "evaluated" and ev is None:
            continue
        if status == "not_evaluated" and ev is not None:
            continue
        if status == "under_review" and s["review_state"] != "under_review":
            continue
        if critical is not None and bool(ev and ev.critical_error) != critical:
            continue
        total = ev.final_total_score if ev else None
        if min_total is not None and (total is None or total < min_total):
            continue
        if max_total is not None and (total is None or total > max_total):
            continue
        if q:
            ql = q.lower()
            hay = f"{c.id} {c.operator_name or ''} " + " ".join(t.text for t in c.turns)
            if ql not in hay.lower():
                continue
        items.append(s)
    total_n = len(items)
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": total_n}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    c = db.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "conversation not found"})
    return conversation_detail(db, c)


@router.get("/conversations/{conversation_id}/evaluation")
def get_latest_evaluation(conversation_id: str, checklist_version_id: int | None = None, db: Session = Depends(get_db)):
    ev = latest_evaluation(db, conversation_id, checklist_version_id)
    if ev is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "no evaluation yet"})
    return evaluation_dict(db, ev)


@router.get("/conversations/{conversation_id}/evaluations")
def list_evaluations(conversation_id: str, db: Session = Depends(get_db)):
    evs = db.execute(select(Evaluation).where(Evaluation.conversation_id == conversation_id).order_by(Evaluation.id.desc())).scalars().all()
    return [evaluation_dict(db, ev, include_criteria=False) for ev in evs]


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "evaluation not found"})
    return evaluation_dict(db, ev)


@router.get("/evaluations/{evaluation_id}/trace")
def get_trace(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "evaluation not found"})
    return [trace_dict(t) for t in sorted(ev.traces, key=lambda t: t.id)]
