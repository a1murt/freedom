"""Persist organizer records as conversations + normalized turns."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Conversation, Dataset, Turn
from .normalize import normalize


def upsert_record(db: Session, rec: dict[str, Any], dataset_id: int | None = None) -> Conversation:
    cid = str(rec.get("conversation_id") or rec.get("id"))
    norm = normalize(rec)
    conv = db.get(Conversation, cid)
    if conv is None:
        conv = Conversation(id=cid)
        db.add(conv)
    else:
        for t in list(conv.turns):
            db.delete(t)
        db.flush()
    conv.dataset_id = dataset_id if dataset_id is not None else conv.dataset_id
    conv.channel = rec.get("channel") or "agent chat"
    conv.language = (rec.get("language") or "RU").upper()
    conv.audio_url = rec.get("audio_url") or None
    conv.operator_name = norm.operator_name
    conv.turn_count = len(norm.turns)
    conv.first_response_seconds = norm.first_response_seconds
    conv.terminal_event = norm.terminal_event
    conv.meta = {**norm.meta, "operator_start_idx": norm.operator_start_idx}
    conv.turns = [Turn(idx=t.idx, timestamp_raw=t.timestamp_raw, offset_seconds=t.offset_seconds, speaker=t.speaker, text=t.text,
                       is_duplicate=t.is_duplicate, segment=t.segment) for t in norm.turns]
    return conv


def import_records(db: Session, records: list[dict[str, Any]], group_id: str, source: str = "organizer_api") -> dict[str, Any]:
    ds = Dataset(group_id=group_id, source=source, record_count=len(records))
    db.add(ds)
    db.flush()
    by_lang: dict[str, int] = {}
    by_chan: dict[str, int] = {}
    for rec in records:
        conv = upsert_record(db, rec, ds.id)
        by_lang[conv.language] = by_lang.get(conv.language, 0) + 1
        by_chan[conv.channel] = by_chan.get(conv.channel, 0) + 1
    db.commit()
    return {"dataset_id": ds.id, "group_id": group_id, "imported": len(records), "by_language": by_lang, "by_channel": by_chan}


def list_conversation_ids(db: Session) -> list[str]:
    return list(db.execute(select(Conversation.id)).scalars())
