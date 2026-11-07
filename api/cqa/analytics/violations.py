"""Gross violations (criterion C8 / export id "critical"): which dialogs, which type, why.

The checklist defines three kinds of gross error. The judge's C8 explanation and flags describe which one it
found; this module classifies them so the UI can list and filter violations by type (organizer live ask:
"show all dialogs with gross errors and explain why they were classified so").
"""

from __future__ import annotations

import re
from typing import Any

VIOLATION_TYPES: dict[str, dict[str, str]] = {
    "insult": {"ru": "Оскорбление клиента", "en": "Insulting the client", "kk": "Клиентті қорлау"},
    "paid_bypass": {"ru": "Платная услуга в обход компании", "en": "Paid service bypassing the company", "kk": "Компанияны айналып өтетін ақылы қызмет"},
    "disclosure": {"ru": "Раскрытие конфиденциальной информации", "en": "Disclosure of confidential information", "kk": "Құпия ақпаратты жария ету"},
    "other": {"ru": "Иное грубое нарушение", "en": "Other gross violation", "kk": "Басқа өрескел бұзушылық"},
}

_PATTERNS: dict[str, re.Pattern[str]] = {
    "insult": re.compile(r"оскорб|унижа|униз|insult|сарказм|читать\s+умеете|rude|грубост[ьи]\s+в\s+адрес|қорла", re.I),
    "paid_bypass": re.compile(
        r"платн|за\s+плату|в\s+обход|на\s+(?:личн\w*\s+)?карт|личн\w*\s+(?:номер|телефон|канал|карт)|частн\w*\s+(?:порядк|консульт)|"
        r"paid|private\s+advice|personal\s+(?:number|phone|card)|bypass|ақылы|жеке\s+нөмір",
        re.I,
    ),
    "disclosure": re.compile(
        r"раскры|конфиденц|без\s+(?:подтвержд|идентифик|провер)|до\s+идентифик|disclos|confidential|without\s+(?:identification|verification)|"
        r"жария|растаусыз",
        re.I,
    ),
}


def classify(explanation: str | None, flags: list[str] | None = None, evidence_quotes: list[str] | None = None, c3_answer: Any = None) -> list[str]:
    """Return one or more violation types for a C8 = Да verdict (ordered by checklist order)."""
    text = " ".join([explanation or "", " ".join(flags or []), " ".join(evidence_quotes or [])])
    found = [k for k in ("insult", "paid_bypass", "disclosure") if _PATTERNS[k].search(text)]
    flag_text = " ".join(flags or []).lower()
    if "insult" in flag_text and "insult" not in found:
        found.append("insult")
    if not found and str(c3_answer).lower() == "no":
        found.append("disclosure")  # account data shown without identification is the typical disclosure case
    return found or ["other"]


def label(kind: str, lang: str = "ru") -> str:
    return VIOLATION_TYPES.get(kind, VIOLATION_TYPES["other"]).get(lang, VIOLATION_TYPES[kind]["ru"] if kind in VIOLATION_TYPES else "")


def violations(db, *, checklist_version_id: int | None = None, kind: str | None = None, language: str | None = None,
               channel: str | None = None, operator: str | None = None, include_reviewed_out: bool = False) -> dict[str, Any]:
    """Latest evaluation per conversation with C8 = Да (after reviewer decisions unless include_reviewed_out)."""
    from .service import latest_evaluations

    items: list[dict[str, Any]] = []
    counts = {k: 0 for k in VIOLATION_TYPES}
    for ev in latest_evaluations(db, checklist_version_id):
        conv = ev.conversation
        if language and conv.language != language.upper():
            continue
        if channel and conv.channel != channel:
            continue
        if operator and conv.operator_name != operator:
            continue
        rows = {r.code: r for r in ev.criteria}
        c8 = rows.get("C8")
        if c8 is None:
            continue
        ai_yes = c8.applicable and str(c8.answer).lower() == "yes"
        final = c8.final_answer if c8.final_answer is not None else c8.answer
        final_yes = str(final).lower() == "yes"
        if not (final_yes or (include_reviewed_out and ai_yes)):
            continue
        quotes = [e.get("quote", "") for e in (c8.evidence or []) if isinstance(e, dict)]
        kinds = classify(c8.explanation, c8.flags, quotes, rows.get("C3").answer if rows.get("C3") else None)
        if kind and kind not in kinds:
            continue
        for k in kinds:
            counts[k] += 1
        turns = {t.idx: t for t in conv.turns}
        evidence = []
        for e in c8.evidence or []:
            if not isinstance(e, dict):
                continue
            t = turns.get(e.get("turn_idx"))
            evidence.append({"turn_idx": e.get("turn_idx"), "speaker": e.get("speaker") or (t.speaker if t else None), "quote": e.get("quote"),
                             "timestamp": t.timestamp_raw if t else None, "offset_seconds": t.offset_seconds if t else None})
        items.append({
            "conversation_id": conv.id, "channel": conv.channel, "language": conv.language, "operator_name": conv.operator_name,
            "evaluation_id": ev.id, "evaluation_criterion_id": c8.id, "total_score": ev.total_score, "final_total_score": ev.final_total_score,
            "types": kinds, "type_labels": [label(k) for k in kinds], "explanation": c8.explanation, "confidence": c8.confidence,
            "evidence": evidence, "review_state": c8.review_state, "ai_answer": c8.answer, "final_answer": final,
            "confirmed_by_reviewer": c8.review_state == "confirmed", "overturned_by_reviewer": ai_yes and not final_yes,
            "evaluated_at": ev.completed_at.isoformat() if ev.completed_at else None,
        })
    items.sort(key=lambda x: (x["final_total_score"] is None, x["final_total_score"] if x["final_total_score"] is not None else 0))
    return {"total": len(items), "counts": counts, "types": [{"code": k, **v} for k, v in VIOLATION_TYPES.items()], "items": items}


def operators(db, *, checklist_version_id: int | None = None) -> list[dict[str, Any]]:
    """Operators found in the data with their evaluation stats (operator portal and coaching)."""
    from sqlalchemy import select

    from ..models import Appeal, Conversation, EvaluationCriterion
    from .service import latest_evaluations

    by_op: dict[str, dict[str, Any]] = {}
    for conv in db.execute(select(Conversation)).scalars():
        name = conv.operator_name or "—"
        o = by_op.setdefault(name, {"operator_name": name, "conversations": 0, "evaluated": 0, "totals": [], "critical_count": 0,
                                    "languages": set(), "channels": set(), "open_appeals": 0, "last_evaluated_at": None})
        o["conversations"] += 1
        o["languages"].add(conv.language)
        o["channels"].add(conv.channel)
    for ev in latest_evaluations(db, checklist_version_id):
        name = ev.conversation.operator_name or "—"
        o = by_op.get(name)
        if o is None:
            continue
        o["evaluated"] += 1
        if ev.final_total_score is not None:
            o["totals"].append(ev.final_total_score)
        if ev.critical_error:
            o["critical_count"] += 1
        ts = ev.completed_at.isoformat() if ev.completed_at else None
        if ts and (o["last_evaluated_at"] is None or ts > o["last_evaluated_at"]):
            o["last_evaluated_at"] = ts
    open_rows = db.execute(select(Appeal).where(Appeal.status == "open")).scalars().all()
    for ap in open_rows:
        ec: EvaluationCriterion = ap.evaluation_criterion
        name = ec.evaluation.conversation.operator_name or "—"
        if name in by_op:
            by_op[name]["open_appeals"] += 1
    out = []
    for o in by_op.values():
        totals = o.pop("totals")
        o["mean_total"] = round(sum(totals) / len(totals), 2) if totals else None
        o["languages"] = sorted(o["languages"])
        o["channels"] = sorted(o["channels"])
        out.append(o)
    out.sort(key=lambda x: (x["operator_name"] == "—", x["operator_name"]))
    return out
