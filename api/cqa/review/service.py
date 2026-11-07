"""Appeals and reviewer decisions with deterministic recalculation and few-shot memory."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Appeal, Decision, Evaluation, EvaluationCriterion, FewshotExample, Turn
from ..scoring.engine import CriterionInput, answer_points, compute_score, normalize_answer


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
    ev = ec.evaluation
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


def create_appeal(db: Session, ec: EvaluationCriterion, comment: str, author_label: str) -> Appeal:
    ap = Appeal(evaluation_criterion_id=ec.id, comment=comment, author_label=author_label or "QA analyst")
    ec.review_state = "under_review"
    db.add(ap)
    db.commit()
    db.refresh(ap)
    return ap


def decide(db: Session, ap: Appeal, *, action: str, new_answer: Any, reason: str, reviewer_label: str, add_to_fewshot: bool) -> Decision:
    if ap.decision is not None:
        raise ValueError("appeal already decided")
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
        elif cdef is not None and not cdef.na_allowed:
            raise ValueError("N/A is not allowed for this criterion")
        ec.final_answer = new_answer
        ec.review_state = "changed"
    else:
        ec.final_answer = ec.answer if ec.applicable else "N/A"
        ec.review_state = "confirmed"
    dec = Decision(appeal_id=ap.id, action=action, new_answer=(new_answer if action == "change" else None), reason=reason, reviewer_label=reviewer_label or "Reviewer")
    ap.status = "decided"
    db.add(dec)
    recompute_final(db, ev)
    if action == "change":
        dec.new_score = ec.final_score
    if add_to_fewshot:
        _add_fewshot(db, ec, reason)
    db.commit()
    db.refresh(dec)
    return dec


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
