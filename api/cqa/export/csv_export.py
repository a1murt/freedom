"""CSV export of the latest completed evaluation per conversation.

Columns: conversation_id, channel, language, operator_name, <code>_answer…, <code>_score…,
<code>_applicable…, <code>_final…, total_score, final_total_score, critical_error, provider, model,
cost_usd, evaluated_at. Criterion codes come from the checklist version's criteria in order, so the
export works for any checklist. The text starts with a UTF-8 BOM so Excel opens Cyrillic correctly.
"""
from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics.service import criterion_rows, latest_evaluations
from ..models import ChecklistVersion, Criterion, Evaluation, EvaluationCriterion

BOM = "﻿"
NA = "N/A"
FIXED_HEAD = ["conversation_id", "channel", "language", "operator_name"]
FIXED_TAIL = ["total_score", "final_total_score", "critical_error", "provider", "model", "cost_usd", "evaluated_at"]


def criterion_codes(db: Session, checklist_version_id: int | None, evaluations: list[Evaluation]) -> list[str]:
    """Criterion codes in checklist order. Without a version filter, the versions used by the
    exported evaluations are merged (ascending version id); with no evaluations the active version is used."""
    if checklist_version_id is not None:
        version_ids = [checklist_version_id]
    else:
        version_ids = sorted({ev.checklist_version_id for ev in evaluations})
    if not version_ids:
        active = db.scalars(select(ChecklistVersion).where(ChecklistVersion.is_active.is_(True))).first()
        version_ids = [active.id] if active else []
    codes: list[str] = []
    for vid in version_ids:
        stmt = select(Criterion).where(Criterion.checklist_version_id == vid).order_by(Criterion.order, Criterion.id)
        for c in db.scalars(stmt):
            if c.code not in codes:
                codes.append(c.code)
    return codes


def header_for(codes: list[str]) -> list[str]:
    return (
        FIXED_HEAD
        + [f"{c}_answer" for c in codes]
        + [f"{c}_score" for c in codes]
        + [f"{c}_applicable" for c in codes]
        + [f"{c}_final" for c in codes]
        + FIXED_TAIL
    )


def _number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        f = float(value)
        return str(int(f)) if f.is_integer() else repr(f)
    return str(value)


def render_answer(answer: Any) -> str:
    """"yes" / "no" / number / "" (unknown). Not-applicable rows are handled by the caller."""
    if answer is None:
        return ""
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    if isinstance(answer, (int, float)):
        return _number(answer)
    text = str(answer).strip()
    low = text.lower()
    if low in {"yes", "да", "иә", "true"}:
        return "yes"
    if low in {"no", "нет", "жоқ", "false"}:
        return "no"
    try:
        return _number(float(text))
    except ValueError:
        return text


def _answer_cell(row: EvaluationCriterion | None) -> str:
    if row is None:
        return ""
    if not row.applicable:
        return NA
    return render_answer(row.answer)


def _score_cell(row: EvaluationCriterion | None) -> str:
    if row is None:
        return ""
    if not row.applicable:
        return NA
    return _number(row.score)


def _applicable_cell(row: EvaluationCriterion | None) -> str:
    if row is None:
        return ""
    return "true" if row.applicable else "false"


def _final_cell(row: EvaluationCriterion | None) -> str:
    if row is None:
        return ""
    if not row.applicable:
        return NA
    return render_answer(row.final_answer if row.final_answer is not None else row.answer)


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def evaluations_csv(db: Session, checklist_version_id: int | None = None) -> str:
    evaluations = latest_evaluations(db, checklist_version_id)
    codes = criterion_codes(db, checklist_version_id, evaluations)
    rows_by_eval = criterion_rows(db, evaluations)

    buf = io.StringIO()
    buf.write(BOM)
    writer = csv.writer(buf)
    writer.writerow(header_for(codes))
    for ev in evaluations:
        conv = ev.conversation
        by_code = {r.code: r for r in rows_by_eval.get(ev.id, [])}
        cells: list[str] = [
            ev.conversation_id,
            (conv.channel if conv is not None else "") or "",
            (conv.language if conv is not None else "") or "",
            (conv.operator_name if conv is not None else "") or "",
        ]
        cells += [_answer_cell(by_code.get(c)) for c in codes]
        cells += [_score_cell(by_code.get(c)) for c in codes]
        cells += [_applicable_cell(by_code.get(c)) for c in codes]
        cells += [_final_cell(by_code.get(c)) for c in codes]
        cells += [
            _number(ev.total_score),
            _number(ev.final_total_score),
            "true" if ev.critical_error else "false",
            ev.provider or "",
            ev.model or "",
            _number(round(float(ev.cost_usd or 0.0), 4)),
            _iso(ev.completed_at or ev.created_at),
        ]
        writer.writerow(cells)
    return buf.getvalue()
