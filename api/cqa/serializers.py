"""ORM → API JSON per docs/API.md."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Appeal, Conversation, Decision, Evaluation, EvaluationCriterion, LlmTrace


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def latest_evaluation(db: Session, conversation_id: str, checklist_version_id: int | None = None, completed_only: bool = True) -> Evaluation | None:
    q = select(Evaluation).where(Evaluation.conversation_id == conversation_id)
    if checklist_version_id:
        q = q.where(Evaluation.checklist_version_id == checklist_version_id)
    if completed_only:
        q = q.where(Evaluation.status.in_(["completed", "failed"]))
    return db.execute(q.order_by(Evaluation.id.desc())).scalars().first()


def evaluation_brief(ev: Evaluation | None) -> dict[str, Any] | None:
    if ev is None:
        return None
    return {"id": ev.id, "status": ev.status, "total_score": ev.total_score, "final_total_score": ev.final_total_score,
            "critical_error": ev.critical_error, "provider": ev.provider, "mode": ev.mode, "model": ev.model, "cost_usd": ev.cost_usd,
            "checklist_version_id": ev.checklist_version_id, "completed_at": _iso(ev.completed_at), "error": ev.error}


def review_state_of(ev: Evaluation | None) -> str:
    if ev is None:
        return "none"
    states = {r.review_state for r in ev.criteria}
    if "under_review" in states:
        return "under_review"
    if states & {"confirmed", "changed"}:
        return "reviewed"
    return "none"


def conversation_summary(db: Session, c: Conversation, ev: Evaluation | None = None) -> dict[str, Any]:
    if ev is None:
        ev = latest_evaluation(db, c.id)
    return {"id": c.id, "channel": c.channel, "language": c.language, "operator_name": c.operator_name, "turn_count": c.turn_count,
            "has_audio": bool(c.audio_url), "first_response_seconds": c.first_response_seconds, "terminal_event": c.terminal_event,
            "imported_at": _iso(c.imported_at), "dataset_id": c.dataset_id, "latest_evaluation": evaluation_brief(ev), "review_state": review_state_of(ev)}


def turn_dict(t) -> dict[str, Any]:
    return {"idx": t.idx, "timestamp_raw": t.timestamp_raw, "offset_seconds": t.offset_seconds, "speaker": t.speaker, "text": t.text,
            "is_duplicate": t.is_duplicate, "segment": t.segment}


def conversation_detail(db: Session, c: Conversation) -> dict[str, Any]:
    d = conversation_summary(db, c)
    d["turns"] = [turn_dict(t) for t in c.turns]
    d["audio_url_available"] = bool(c.audio_url)
    d["meta"] = c.meta or {}
    return d


def appeal_dict(ap: Appeal) -> dict[str, Any]:
    return {"id": ap.id, "evaluation_criterion_id": ap.evaluation_criterion_id, "comment": ap.comment, "author_label": ap.author_label,
            "status": ap.status, "created_at": _iso(ap.created_at), "decision": decision_dict(ap.decision) if ap.decision else None}


def decision_dict(d: Decision | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {"id": d.id, "action": d.action, "new_answer": d.new_answer, "new_score": d.new_score, "reason": d.reason,
            "reviewer_label": d.reviewer_label, "created_at": _iso(d.created_at)}


def criterion_result(r: EvaluationCriterion, title: str | None = None, answer_type: str | None = None, options: list | None = None) -> dict[str, Any]:
    appeals = sorted(r.appeals, key=lambda a: a.id)
    return {"id": r.id, "code": r.code, "title": title, "answer_type": answer_type, "options": options, "applicable": r.applicable, "na_reason": r.na_reason,
            "answer": r.answer, "score": r.score, "max_points": r.max_points, "weight": r.weight, "weight_applied": round(r.weight_applied, 4),
            "earned": round(r.earned, 4), "confidence": r.confidence, "explanation": r.explanation, "evidence": r.evidence or [],
            "checked_span": r.checked_span, "flags": r.flags or [], "claims": r.claims or [], "votes": r.votes,
            "review_state": r.review_state, "final_answer": (r.final_answer if r.final_answer is not None else (r.answer if r.applicable else None)),
            "final_score": (r.final_score if r.final_answer is not None else r.score),
            "final_earned": (r.final_earned if r.final_answer is not None else round(r.earned, 4)),
            "appeal": appeal_dict(appeals[-1]) if appeals else None, "appeals": [appeal_dict(a) for a in appeals]}


def evaluation_dict(db: Session, ev: Evaluation, include_criteria: bool = True) -> dict[str, Any]:
    from .models import Criterion

    defs = {c.code: c for c in db.execute(select(Criterion).where(Criterion.checklist_version_id == ev.checklist_version_id)).scalars()}
    d = {"id": ev.id, "conversation_id": ev.conversation_id, "checklist_version_id": ev.checklist_version_id, "status": ev.status,
         "provider": ev.provider, "mode": ev.mode, "model": ev.model, "prompt_version": ev.prompt_version, "total_score": ev.total_score,
         "final_total_score": ev.final_total_score, "critical_error": ev.critical_error, "sections": ev.sections or [], "cost_usd": ev.cost_usd,
         "input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens, "latency_ms": ev.latency_ms, "job_id": ev.job_id, "error": ev.error,
         "created_at": _iso(ev.created_at), "completed_at": _iso(ev.completed_at), "review_state": review_state_of(ev)}
    if include_criteria:
        rows = sorted(ev.criteria, key=lambda r: (defs[r.code].order if r.code in defs else 99))
        d["criteria"] = [criterion_result(r, defs[r.code].title if r.code in defs else None, defs[r.code].answer_type if r.code in defs else None,
                                          defs[r.code].options if r.code in defs else None) for r in rows]
    return d


def trace_dict(t: LlmTrace) -> dict[str, Any]:
    return {"id": t.id, "criterion_code": t.criterion_code, "kind": t.kind, "provider": t.provider, "model": t.model, "prompt_version": t.prompt_version,
            "request": t.request, "response": t.response, "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
            "cache_read_tokens": t.cache_read_tokens, "cost_usd": t.cost_usd, "latency_ms": t.latency_ms, "created_at": _iso(t.created_at)}
