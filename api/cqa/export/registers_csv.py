"""CSV registers for leads and reviewers (docs/USER_STORIES.md EXP-05): gross violations and appeal decisions.

Same data as `GET /api/violations` and `GET /api/appeals` (filters included), UTF-8 with BOM for Excel, numbers
with a dot, turn numbers 1-based (results.json `evidence_turns`), judge explanations with 1-based turn references.
People's own texts (appeal comments, reviewer reasons) are written as typed, as the API returns them.

Every text cell that a spreadsheet would read as a formula (it starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage
return) gets a leading apostrophe, so it is shown as text and never runs (CSV injection, OWASP): comments, names,
quotes and explanations come from people and transcripts.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Appeal, Criterion
from ..turnrefs import display_turn_number
from .csv_export import BOM

DEFAULT_WEB_BASE_URL = "https://cqa-freedom-pi.vercel.app"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _num(x: Any) -> str:
    return "" if x is None else (f"{float(x):.2f}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x))


def _cell(value: Any) -> Any:
    """A text cell that starts like a formula, prefixed with an apostrophe; any other value unchanged."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _row(values: list[Any]) -> list[Any]:
    return [_cell(v) for v in values]


def _card_url(conversation_id: str) -> str:
    base = (settings.web_base_url or DEFAULT_WEB_BASE_URL).rstrip("/")
    return f"{base}/#/conversations/{conversation_id}"


def violations_csv(db: Session, **filters: Any) -> str:
    from ..analytics.violations import violations

    data = violations(db, **filters)
    buf = io.StringIO()
    buf.write(BOM)
    w = csv.writer(buf)
    w.writerow(["conversation_id", "language", "channel", "operator_name", "violation_types", "violation_codes", "final_total_score",
                "total_score", "review_state", "confirmed_by_reviewer", "overturned_by_reviewer", "confidence", "turn", "timestamp",
                "quote", "explanation", "evaluated_at", "card_url"])
    for it in data["items"]:
        ev = (it.get("evidence") or [{}])[0] if it.get("evidence") else {}
        w.writerow(_row([it["conversation_id"], it.get("language") or "", it.get("channel") or "", it.get("operator_name") or "",
                         "; ".join(it.get("type_labels") or []), ";".join(it.get("types") or []), _num(it.get("final_total_score")),
                         _num(it.get("total_score")), it.get("review_state") or "", "true" if it.get("confirmed_by_reviewer") else "false",
                         "true" if it.get("overturned_by_reviewer") else "false", _num(it.get("confidence")),
                         display_turn_number(ev.get("turn_idx")) if ev.get("turn_idx") is not None else "", ev.get("timestamp") or "",
                         (ev.get("quote") or "").replace("\n", " "), (it.get("explanation") or "").replace("\n", " "),
                         it.get("evaluated_at") or "", _card_url(it["conversation_id"])]))
    return buf.getvalue()


def decisions_csv(db: Session, *, status: str | None = None, operator: str | None = None, author_role: str | None = None,
                  code: str | None = None, conversation_id: str | None = None, language: str | None = None, channel: str | None = None,
                  q: str | None = None) -> str:
    """One row per appeal with its decision. Filters as in GET /api/appeals: exact except ``q`` (substring of the comment,
    conversation id, operator or author name, case-insensitive) and the case-insensitive ``code`` and ``language``."""
    stmt = select(Appeal).options(selectinload(Appeal.decision)).order_by(Appeal.id)
    if status:
        stmt = stmt.where(Appeal.status == status)
    if author_role:
        stmt = stmt.where(Appeal.author_role == author_role)
    needle = q.strip().lower() if q and q.strip() else None
    titles: dict[tuple[int, str], str] = {}
    buf = io.StringIO()
    buf.write(BOM)
    w = csv.writer(buf)
    w.writerow(["appeal_id", "status", "conversation_id", "language", "channel", "operator_name", "criterion", "criterion_title",
                "ai_answer", "final_answer", "review_state", "author_role", "author", "comment", "filed_at", "decision", "new_answer",
                "new_score", "reviewer_role", "reviewer", "reason", "decided_at", "total_score", "final_total_score", "evaluation_id",
                "card_url"])
    for ap in db.execute(stmt).scalars():
        ec = ap.evaluation_criterion
        ev = ec.evaluation
        conv = ev.conversation
        if operator and (conv.operator_name or "") != operator:
            continue
        if code and ec.code.upper() != code.upper():
            continue
        if conversation_id and conv.id != conversation_id:
            continue
        if language and (conv.language or "").upper() != language.upper():
            continue
        if channel and conv.channel != channel:
            continue
        if needle and not any(needle in str(v or "").lower() for v in (ap.comment, conv.id, conv.operator_name, ap.author_name, ap.author_label)):
            continue
        key = (ev.checklist_version_id, ec.code)
        if key not in titles:
            cdef = db.execute(select(Criterion).where(Criterion.checklist_version_id == ev.checklist_version_id,
                                                      Criterion.code == ec.code)).scalar_one_or_none()
            titles[key] = cdef.title if cdef else ""
        d = ap.decision
        w.writerow(_row([ap.id, ap.status, conv.id, conv.language or "", conv.channel or "", conv.operator_name or "", ec.code, titles[key],
                         ec.answer if ec.applicable else "N/A", ec.final_answer if ec.final_answer is not None else "", ec.review_state or "",
                         ap.author_role or "", ap.author_name or ap.author_label or "", (ap.comment or "").replace("\n", " "),
                         ap.created_at.isoformat() if ap.created_at else "", d.action if d else "",
                         (d.new_answer if d and d.new_answer is not None else ""), _num(d.new_score) if d else "",
                         (d.reviewer_role or "") if d else "", (d.reviewer_label or "") if d else "",
                         (d.reason or "").replace("\n", " ") if d else "", d.created_at.isoformat() if d and d.created_at else "",
                         _num(ev.total_score), _num(ev.final_total_score), ev.id, _card_url(conv.id)]))
    return buf.getvalue()
