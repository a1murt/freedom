"""Appeals and reviewer decisions with deterministic recalculation and few-shot memory."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Appeal, Criterion, Decision, Evaluation, EvaluationCriterion, FewshotExample, Turn
from ..scoring.engine import CriterionInput, answer_points, compute_score, normalize_answer


# Spellings of "not applicable" accepted from clients (the API stores and returns exactly "N/A").
NA_ALIASES = frozenset({"n/a", "na", "n.a.", "н/п", "нп", "н/а", "not_applicable", "not applicable", "неприменимо", "не применимо",
                        "қолданылмайды"})


def normalize_na_value(value: Any) -> Any:
    """"na", "н/п", "not applicable"… → "N/A"; anything else unchanged."""
    if isinstance(value, str) and value.strip().lower() in NA_ALIASES:
        return "N/A"
    return value


def is_missing_answer(value: Any) -> bool:
    """No new answer given: None or a blank string."""
    return value is None or (isinstance(value, str) and not value.strip())


def na_allowed_on_channel(cdef: Criterion | None, channel: str | None) -> bool:
    """N/A is a valid answer of the criterion on this channel: allowed for the criterion itself, or by its channel
    override (a checklist may allow N/A on one channel only; the organizer checklist no longer does since 2026-11-08)."""
    if cdef is None:
        return False
    override = (cdef.channel_overrides or {}).get(channel) or {}
    return bool(cdef.na_allowed or (isinstance(override, dict) and override.get("na_allowed")))


def criterion_definition(db: Session, ec: EvaluationCriterion) -> Criterion | None:
    """The checklist criterion a result row was scored against (same checklist version, same code)."""
    return db.execute(select(Criterion).where(Criterion.checklist_version_id == ec.evaluation.checklist_version_id,
                                              Criterion.code == ec.code)).scalar_one_or_none()


def missing_answer_as_na(db: Session, ec: EvaluationCriterion, new_answer: Any) -> Any:
    """A missing new answer (None or a blank string) stands for N/A when the criterion allows N/A on the dialog's channel
    (the deployed web app sends N/A as null); otherwise it stays missing (None). Any other value is returned unchanged."""
    if not is_missing_answer(new_answer):
        return new_answer
    return "N/A" if na_allowed_on_channel(criterion_definition(db, ec), ec.evaluation.conversation.channel) else None


def _criterion_defs(ev: Evaluation) -> dict[str, Any]:
    return {c.code: c for c in ev.checklist_version.criteria} if hasattr(ev, "checklist_version") else {}


def recompute_final(db: Session, ev: Evaluation) -> None:
    """Recompute final_total_score and final_earned from final_* (fallback to AI values)."""
    from ..models import ChecklistVersion

    version = db.execute(select(ChecklistVersion).where(ChecklistVersion.id == ev.checklist_version_id).options(selectinload(ChecklistVersion.criteria))).scalar_one()
    defs = {c.code: c for c in version.criteria}
    inputs = []
    for r in ev.criteria:
        c = defs.get(r.code)
        if c is None:
            continue
        answer = r.final_answer if r.final_answer is not None else r.answer
        applicable = r.applicable if r.final_answer is None else (r.final_answer != "N/A")
        pts = answer_points(c.options, answer) if (applicable and answer not in (None, "N/A")) else None
        critical_hit = bool(c.is_critical and applicable and answer is not None and str(answer).lower() == str(c.critical_value or "yes").lower())
        inputs.append(CriterionInput(code=r.code, weight=c.weight, max_points=c.max_points, points=pts, applicable=bool(applicable and pts is not None),
                                     section=c.section, is_critical=c.is_critical, critical_hit=critical_hit))
    score = compute_score(version.scoring_rules or {}, inputs, version.sections or [])
    for r in ev.criteria:
        cr = score.criteria.get(r.code)
        if r.final_answer is not None:
            c = defs.get(r.code)
            r.final_score = answer_points(c.options, r.final_answer) if (c and r.final_answer != "N/A") else None
            r.final_earned = cr.earned if cr else 0.0
    ev.final_total_score = score.total
    ev.critical_error = score.critical
    titles = {s["code"]: s.get("title") for s in (version.sections or [])}
    ev.sections = [{"code": s.code, "title": titles.get(s.code), "score": s.score, "max": s.max} for s in score.sections]


def preview_total(db: Session, ec: EvaluationCriterion, new_answer: Any) -> dict[str, Any]:
    """The final total if the row's answer were `new_answer` (nothing is saved). ValueError for an N/A the criterion
    does not allow on the dialog's channel, as a decision would refuse it."""
    new_answer = normalize_na_value(new_answer)
    ev = ec.evaluation
    if new_answer == "N/A" and not na_allowed_on_channel(criterion_definition(db, ec), ev.conversation.channel):
        raise ValueError("N/A is not allowed for this criterion")
    saved = {r.id: (r.final_answer, r.final_score, r.final_earned) for r in ev.criteria}
    saved_total, saved_sections, saved_crit = ev.final_total_score, ev.sections, ev.critical_error
    ec.final_answer = new_answer
    recompute_final(db, ev)
    out = {"final_total_score": ev.final_total_score, "sections": ev.sections, "critical_error": ev.critical_error}
    for r in ev.criteria:
        r.final_answer, r.final_score, r.final_earned = saved[r.id]
    ev.final_total_score, ev.sections, ev.critical_error = saved_total, saved_sections, saved_crit
    db.expire_all()
    return out


def create_appeal(db: Session, ec: EvaluationCriterion, comment: str, author_label: str, author_role: str | None = None, author_name: str | None = None) -> Appeal:
    ap = Appeal(evaluation_criterion_id=ec.id, comment=comment, author_label=author_label or "QA analyst", author_role=author_role, author_name=author_name)
    ec.review_state = "under_review"
    db.add(ap)
    db.commit()
    db.refresh(ap)
    return ap


def decide(db: Session, ap: Appeal, *, action: str, new_answer: Any, reason: str, reviewer_label: str, add_to_fewshot: bool, reviewer_role: str | None = None) -> Decision:
    """Decide an open appeal ("confirm" | "change") and recompute the final total. ValueError (English text, translated
    by the router) for a decided or withdrawn appeal and for a new answer the criterion does not allow."""
    if ap.decision is not None:
        raise ValueError("appeal already decided")
    if ap.status != "open":  # withdrawn: deciding it would rewrite the criterion's state (and hide a newer open appeal)
        raise ValueError("appeal is not open")
    new_answer = normalize_na_value(new_answer)
    ec = ap.evaluation_criterion
    ev = ec.evaluation
    from ..models import ChecklistVersion

    version = db.execute(select(ChecklistVersion).where(ChecklistVersion.id == ev.checklist_version_id).options(selectinload(ChecklistVersion.criteria))).scalar_one()
    cdef = next((c for c in version.criteria if c.code == ec.code), None)
    if action == "change":
        if new_answer is None:
            raise ValueError("new_answer is required for a change")
        if new_answer != "N/A":
            if cdef is None or answer_points(cdef.options, new_answer) is None:
                raise ValueError(f"new_answer {new_answer!r} is not an allowed option")
            new_answer = normalize_answer(cdef.options, new_answer)
        elif cdef is not None and not na_allowed_on_channel(cdef, ev.conversation.channel):
            raise ValueError("N/A is not allowed for this criterion")
        ec.final_answer = new_answer
        ec.review_state = "changed"
    else:
        if not ec.applicable and cdef is not None and not na_allowed_on_channel(cdef, ev.conversation.channel):
            # a stale N/A: the checklist no longer allows it on this channel (C1 on calls), so confirming it would export
            # a null the results format refuses; the reviewer changes the answer instead
            raise ValueError("N/A is not allowed for this criterion")
        ec.final_answer = ec.answer if ec.applicable else "N/A"
        ec.review_state = "confirmed"
    dec = Decision(appeal_id=ap.id, action=action, new_answer=(new_answer if action == "change" else None), reason=reason, reviewer_label=reviewer_label or "Reviewer", reviewer_role=reviewer_role)
    ap.status = "decided"
    db.add(dec)
    recompute_final(db, ev)
    if action == "change":
        dec.new_score = ec.final_score
    from ..judge.validate import CONTACT_OUTSIDE_RECORDING_FLAG

    # a row decided by the recording-gap rule never teaches the judge: its «Да» says nothing about what a greeting is
    if add_to_fewshot and CONTACT_OUTSIDE_RECORDING_FLAG not in (ec.flags or []):
        _add_fewshot(db, ec, reason)
    db.commit()
    db.refresh(dec)
    return dec


def withdraw_appeal(db: Session, ap: Appeal) -> Appeal:
    """Withdraw an open appeal: status "withdrawn"; the criterion returns to its state before the appeal (see
    _settled_review_state). Final answers and totals are untouched (an open appeal never changed them)."""
    if ap.status != "open":
        raise ValueError("appeal is not open")
    ap.status = "withdrawn"
    ec = ap.evaluation_criterion
    if not any(a.status == "open" and a.id != ap.id for a in ec.appeals):
        ec.review_state = _settled_review_state(ec, exclude_appeal_id=ap.id)
    db.commit()
    db.refresh(ap)
    return ap


def _settled_review_state(ec: EvaluationCriterion, exclude_appeal_id: int | None = None) -> str:
    """Review state of a row without an open appeal: the last decision on it; else a decision a partial re-evaluation
    carried onto the row (final_answer is set while the decided appeal stays on the base evaluation's row): "confirmed"
    when the final answer is the AI answer, else "changed"; else "none"."""
    decided = sorted((a for a in ec.appeals if a.id != exclude_appeal_id and a.decision is not None), key=lambda a: a.id)
    if decided:
        return {"change": "changed", "confirm": "confirmed"}.get(decided[-1].decision.action, "none")
    if ec.final_answer is not None:
        return "confirmed" if ec.final_answer == (ec.answer if ec.applicable else "N/A") else "changed"
    return "none"


def _add_fewshot(db: Session, ec: EvaluationCriterion, reason: str) -> None:
    ev = ec.evaluation
    conv = ev.conversation
    turns = db.execute(select(Turn).where(Turn.conversation_id == conv.id).order_by(Turn.idx)).scalars().all()
    idxs = sorted({e.get("turn_idx") for e in (ec.evidence or []) if isinstance(e, dict) and e.get("turn_idx") is not None})
    if not idxs:
        start = (conv.meta or {}).get("operator_start_idx") or 0
        idxs = list(range(start, min(start + 4, len(turns))))
    lo, hi = max(0, min(idxs) - 1), min(len(turns) - 1, max(idxs) + 1)
    excerpt = "\n".join(f"[{t.idx}] {t.speaker}: {t.text}" for t in turns if lo <= t.idx <= hi)[:1200]
    verdict = {"applicable": ec.final_answer != "N/A", "answer": ec.final_answer, "explanation": f"Решение проверяющего: {reason}"}
    db.add(FewshotExample(criterion_code=ec.code, language=conv.language, channel=conv.channel, transcript_excerpt=excerpt, verdict=verdict, source="review"))
