"""Operator scorecard and coaching points: ``GET /api/operators/{name}`` (see docs/arch/overview.md).

Same definitions as ``cqa.analytics.overview``: the latest completed evaluation per conversation, final (post-review)
values, lost points recomputed by the scoring engine from the final answers. On top of the operator's own figures the
scorecard carries the team figures (every operator in scope) for comparison, and picks:

* ``coaching_points``: the two weighted criteria where the operator lost the most points (ties: the lower pass rate
  first), each with 1-3 representative examples: the dialogs with the largest loss on that criterion, preferring
  verdicts a reviewer did not overturn, with the explanation, the conversation id and evidence quotes;
* ``strengths``: the two best weighted criteria among the rest (pass rate / mean ratio, then the larger weight).

Critical criteria (C8 «Грубые ошибки») are never strengths or coaching points: gross violations are listed under
``violations`` (the same items as ``GET /api/violations?operator=``). The operator name "—" addresses the conversations
without a detected operator name, as in ``GET /api/operators``. Read-only.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Appeal, Conversation, Criterion, Evaluation, EvaluationCriterion
from ..serializers import appeal_dict, review_state_of
from .overview import (
    EPS,
    NA,
    UNKNOWN,
    EvalView,
    appeal_funnel,
    as_utc,
    criteria_stats,
    example_items,
    example_rank,
    iso,
    load_views,
    mean,
    ordered_codes,
    rnd,
    scope_definitions,
)
from .violations import VIOLATION_TYPES, violations

STRENGTHS = 2
COACHING_POINTS = 2
EXAMPLES_PER_POINT = 3


class OperatorNotFound(LookupError):
    """No conversation carries this operator name."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def resolve_operator(db: Session, name: str) -> tuple[str, list[Conversation]]:
    """Stored operator name and conversations for a requested name (exact, then case/space-insensitive)."""
    wanted = (name or "").strip()
    if not wanted:
        raise OperatorNotFound(name)
    if wanted == UNKNOWN:
        stmt = select(Conversation).where(or_(Conversation.operator_name.is_(None), func.trim(Conversation.operator_name) == ""))
        conversations = list(db.scalars(stmt.order_by(Conversation.id)))
        if not conversations:
            raise OperatorNotFound(name)
        return UNKNOWN, conversations
    conversations = list(db.scalars(select(Conversation).where(Conversation.operator_name == wanted).order_by(Conversation.id)))
    if conversations:
        return wanted, conversations
    folded = wanted.casefold()
    names = sorted({n for n in db.scalars(select(Conversation.operator_name).distinct()) if n and n.strip().casefold() == folded})
    if not names:
        raise OperatorNotFound(name)
    stored = names[0]
    return stored, list(db.scalars(select(Conversation).where(Conversation.operator_name == stored).order_by(Conversation.id)))


def _value(c: dict[str, Any]) -> float:
    value = c["mean_ratio"] if c["answer_type"] == "scale" else c["pass_rate"]
    return float(value) if value is not None else 0.0


def _conversation_items(db: Session, conversations: Sequence[Conversation], mine: Sequence[EvalView],
                        checklist_version_id: int | None) -> list[dict[str, Any]]:
    by_conv = {v.ev.conversation_id: v for v in mine}
    failed_stmt = select(Evaluation.conversation_id).where(Evaluation.status == "failed",
                                                          Evaluation.conversation_id.in_(sorted(c.id for c in conversations)))
    if checklist_version_id is not None:
        failed_stmt = failed_stmt.where(Evaluation.checklist_version_id == checklist_version_id)
    failed = set(db.scalars(failed_stmt))
    evaluated: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for c in conversations:
        v = by_conv.get(c.id)
        if v is not None:
            evaluated.append({
                "id": c.id, "channel": c.channel, "language": c.language, "status": "evaluated", "evaluation_id": v.ev.id,
                "total_score": rnd(v.ev.total_score), "final_total_score": rnd(v.total), "critical_error": v.critical,
                "evaluated_at": iso(v.ev.completed_at), "review_state": review_state_of(v.ev),
            })
        else:
            rest.append({
                "id": c.id, "channel": c.channel, "language": c.language, "status": "failed" if c.id in failed else "not_evaluated",
                "evaluation_id": None, "total_score": None, "final_total_score": None, "critical_error": False,
                "evaluated_at": None, "review_state": "none",
            })
    evaluated.sort(key=lambda i: (i["evaluated_at"] or "", i["id"]), reverse=True)  # newest first
    return evaluated + rest


def _operator_violations(db: Session, operator_name: str, checklist_version_id: int | None) -> dict[str, Any]:
    if operator_name != UNKNOWN:
        return violations(db, checklist_version_id=checklist_version_id, operator=operator_name)
    listed = violations(db, checklist_version_id=checklist_version_id)
    items = [i for i in listed["items"] if not (i.get("operator_name") or "").strip()]
    counts = {k: 0 for k in VIOLATION_TYPES}
    for item in items:
        for kind in item.get("types") or []:
            counts[kind] = counts.get(kind, 0) + 1
    return {"total": len(items), "counts": counts, "types": listed["types"], "items": items}


def _operator_appeals(db: Session, conversation_ids: set[str], checklist_version_id: int | None,
                      definitions: dict[str, Criterion], current_ids: set[int], now: datetime) -> dict[str, Any]:
    stmt = (
        select(Appeal, EvaluationCriterion, Evaluation)
        .join(EvaluationCriterion, Appeal.evaluation_criterion_id == EvaluationCriterion.id)
        .join(Evaluation, EvaluationCriterion.evaluation_id == Evaluation.id)
        .where(Evaluation.conversation_id.in_(sorted(conversation_ids)))
        .options(selectinload(Appeal.decision))
    )
    if checklist_version_id is not None:
        stmt = stmt.where(Evaluation.checklist_version_id == checklist_version_id)
    rows = db.execute(stmt).all()
    items = []
    for ap, ec, ev in rows:
        d = definitions.get(ec.code)
        item = appeal_dict(ap)
        item.update({
            "conversation_id": ev.conversation_id,
            "evaluation_id": ev.id,
            "code": ec.code,
            "title": d.title if d is not None else ec.code,
            "ai_answer": ec.answer if ec.applicable else NA,
            "final_answer": ec.final_answer if ec.final_answer is not None else (ec.answer if ec.applicable else NA),
            "review_state": ec.review_state,
            "current_evaluation": ev.id in current_ids,
        })
        items.append(item)
    items.sort(key=lambda a: (a["status"] != "open", -a["id"]))
    return {**appeal_funnel([ap for ap, _, _ in rows], now), "items": items}


def operator_scorecard(db: Session, name: str, checklist_version_id: int | None = None, *,
                       now: datetime | None = None) -> dict[str, Any]:
    """Scorecard of one operator; raises OperatorNotFound when no conversation carries the name."""
    now = as_utc(now) or datetime.now(UTC)
    operator_name, conversations = resolve_operator(db, name)
    conversation_ids = {c.id for c in conversations}
    views = load_views(db, checklist_version_id)  # the whole team: comparison figures
    mine = [v for v in views if v.ev.conversation_id in conversation_ids]
    definitions = scope_definitions(db, checklist_version_id, views)
    codes = ordered_codes(definitions, views)

    team = {c["code"]: c for c in criteria_stats(views, definitions, codes)}
    criteria = []
    for c in criteria_stats(mine, definitions, codes):
        t = team.get(c["code"], {})
        criteria.append({**c, "team_pass_rate": t.get("pass_rate"), "team_mean_ratio": t.get("mean_ratio")})

    rankable = [c for c in criteria if not c["is_critical"] and (c["weight"] or 0) > 0 and c["n_applicable"] > 0]
    weak = sorted((c for c in rankable if c["lost_points"] > 0), key=lambda c: (-c["lost_points"], _value(c)))[:COACHING_POINTS]
    weak_codes = {c["code"] for c in weak}
    strong = sorted((c for c in rankable if c["code"] not in weak_codes), key=lambda c: (-_value(c), -(c["weight"] or 0)))[:STRENGTHS]

    def brief(c: dict[str, Any]) -> dict[str, Any]:
        keys = ("code", "title", "answer_type", "weight", "pass_rate", "mean_ratio", "team_pass_rate", "team_mean_ratio", "n_applicable")
        return {k: c[k] for k in keys}

    coaching_points = []
    for c in weak:
        candidates = sorted(((v, rv) for v in mine for rv in v.rows if rv.code == c["code"] and rv.lost > EPS), key=example_rank)
        d = definitions.get(c["code"])
        coaching_points.append({
            **brief(c),
            "lost_points": c["lost_points"],
            "dialogs_losing_points": c["dialogs_losing_points"],
            "expected": d.instruction if d is not None else None,
            "examples": example_items(db, candidates[:EXAMPLES_PER_POINT]),
        })

    totals = [v.total for v in mine if v.total is not None]
    team_totals = [v.total for v in views if v.total is not None]
    return {
        "operator_name": operator_name,
        "checklist_version_id": checklist_version_id,
        "generated_at": now.isoformat(),
        "conversations_total": len(conversations),
        "evaluated": len(mine),
        "mean_total": mean(totals),
        "median_total": rnd(statistics.median(totals)) if totals else None,
        "critical_count": sum(1 for v in mine if v.critical),
        "team_mean_total": mean(team_totals),
        "languages": sorted({c.language for c in conversations if c.language}),
        "channels": sorted({c.channel for c in conversations if c.channel}),
        "conversations": _conversation_items(db, conversations, mine, checklist_version_id),
        "criteria": criteria,
        "strengths": [brief(c) for c in strong],
        "coaching_points": coaching_points,
        "violations": _operator_violations(db, operator_name, checklist_version_id),
        "appeals": _operator_appeals(db, conversation_ids, checklist_version_id, definitions, {v.ev.id for v in views}, now),
    }
