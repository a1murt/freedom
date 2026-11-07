from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Appeal, Criterion, EvaluationCriterion
from ..review import service as review
from ..serializers import appeal_dict, evaluation_dict

router = APIRouter(prefix="/api/appeals", tags=["appeals"])


class AppealBody(BaseModel):
    evaluation_criterion_id: int
    comment: str
    author_label: str = "QA analyst"
    author_role: str | None = None  # operator | analyst | reviewer | lead | methodologist | admin | director
    author_name: str | None = None


class DecisionBody(BaseModel):
    action: str  # confirm | change
    new_answer: Any = None
    reason: str
    reviewer_label: str = "Reviewer"
    reviewer_role: str | None = None
    add_to_fewshot: bool = True


class PreviewBody(BaseModel):
    evaluation_criterion_id: int
    new_answer: Any


def _with_context(db: Session, ap: Appeal) -> dict[str, Any]:
    ec = ap.evaluation_criterion
    ev = ec.evaluation
    cdef = db.execute(select(Criterion).where(Criterion.checklist_version_id == ev.checklist_version_id, Criterion.code == ec.code)).scalar_one_or_none()
    d = appeal_dict(ap)
    d.update({"conversation_id": ev.conversation_id, "evaluation_id": ev.id, "code": ec.code, "title": cdef.title if cdef else None,
              "answer_type": cdef.answer_type if cdef else None, "options": cdef.options if cdef else None, "na_allowed": cdef.na_allowed if cdef else False,
              "applicable": ec.applicable, "answer": ec.answer, "na_reason": ec.na_reason, "score": ec.score, "max_points": ec.max_points,
              "weight": ec.weight, "explanation": ec.explanation, "evidence": ec.evidence or [], "total_score": ev.total_score,
              "final_total_score": ev.final_total_score, "review_state": ec.review_state, "final_answer": ec.final_answer,
              "language": ev.conversation.language, "channel": ev.conversation.channel, "operator_name": ev.conversation.operator_name,
              "na_allowed_for_channel": bool(cdef and (cdef.na_allowed or ((cdef.channel_overrides or {}).get(ev.conversation.channel, {}) or {}).get("na_allowed")))})
    return d


@router.get("")
def list_appeals(status: str | None = None, operator: str | None = None, conversation_id: str | None = None, author_role: str | None = None,
                 db: Session = Depends(get_db)):
    stmt = select(Appeal).options(selectinload(Appeal.decision)).order_by(Appeal.status.desc(), Appeal.id.desc())
    if status:
        stmt = stmt.where(Appeal.status == status)
    if author_role:
        stmt = stmt.where(Appeal.author_role == author_role)
    items = [_with_context(db, ap) for ap in db.execute(stmt).scalars().all()]
    if operator:
        items = [a for a in items if a.get("operator_name") == operator]
    if conversation_id:
        items = [a for a in items if a.get("conversation_id") == conversation_id]
    return items


@router.post("")
def create_appeal(body: AppealBody, db: Session = Depends(get_db)):
    ec = db.get(EvaluationCriterion, body.evaluation_criterion_id)
    if ec is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "criterion result not found"})
    if any(a.status == "open" for a in ec.appeals):
        raise HTTPException(409, detail={"code": "already_open", "message": "an appeal is already open for this criterion"})
    if body.author_role == "operator" and body.author_name and ec.evaluation.conversation.operator_name and body.author_name != ec.evaluation.conversation.operator_name:
        raise HTTPException(403, detail={"code": "not_your_dialog", "message": "Оператор может оспаривать только оценки своих диалогов"})
    ap = review.create_appeal(db, ec, body.comment, body.author_label, body.author_role, body.author_name)
    return _with_context(db, ap)


@router.post("/preview")
def preview(body: PreviewBody, db: Session = Depends(get_db)):
    ec = db.get(EvaluationCriterion, body.evaluation_criterion_id)
    if ec is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "criterion result not found"})
    return review.preview_total(db, ec, body.new_answer)


@router.get("/{appeal_id}")
def get_appeal(appeal_id: int, db: Session = Depends(get_db)):
    ap = db.get(Appeal, appeal_id)
    if ap is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "appeal not found"})
    return _with_context(db, ap)


@router.post("/{appeal_id}/decision")
def decide(appeal_id: int, body: DecisionBody, db: Session = Depends(get_db)):
    ap = db.get(Appeal, appeal_id)
    if ap is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "appeal not found"})
    if body.action not in ("confirm", "change"):
        raise HTTPException(400, detail={"code": "bad_action", "message": "action must be confirm or change"})
    try:
        review.decide(db, ap, action=body.action, new_answer=body.new_answer, reason=body.reason, reviewer_label=body.reviewer_label, reviewer_role=body.reviewer_role,
                      add_to_fewshot=body.add_to_fewshot)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_decision", "message": str(e)})
    db.refresh(ap)
    return {"appeal": _with_context(db, ap), "evaluation": evaluation_dict(db, ap.evaluation_criterion.evaluation)}
