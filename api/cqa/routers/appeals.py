from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import case, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Appeal, EvaluationCriterion
from ..review import service as review
from ..security.roles import DEFAULT_ROLE, actor_display, current_actor, same_person
from ..serializers import appeal_dict, claims_out, evaluation_dict, max_turn_index, votes_out
from ..turnrefs import display_turn_refs

router = APIRouter(prefix="/api/appeals", tags=["appeals"])

NOT_YOUR_DIALOG = {"code": "not_your_dialog", "message": "Оператор может оспаривать только оценки своих диалогов"}
CRITERION_NOT_FOUND = {"code": "not_found", "message": "Результат оценки критерия не найден"}
APPEAL_NOT_FOUND = {"code": "not_found", "message": "Апелляция не найдена"}
ALREADY_OPEN = {"code": "already_open", "message": "По этому критерию уже есть открытая апелляция"}
BAD_ACTION = {"code": "bad_action", "message": "Действие должно быть confirm (подтвердить) или change (изменить)"}
# review.service raises ValueError with English texts; the API answers in Russian (unknown texts are passed through)
DECISION_ERRORS_RU = {
    "appeal already decided": "По этой апелляции уже принято решение",
    "new_answer is required for a change": "Для изменения оценки укажите new_answer",
    "N/A is not allowed for this criterion": "Для этого критерия ответ N/A недопустим",
    "appeal is not open": "Апелляция закрыта (отозвана): решение можно принять только по открытой апелляции",
}


def _decision_error_ru(text: str) -> str:
    if text in DECISION_ERRORS_RU:
        return DECISION_ERRORS_RU[text]
    if text.startswith("new_answer ") and text.endswith(" is not an allowed option"):
        return f"Ответ {text[len('new_answer '):-len(' is not an allowed option')]} не входит в варианты ответа критерия"
    return text
NOT_OPEN = {"code": "not_open", "message": "Апелляция уже закрыта: отозвать можно только открытую апелляцию"}
NOT_YOUR_APPEAL = {"code": "not_your_appeal", "message": "Оператор может отозвать только свою апелляцию"}
# the deployed web app (AppealDialog, ReviewPage) only checks that the text is not blank: any non-blank text is enough
COMMENT_MIN, COMMENT_MAX, REASON_MIN = 1, 4000, 1


def _field_error(code: str, field: str, message: str) -> HTTPException:
    return HTTPException(422, detail={"code": code, "message": message, "errors": [{"field": field, "message": message}]})


OPERATOR_NAME_REQUIRED = {"code": "operator_name_required",
                          "message": "Укажите имя оператора (заголовок X-CQA-User), чтобы оспаривать оценки своих диалогов"}


class AppealBody(BaseModel):
    evaluation_criterion_id: int
    comment: str
    author_label: str = "QA analyst"
    author_role: str | None = None  # operator | analyst | reviewer | lead | methodologist | admin | director
    author_name: str | None = None


class DecisionBody(BaseModel):
    action: str  # confirm | change
    new_answer: Any = None  # an option value, or "N/A" (also "na", "н/п", "not applicable"; null / "" where N/A is allowed)
    reason: str = ""  # required (not blank) for "change"
    reviewer_label: str = "Reviewer"
    reviewer_role: str | None = None
    add_to_fewshot: bool = True


class PreviewBody(BaseModel):
    evaluation_criterion_id: int
    new_answer: Any


def _is_latest(db: Session, ev) -> bool:
    """False when the dialog has a newer completed evaluation of the same checklist version (the appeal is on an old one)."""
    from ..models import Evaluation

    newer = db.execute(select(Evaluation.id).where(Evaluation.conversation_id == ev.conversation_id, Evaluation.status == "completed",
                                                   Evaluation.checklist_version_id == ev.checklist_version_id, Evaluation.id > ev.id).limit(1)).first()
    return newer is None


def _with_context(db: Session, ap: Appeal) -> dict[str, Any]:
    ec = ap.evaluation_criterion
    ev = ec.evaluation
    cdef = review.criterion_definition(db, ec)
    d = appeal_dict(ap)
    d.update({"conversation_id": ev.conversation_id, "evaluation_id": ev.id, "code": ec.code, "title": cdef.title if cdef else None,
              "answer_type": cdef.answer_type if cdef else None, "options": cdef.options if cdef else None, "na_allowed": cdef.na_allowed if cdef else False,
              "applicable": ec.applicable, "answer": ec.answer, "na_reason": display_turn_refs(ec.na_reason, max_turn_index(ev.conversation)), "score": ec.score, "max_points": ec.max_points,
              "weight": ec.weight, "explanation": display_turn_refs(ec.explanation, max_turn_index(ev.conversation)), "evidence": ec.evidence or [], "total_score": ev.total_score,
              "final_total_score": ev.final_total_score, "review_state": ec.review_state, "final_answer": ec.final_answer,
              "language": ev.conversation.language, "channel": ev.conversation.channel, "operator_name": ev.conversation.operator_name,
              "na_allowed_for_channel": review.na_allowed_on_channel(cdef, ev.conversation.channel),
              "checklist_version_id": ev.checklist_version_id, "confidence": ec.confidence, "weight_applied": ec.weight_applied,
              "earned": ec.earned, "final_earned": ec.final_earned, "checked_span": ec.checked_span, "flags": ec.flags or [],
              "votes": votes_out(ec.votes, max_turn_index(ev.conversation)), "claims": claims_out(ec.claims, max_turn_index(ev.conversation)), "instruction": cdef.instruction if cdef else None,
              "is_latest_evaluation": _is_latest(db, ev)})
    return d


@router.get("")
def list_appeals(status: str | None = None, operator: str | None = None, conversation_id: str | None = None, author_role: str | None = None,
                 code: str | None = None, language: str | None = None, channel: str | None = None, q: str | None = None,
                 db: Session = Depends(get_db)):
    """Open appeals first, then decided, then withdrawn; newest first within each group. Filters are exact except `q`
    (substring of the comment, conversation id, operator or author name, case-insensitive)."""
    order = case((Appeal.status == "open", 0), (Appeal.status == "decided", 1), else_=2)
    stmt = select(Appeal).options(selectinload(Appeal.decision)).order_by(order, Appeal.id.desc())
    if status:
        stmt = stmt.where(Appeal.status == status)
    if author_role:
        stmt = stmt.where(Appeal.author_role == author_role)
    items = [_with_context(db, ap) for ap in db.execute(stmt).scalars().all()]
    if operator:
        items = [a for a in items if a.get("operator_name") == operator]
    if conversation_id:
        items = [a for a in items if a.get("conversation_id") == conversation_id]
    if code:
        items = [a for a in items if (a.get("code") or "").upper() == code.upper()]
    if language:
        items = [a for a in items if (a.get("language") or "").upper() == language.upper()]
    if channel:
        items = [a for a in items if a.get("channel") == channel]
    if q and q.strip():
        needle = q.strip().lower()
        items = [a for a in items if any(needle in str(a.get(k) or "").lower()
                                         for k in ("comment", "conversation_id", "operator_name", "author_name", "author_label"))]
    return items


def _appeal_author(body: AppealBody, actor: dict[str, Any]) -> tuple[str | None, str | None, str]:
    """author_role / author_name / author_label: the body's values, defaulted from the actor (X-CQA-Role / X-CQA-User).
    An operator always appeals as "operator" under the header name, so the body cannot lift the own-dialog check."""
    role, name = actor.get("role") or DEFAULT_ROLE, actor.get("name")
    author_role, author_name, author_label = body.author_role, body.author_name, body.author_label
    if role == "operator":
        author_role, author_name = "operator", name or author_name
    elif role != DEFAULT_ROLE:
        author_role, author_name = author_role or role, author_name or name
    else:
        author_name = author_name or name
    if "author_label" not in body.model_fields_set:
        author_label = actor_display(actor) or author_label
    return author_role, author_name, author_label


@router.post("")
def create_appeal(body: AppealBody, request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    comment = (body.comment or "").strip()
    if len(comment) < COMMENT_MIN:
        raise _field_error("comment_required", "comment", "Опишите, с чем вы не согласны: комментарий не может быть пустым.")
    if len(comment) > COMMENT_MAX:
        raise _field_error("comment_too_long", "comment", f"Комментарий длиннее {COMMENT_MAX} символов: сократите его.")
    ec = db.get(EvaluationCriterion, body.evaluation_criterion_id)
    if ec is None:
        raise HTTPException(404, detail=CRITERION_NOT_FOUND)
    if any(a.status == "open" for a in ec.appeals):
        raise HTTPException(409, detail=ALREADY_OPEN)
    author_role, author_name, author_label = _appeal_author(body, actor)
    conv = ec.evaluation.conversation
    dialog_operator = conv.operator_name  # detected from the greeting
    dialog_agent = getattr(conv, "agent_name", None)  # source-system identity (Dynamics 365), when known

    def is_own(name: str | None) -> bool:
        return same_person(name, dialog_operator) or same_person(name, dialog_agent)

    if actor.get("role") == "operator":  # identified by the role header: only dialogs whose operator is theirs
        if not author_name:
            raise HTTPException(403, detail=OPERATOR_NAME_REQUIRED)
        if not is_own(author_name):
            raise HTTPException(403, detail=NOT_YOUR_DIALOG)
    elif author_role == "operator" and author_name and (dialog_operator or dialog_agent) and not is_own(author_name):
        raise HTTPException(403, detail=NOT_YOUR_DIALOG)
    ap = review.create_appeal(db, ec, comment, author_label, author_role, author_name)
    return _with_context(db, ap)


@router.post("/preview")
def preview(body: PreviewBody, db: Session = Depends(get_db)):
    """Final total if the criterion's answer were `new_answer` (nothing is saved). A missing new_answer (null or "")
    previews N/A where the criterion allows N/A on the dialog's channel, as a decision would save it; elsewhere it
    previews the total without a decision on this criterion."""
    ec = db.get(EvaluationCriterion, body.evaluation_criterion_id)
    if ec is None:
        raise HTTPException(404, detail=CRITERION_NOT_FOUND)
    try:
        return review.preview_total(db, ec, review.missing_answer_as_na(db, ec, body.new_answer))
    except ValueError as e:  # an N/A the criterion does not allow here: the same 400 a decision would give
        raise HTTPException(400, detail={"code": "invalid_decision", "message": _decision_error_ru(str(e))})


@router.get("/{appeal_id}")
def get_appeal(appeal_id: int, db: Session = Depends(get_db)):
    ap = db.get(Appeal, appeal_id)
    if ap is None:
        raise HTTPException(404, detail=APPEAL_NOT_FOUND)
    return _with_context(db, ap)


class WithdrawBody(BaseModel):
    reason: str | None = None


@router.post("/{appeal_id}/withdraw")
def withdraw(appeal_id: int, request: Request, body: WithdrawBody | None = None, db: Session = Depends(get_db)):
    """Withdraw an open appeal (the author, or anyone with appeals.create for operator appeals filed on their behalf;
    an operator may withdraw only their own). The criterion returns to its previous review state."""
    actor = current_actor(request)
    ap = db.get(Appeal, appeal_id)
    if ap is None:
        raise HTTPException(404, detail=APPEAL_NOT_FOUND)
    if ap.status != "open":
        raise HTTPException(409, detail=NOT_OPEN)
    if actor.get("role") == "operator" and not same_person(actor.get("name"), ap.author_name):
        raise HTTPException(403, detail=NOT_YOUR_APPEAL)
    review.withdraw_appeal(db, ap)
    return _with_context(db, ap)


@router.post("/{appeal_id}/decision")
def decide(appeal_id: int, body: DecisionBody, request: Request, db: Session = Depends(get_db)):
    """Decide an open appeal: "confirm" the AI answer, or "change" it (new_answer and a non-blank reason). With "change"
    a missing new_answer (null or "") means N/A where the criterion allows N/A on the dialog's channel (the deployed web
    app sends N/A as null), else 422 new_answer_required. A decided or withdrawn appeal: 400 invalid_decision."""
    actor = current_actor(request)
    ap = db.get(Appeal, appeal_id)
    if ap is None:
        raise HTTPException(404, detail=APPEAL_NOT_FOUND)
    if body.action not in ("confirm", "change"):
        raise HTTPException(400, detail=BAD_ACTION)
    new_answer = body.new_answer
    if body.action == "change":
        new_answer = review.missing_answer_as_na(db, ap.evaluation_criterion, new_answer)
        if new_answer is None:
            raise _field_error("new_answer_required", "new_answer",
                               "Укажите новый ответ: значение одного из вариантов ответа (ответ N/A для этого критерия в этом канале недопустим).")
        if len((body.reason or "").strip()) < REASON_MIN:
            raise _field_error("reason_required", "reason", "Укажите причину изменения оценки: поле не может быть пустым.")
    reviewer_role = body.reviewer_role or (actor["role"] if actor.get("role") != DEFAULT_ROLE else None)
    reviewer_label = body.reviewer_label
    if "reviewer_label" not in body.model_fields_set:
        reviewer_label = actor_display(actor) or reviewer_label
    try:
        review.decide(db, ap, action=body.action, new_answer=new_answer, reason=body.reason, reviewer_label=reviewer_label, reviewer_role=reviewer_role,
                      add_to_fewshot=body.add_to_fewshot)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_decision", "message": _decision_error_ru(str(e))})
    db.refresh(ap)
    return {"appeal": _with_context(db, ap), "evaluation": evaluation_dict(db, ap.evaluation_criterion.evaluation)}
