"""Recording gaps of calls: the audio recording started after the conversation did (organizer answer 2026-11-08).

The start of such a call cannot be checked by listening. The judge credits C1 «Установление контакта» for it by rule
(cqa.judge.validate.opening_outside_recording: a deduction needs a fragment one can listen to and contest), and this
report lists the calls for the telephony owner: how many, how many seconds went unrecorded, whose dialogs, and which
criteria the rule decided. Turn numbers are 1-based, like evidence_turns in results.json.
"""
from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..judge.validate import CONTACT_OUTSIDE_RECORDING_FLAG, opening_outside_recording
from ..models import Conversation
from ..serializers import display_evaluation


def recording_gaps(db: Session, *, checklist_version_id: int | None = None, dataset_id: int | None = None) -> dict[str, Any]:
    q = select(Conversation).where(Conversation.channel == "voice call").options(selectinload(Conversation.turns)).order_by(Conversation.id)
    if dataset_id is not None:
        q = q.where(Conversation.dataset_id == dataset_id)
    calls = db.execute(q).scalars().all()
    items: list[dict[str, Any]] = []
    for conv in calls:
        from ..judge.runner import _to_norm_turns  # lazy: the runner imports the whole judge stack

        meta = conv.meta or {}
        not_recorded = [int(i) for i in (meta.get("not_in_recording") or [])]
        gap = opening_outside_recording(sorted(_to_norm_turns(conv), key=lambda t: t.idx))  # the rule's own definition
        if not not_recorded and gap is None:
            continue
        by_idx = {t.idx: t for t in conv.turns}
        operator_not_recorded = [i for i in not_recorded if by_idx.get(i) is not None and by_idx[i].speaker == "operator"]
        ev = display_evaluation(db, conv.id, checklist_version_id)
        credited = sorted(r.code for r in (ev.criteria if ev is not None else []) if CONTACT_OUTSIDE_RECORDING_FLAG in (r.flags or []))
        start = meta.get("recording_start_idx")
        items.append({
            "conversation_id": conv.id,
            "operator_name": conv.operator_name,
            "recording_start_turn": int(start) + 1 if start is not None else None,
            "seconds_not_recorded": meta.get("recording_start_offset_s"),
            "turns_not_recorded": len(not_recorded),
            "operator_turns_not_recorded": [i + 1 for i in operator_not_recorded],
            "opening_not_recorded": gap is not None,
            "opening_partially_recorded": bool(gap is not None and gap.partial),
            "opening_turn": gap.op.idx + 1 if gap is not None else None,
            "credited_by_rule": credited,
            "evaluation_id": ev.id if ev is not None else None,
        })
    seconds = [float(i["seconds_not_recorded"]) for i in items if isinstance(i["seconds_not_recorded"], (int, float))]
    return {
        "calls": len(calls),
        "with_gap": len(items),
        "share": round(len(items) / len(calls), 4) if calls else None,
        "opening_not_recorded": sum(1 for i in items if i["opening_not_recorded"]),
        "seconds_not_recorded": {"total": round(sum(seconds), 1), "mean": round(statistics.fmean(seconds), 1) if seconds else None,
                                 "max": round(max(seconds), 1) if seconds else None},
        "rule": ("Снижать балл можно только по фрагменту, который можно прослушать и оспорить: если первая реплика оператора "
                 "прозвучала до начала записи, «Установление контакта» засчитывается правилом, а звонок попадает в этот список."),
        "items": items,
    }
