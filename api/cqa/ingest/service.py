"""Persist organizer records as conversations + normalized turns.

Re-import rules (docs/USER_STORIES.md CONV-02, CONV-06):

- A record is compared with the stored conversation by a fingerprint of what the judge reads: channel, language and
  the normalized turns (raw timestamp, speaker, text). Signed audio links change on every organizer fetch, so the
  audio URL is not part of it (it is refreshed on every import).
- ``unchanged``: turns are left as they are (ids, evaluations and evidence stay valid); only the audio link is refreshed.
- ``updated``: a changed record replaces the turns (``on_conflict="replace"``) and the conversation is flagged with
  ``meta.transcript_changed_at``, so evaluations made before that time can be shown as stale.
- ``on_conflict``: ``replace`` (organizer import: the organizer is the source of truth for its ids), ``error`` (paste
  and upload default: never overwrite by accident, the router answers 409 with a free ``suggested_id``), ``rename``
  (store the record under the next free id ``<id>-v2``, ``-v3`` …) or ``skip``.
- A conversation that is being evaluated right now is never rewritten (``busy``).
- A lead's manual operator attribution (``meta.operator_name_source == "manual"``) survives every re-import.
- An organizer import reuses the dataset row of the same (group_id, source) and claims its conversations; uploads and
  pastes never move an existing conversation out of its dataset (results.json selects by the organizer group).
- The Dynamics 365 push (``upsert_record``) follows the same rules with ``on_conflict="replace"`` and claims its dataset.
- An id repeated within one import is a re-import of its earlier copy (identical: ``unchanged``, different: on_conflict).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Conversation, Dataset, Evaluation, Turn
from .normalize import normalize

ON_CONFLICT = ("replace", "error", "rename", "skip")
RENAME_LIMIT = 50


class ConversationExists(Exception):
    """A record with this id exists with different content and on_conflict is "error"."""

    def __init__(self, conversation_id: str, suggested_id: str):
        super().__init__(f"conversation {conversation_id} exists")
        self.conversation_id = conversation_id
        self.suggested_id = suggested_id


class ConversationBusy(Exception):
    """The conversation is being evaluated; its transcript cannot be rewritten now."""

    def __init__(self, conversation_id: str):
        super().__init__(f"conversation {conversation_id} is being evaluated")
        self.conversation_id = conversation_id


@dataclass
class IngestResult:
    conversation: Conversation | None
    status: str  # created | updated | unchanged | renamed | skipped | busy
    requested_id: str
    conversation_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _fingerprint(channel: str | None, language: str | None, turns: list[tuple[Any, Any, Any]]) -> str:
    payload = {"channel": channel or "agent chat", "language": (language or "RU").upper(),
               "turns": [[str(ts or ""), str(sp or ""), str(tx or "")] for ts, sp, tx in turns]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def record_fingerprint(rec: dict[str, Any], norm=None) -> str:
    norm = norm or normalize(rec)
    return _fingerprint(rec.get("channel"), rec.get("language"), [(t.timestamp_raw, t.speaker, t.text) for t in norm.turns])


def stored_fingerprint(conv: Conversation) -> str:
    turns = sorted(conv.turns, key=lambda t: t.idx)
    return _fingerprint(conv.channel, conv.language, [(t.timestamp_raw, t.speaker, t.text) for t in turns])


def is_busy(db: Session, conversation_id: str) -> bool:
    q = select(Evaluation.id).where(Evaluation.conversation_id == conversation_id, Evaluation.status.in_(("running", "queued")))
    return db.execute(q.limit(1)).first() is not None


def free_id(db: Session, base: str, taken: set[str] | None = None) -> str:
    taken = taken or set()
    for n in range(2, RENAME_LIMIT + 2):
        candidate = f"{base}-v{n}"
        if candidate not in taken and db.get(Conversation, candidate) is None:
            return candidate
    raise ValueError(f"no free id for {base}")


def _write(conv: Conversation, rec: dict[str, Any], norm, fingerprint: str) -> None:
    conv.channel = rec.get("channel") or "agent chat"
    conv.language = (rec.get("language") or "RU").upper()
    conv.audio_url = rec.get("audio_url") or None
    conv.operator_name = norm.operator_name
    conv.turn_count = len(norm.turns)
    conv.first_response_seconds = norm.first_response_seconds
    conv.terminal_event = norm.terminal_event
    conv.meta = {**norm.meta, "operator_start_idx": norm.operator_start_idx, "content_hash": fingerprint}
    conv.turns = [Turn(idx=t.idx, timestamp_raw=t.timestamp_raw, offset_seconds=t.offset_seconds, speaker=t.speaker, text=t.text,
                       is_duplicate=t.is_duplicate, segment=t.segment) for t in norm.turns]


def upsert_record(db: Session, rec: dict[str, Any], dataset_id: int | None = None) -> Conversation:
    """Dynamics 365 push: the re-import rules with on_conflict="replace", claiming the conversation for ``dataset_id``.
    An unchanged transcript keeps its turns, meta and a lead's manual operator attribution (only the audio link and the
    dataset are refreshed); a changed one replaces the turns and sets meta.transcript_changed_at, keeping the manual
    attribution. Raises ConversationBusy while the conversation is being evaluated."""
    res = ingest_record(db, rec, dataset_id=dataset_id, on_conflict="replace", claim_dataset=True)
    assert res.conversation is not None  # replace always returns the stored conversation
    return res.conversation


def ingest_record(db: Session, rec: dict[str, Any], *, dataset_id: int | None = None, on_conflict: str = "error",
                  claim_dataset: bool = False, taken: set[str] | None = None) -> IngestResult:
    """Create, keep, replace, rename or skip one record (see the module docstring). Raises ConversationExists for
    on_conflict="error" and ConversationBusy for a replace while the conversation is being evaluated."""
    if on_conflict not in ON_CONFLICT:
        raise ValueError(f"on_conflict must be one of {ON_CONFLICT}")
    cid = str(rec.get("conversation_id") or rec.get("id") or "").strip()
    if not cid:
        raise ValueError("record without conversation_id")
    norm = normalize(rec)
    fp = record_fingerprint(rec, norm)
    conv = db.get(Conversation, cid)
    if conv is None:
        conv = Conversation(id=cid, dataset_id=dataset_id)
        db.add(conv)
        _write(conv, rec, norm, fp)
        return IngestResult(conv, "created", cid, cid)
    stored = (conv.meta or {}).get("content_hash") or stored_fingerprint(conv)
    if stored == fp:
        if rec.get("audio_url"):
            conv.audio_url = rec.get("audio_url")
        if claim_dataset and dataset_id is not None:
            conv.dataset_id = dataset_id
        elif conv.dataset_id is None:
            conv.dataset_id = dataset_id
        if (conv.meta or {}).get("content_hash") != fp:
            conv.meta = {**(conv.meta or {}), "content_hash": fp}
        return IngestResult(conv, "unchanged", cid, cid)
    if on_conflict == "error":
        raise ConversationExists(cid, free_id(db, cid, taken))
    if on_conflict == "skip":
        return IngestResult(conv, "skipped", cid, cid)
    if on_conflict == "rename":
        new_id = free_id(db, cid, taken)
        copy = Conversation(id=new_id, dataset_id=dataset_id)
        db.add(copy)
        _write(copy, {**rec, "conversation_id": new_id}, norm, fp)
        copy.meta = {**(copy.meta or {}), "copied_from": cid}
        return IngestResult(copy, "renamed", cid, new_id)
    # replace
    if is_busy(db, cid):
        raise ConversationBusy(cid)
    previous_turns = conv.turn_count
    for t in list(conv.turns):
        db.delete(t)
    db.flush()
    if claim_dataset and dataset_id is not None:
        conv.dataset_id = dataset_id
    elif conv.dataset_id is None:
        conv.dataset_id = dataset_id
    manual = (conv.meta or {}).get("operator_name_source") == "manual"
    manual_name = conv.operator_name if manual else None
    _write(conv, rec, norm, fp)
    conv.meta = {**(conv.meta or {}), "transcript_changed_at": datetime.now(UTC).isoformat(), "previous_turn_count": previous_turns}
    if manual:  # a lead's manual attribution survives a changed transcript; the new detection is kept next to it
        conv.meta = {**conv.meta, "operator_name_source": "manual", "operator_name_detected": conv.operator_name}
        conv.operator_name = manual_name
    return IngestResult(conv, "updated", cid, cid)


def _dataset_for(db: Session, group_id: str, source: str, record_count: int, reuse: bool) -> Dataset:
    ds = None
    if reuse:
        ds = db.execute(select(Dataset).where(Dataset.group_id == group_id, Dataset.source == source).order_by(Dataset.id.desc())).scalars().first()
    if ds is None:
        ds = Dataset(group_id=group_id, source=source, record_count=record_count)
        db.add(ds)
    else:
        ds.record_count = record_count
        ds.imported_at = datetime.now(UTC)
    db.flush()
    return ds


def import_records(db: Session, records: list[dict[str, Any]], group_id: str, source: str = "organizer_api", *,
                   on_conflict: str | None = None) -> dict[str, Any]:
    """Import a set. Organizer imports replace changed records and claim them; uploads default to "error" (raises
    ConversationExists for the first conflicting record before anything is written). An id repeated in the set is a
    re-import of its earlier copy: identical copies are ``unchanged``, a different one follows on_conflict (with "error"
    it is a conflict too). ``conversation_ids`` and the language/channel counts list every stored conversation once."""
    organizer = source == "organizer_api"
    on_conflict = on_conflict or ("replace" if organizer else "error")
    if on_conflict == "error":  # all-or-nothing: report the first conflict before writing anything
        taken: set[str] = set()
        first: dict[str, dict[str, Any]] = {}  # the set's first copy of every id
        for rec in records:
            cid = str(rec.get("conversation_id") or rec.get("id") or "").strip()
            conv = db.get(Conversation, cid) if cid else None
            if conv is not None and ((conv.meta or {}).get("content_hash") or stored_fingerprint(conv)) != record_fingerprint(rec):
                raise ConversationExists(cid, free_id(db, cid, taken))
            if cid and cid in first and record_fingerprint(first[cid]) != record_fingerprint(rec):
                raise ConversationExists(cid, free_id(db, cid, taken))
            first.setdefault(cid, rec)
            taken.add(cid)
    ds = _dataset_for(db, group_id, source, len(records), reuse=True)
    by_lang: dict[str, int] = {}
    by_chan: dict[str, int] = {}
    statuses: dict[str, int] = {"created": 0, "updated": 0, "unchanged": 0, "renamed": 0, "skipped": 0, "busy": 0}
    stored: dict[str, Conversation] = {}  # conversations written or confirmed by this set, once each, in order
    items: list[dict[str, Any]] = []
    taken = set()
    for rec in records:
        cid = str(rec.get("conversation_id") or rec.get("id") or "").strip()
        if cid in taken:  # autoflush is off: the earlier copy is still pending, and db.get() in ingest_record would miss it
            db.flush()
        try:
            res = ingest_record(db, rec, dataset_id=ds.id, on_conflict=on_conflict, claim_dataset=organizer, taken=taken)
        except ConversationBusy as e:
            res = IngestResult(None, "busy", e.conversation_id, e.conversation_id)
        statuses[res.status] = statuses.get(res.status, 0) + 1
        if res.conversation_id:
            taken.add(res.conversation_id)
        items.append({"requested_id": res.requested_id, "conversation_id": res.conversation_id, "status": res.status})
        conv = res.conversation
        if conv is not None and res.status != "skipped":
            stored.setdefault(conv.id, conv)
    for conv in stored.values():  # after the loop: a repeated id counts once, with its final language and channel
        by_lang[conv.language] = by_lang.get(conv.language, 0) + 1
        by_chan[conv.channel] = by_chan.get(conv.channel, 0) + 1
    db.commit()
    return {"dataset_id": ds.id, "group_id": group_id, "imported": len(records), **statuses, "by_language": by_lang,
            "by_channel": by_chan, "conversation_ids": list(stored), "items": items}


def list_conversation_ids(db: Session) -> list[str]:
    return list(db.execute(select(Conversation.id)).scalars())
