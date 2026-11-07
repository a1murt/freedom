"""ORM → API JSON per docs/API.md."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, select
from sqlalchemy.orm import Session

from .models import (
    Appeal,
    ChecklistVersion,
    Conversation,
    Criterion,
    Decision,
    Evaluation,
    EvaluationCriterion,
    LlmTrace,
)
from .review.service import na_allowed_on_channel
from .scoring.engine import CriterionInput, answer_points, compute_score, normalize_answer
from .turnrefs import display_turn_refs


def iso_utc(dt: datetime | None) -> str | None:
    """ISO 8601 with an explicit UTC offset («…+00:00»), the same instant as stored. Every stored time is UTC
    (models.utcnow), but SQLite hands DateTime(timezone=True) columns back naive, and a browser reads a bare
    «2026-11-08T12:00:00» as local time. Every timestamp the serializers emit goes through here."""
    if dt is None:
        return None
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


INTERRUPTED_ERROR = "interrupted by restart"  # = cqa.jobs.runner.INTERRUPTED_ERROR (start-up recovery marks in-flight rows)


def latest_evaluation(db: Session, conversation_id: str, checklist_version_id: int | None = None, completed_only: bool = True) -> Evaluation | None:
    """The dialog's latest evaluation (completed or failed). A row that start-up recovery failed ("interrupted by
    restart": the process stopped mid-evaluation) never hides an earlier result; it is returned only when the dialog has
    no other evaluation. Real failures still count as the latest attempt."""
    q = select(Evaluation).where(Evaluation.conversation_id == conversation_id)
    if checklist_version_id:
        q = q.where(Evaluation.checklist_version_id == checklist_version_id)
    if completed_only:
        q = q.where(Evaluation.status.in_(["completed", "failed"]))
    interrupted = case((and_(Evaluation.status == "failed", Evaluation.error == INTERRUPTED_ERROR), 1), else_=0)
    return db.execute(q.order_by(interrupted, Evaluation.id.desc())).scalars().first()


def version_info(ev: Evaluation) -> dict[str, Any]:
    """Checklist id, name and the human version number of an evaluation's checklist version (the card shows «v2», not
    the database id). One identity-map lookup per version and session."""
    from sqlalchemy.orm import object_session

    db = object_session(ev)
    v = db.get(ChecklistVersion, ev.checklist_version_id) if (db is not None and ev.checklist_version_id is not None) else None
    if v is None:
        return {"checklist_id": None, "checklist_name": None, "checklist_version_no": None}
    return {"checklist_id": v.checklist_id, "checklist_name": v.checklist.name if v.checklist is not None else None,
            "checklist_version_no": v.version_no}


def evaluation_brief(ev: Evaluation | None) -> dict[str, Any] | None:
    if ev is None:
        return None
    return {"id": ev.id, "status": ev.status, "total_score": ev.total_score, "final_total_score": ev.final_total_score,
            "critical_error": ev.critical_error, "provider": ev.provider, "mode": ev.mode, "model": ev.model, "cost_usd": ev.cost_usd,
            "checklist_version_id": ev.checklist_version_id, **version_info(ev), "completed_at": iso_utc(ev.completed_at), "error": ev.error,
            "attention": attention(ev)}


LOW_CONFIDENCE = 0.6


def attention(ev: Evaluation) -> dict[str, Any]:
    """Verdicts worth a human look (CARD-09): low confidence, ensemble members that disagreed, failed criteria, and
    operator claims the reference facts contradict. Empty lists when everything is clean."""
    low, split, failed, contradicted = [], [], [], 0
    for r in ev.criteria:
        if "error" in (r.flags or []):
            failed.append(r.code)
            continue
        if r.applicable and r.confidence is not None and r.confidence < LOW_CONFIDENCE:
            low.append(r.code)
        answers = {str(v.get("answer")) for v in (r.votes or []) if isinstance(v, dict) and v.get("answer") is not None}
        if len(answers) > 1:
            split.append(r.code)
        contradicted += sum(1 for c in (r.claims or []) if isinstance(c, dict) and c.get("status") == "contradicted")
    return {"low_confidence": sorted(low), "split_votes": sorted(split), "failed": sorted(failed), "contradicted_claims": contradicted,
            "needs_review": bool(low or split or failed or contradicted)}


def review_state_of(ev: Evaluation | None) -> str:
    if ev is None:
        return "none"
    states = {r.review_state for r in ev.criteria}
    if "under_review" in states:
        return "under_review"
    if states & {"confirmed", "changed"}:
        return "reviewed"
    return "none"


def display_evaluation(db: Session, conversation_id: str, checklist_version_id: int | None = None) -> Evaluation | None:
    """The evaluation a card and its header show: the latest completed one; the latest attempt (failed) only when no
    evaluation completed. A failed retry never hides a result (the list and GET …/evaluation follow the same rule)."""
    q = select(Evaluation).where(Evaluation.conversation_id == conversation_id, Evaluation.status == "completed")
    if checklist_version_id:
        q = q.where(Evaluation.checklist_version_id == checklist_version_id)
    ev = db.execute(q.order_by(Evaluation.id.desc())).scalars().first()
    return ev if ev is not None else latest_evaluation(db, conversation_id, checklist_version_id)


def conversation_summary(db: Session, c: Conversation, ev: Evaluation | None = None, *, lookup: bool = True) -> dict[str, Any]:
    """`lookup=False`: `ev` is final, None means "no evaluation in this scope" (the list filtered by checklist version must
    not fall back to another version's evaluation)."""
    if ev is None and lookup:
        ev = display_evaluation(db, c.id)
    return {"id": c.id, "channel": c.channel, "language": c.language, "operator_name": c.operator_name,
            "agent_name": getattr(c, "agent_name", None), "turn_count": c.turn_count,
            "has_audio": bool(c.audio_url), "first_response_seconds": c.first_response_seconds, "terminal_event": c.terminal_event,
            "imported_at": iso_utc(c.imported_at), "dataset_id": c.dataset_id, "latest_evaluation": evaluation_brief(ev), "review_state": review_state_of(ev)}


def turn_dict(t) -> dict[str, Any]:
    return {"idx": t.idx, "timestamp_raw": t.timestamp_raw, "offset_seconds": t.offset_seconds, "speaker": t.speaker, "text": t.text,
            "is_duplicate": t.is_duplicate, "segment": t.segment}


IN_PROGRESS_STATUSES = ("running", "queued")  # = cqa.ingest.service.is_busy: the dialog is being evaluated


def evaluation_in_progress(db: Session, conversation_id: str) -> dict[str, Any] | None:
    """The dialog's evaluation running right now (the rows cqa.ingest.service.is_busy checks; the newest when several
    jobs run it), so the card shows «оценивается» instead of the 404 of GET …/evaluation. `started_at`: when the
    evaluation row was created, i.e. when judging this dialog began. None when nothing runs."""
    ev = db.execute(select(Evaluation).where(Evaluation.conversation_id == conversation_id, Evaluation.status.in_(IN_PROGRESS_STATUSES))
                    .order_by(Evaluation.id.desc()).limit(1)).scalars().first()
    if ev is None:
        return None
    return {"evaluation_id": ev.id, "job_id": ev.job_id, "status": ev.status, "started_at": iso_utc(ev.created_at)}


def conversation_detail(db: Session, c: Conversation) -> dict[str, Any]:
    d = conversation_summary(db, c)
    d["turns"] = [turn_dict(t) for t in c.turns]
    d["audio_url_available"] = bool(c.audio_url)
    d["meta"] = c.meta or {}
    d["evaluation_in_progress"] = evaluation_in_progress(db, c.id)
    return d


def appeal_dict(ap: Appeal) -> dict[str, Any]:
    return {"id": ap.id, "evaluation_criterion_id": ap.evaluation_criterion_id, "comment": ap.comment, "author_label": ap.author_label, "author_role": ap.author_role, "author_name": ap.author_name,
            "status": ap.status, "created_at": iso_utc(ap.created_at), "decision": decision_dict(ap.decision) if ap.decision else None}


def decision_dict(d: Decision | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {"id": d.id, "action": d.action, "new_answer": d.new_answer, "new_score": d.new_score, "reason": d.reason,
            "reviewer_label": d.reviewer_label, "reviewer_role": d.reviewer_role, "created_at": iso_utc(d.created_at)}


def _evidence_out(evidence: list | None, turn_offsets: dict[int, float | None] | None) -> list:
    """Stored evidence with the seek time of the current transcript (call offsets may have been re-derived after the
    evaluation, see cqa.ingest.renormalize)."""
    items = evidence or []
    if not turn_offsets:
        return items
    out = []
    for e in items:
        if isinstance(e, dict) and e.get("turn_idx") in turn_offsets:
            e = {**e, "offset_seconds": turn_offsets[e["turn_idx"]]}
        out.append(e)
    return out


def _current_appeal(appeals: list[Appeal]) -> Appeal | None:
    """The appeal that explains the row's review state: an open one, else the latest decided one. A later withdrawn
    appeal never hides the decision that changed the score; a row whose only appeals were withdrawn has none (the
    `appeals` list still shows them)."""
    for status in ("open", "decided"):
        found = [a for a in appeals if a.status == status]
        if found:
            return found[-1]
    return None


def claims_out(claims: list | None, max_index: int | None = None) -> list:
    """Judge-written claim notes cite turns like explanations do: shown 1-based (copies; stored JSON is untouched)."""
    return [{**c, "note": display_turn_refs(c["note"], max_index)} if isinstance(c, dict) and isinstance(c.get("note"), str) else c
            for c in (claims or [])]


def votes_out(votes: list | None, max_index: int | None = None) -> list | None:
    if votes is None:
        return None
    return [{**v, "explanation": display_turn_refs(v["explanation"], max_index)}
            if isinstance(v, dict) and isinstance(v.get("explanation"), str) else v for v in votes]


def max_turn_index(conv: Conversation | None) -> int | None:
    """The last turn index of a dialog (turn numbers beyond it in a judge text are not turn references)."""
    if conv is None or conv.turn_count in (None, 0):
        return None
    return int(conv.turn_count) - 1


NA = "N/A"


def _is_na(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == NA


def _as_option(options: list | None, value: Any) -> Any:
    """An answer as the option value it names, in that value's type («15» → 15 on C4, «Да» → "yes"), so the card can
    compare it with options[].value directly; "N/A" stays "N/A", None stays None, an unknown answer is returned as is."""
    return NA if _is_na(value) else normalize_answer(options or [], value)


def answer_option(r: EvaluationCriterion, options: list | None) -> Any:
    """The AI answer as an option value: "N/A" when the row is not applicable, None when the judge call failed (no answer)."""
    return NA if not r.applicable else _as_option(options, r.answer)


def final_option(r: EvaluationCriterion, options: list | None) -> Any:
    """The final answer as an option value: the reviewer's decision, else the AI answer; "N/A" when it is not applicable
    (also for an AI N/A nobody decided, where the older `final_answer` is null); None only for a failed criterion
    without a decision."""
    return answer_option(r, options) if r.final_answer is None else _as_option(options, r.final_answer)


def final_breakdown(ev: Evaluation, defs: dict[str, Criterion], scoring_rules: dict[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    """Each row's share of final_total_score, {code: {applicable, weight_applied, earned}}; None while nothing was decided
    (the stored weight_applied / earned are the final ones then). A decision to or from N/A moves weight between all
    rows, but only decided rows store final_earned, so the rows' final_earned stop adding up to the total: one
    compute_score over the inputs cqa.review.service.recompute_final builds (keep the two in step) gives every row its
    final share. `applicable`: the final answer is scored (false for N/A and for a failed criterion nobody decided)."""
    if not any(r.final_answer is not None for r in ev.criteria):
        return None
    inputs: list[CriterionInput] = []
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
    score = compute_score(scoring_rules or {}, inputs)
    scored = {i.code: i.applicable for i in inputs}
    out: dict[str, dict[str, Any]] = {}
    for r in ev.criteria:
        cr = score.criteria.get(r.code)  # None for a row without a definition: it earns nothing, as in recompute_final
        out[r.code] = {"applicable": scored.get(r.code, False), "weight_applied": round(cr.weight_applied, 4) if cr else 0.0,
                       "earned": round(cr.earned, 4) if cr else 0.0}
    return out


def criterion_result(r: EvaluationCriterion, title: str | None = None, answer_type: str | None = None, options: list | None = None,
                     turn_offsets: dict[int, float | None] | None = None, max_index: int | None = None, *, cdef: Criterion | None = None,
                     channel: str | None = None, breakdown: dict[str, Any] | None = None) -> dict[str, Any]:
    """One criterion row of the evaluation JSON. `cdef`: the row's criterion in the evaluation's checklist version and
    `channel` the dialog's (the card reads the definition and the channel's N/A rule from the row); `breakdown`: the
    row's entry of final_breakdown(), None while nothing in the evaluation was decided."""
    appeals = sorted(r.appeals, key=lambda a: a.id)
    current = _current_appeal(appeals)
    opts = options if options is not None else (cdef.options if cdef is not None else None)
    return {"id": r.id, "code": r.code, "title": title, "answer_type": answer_type, "options": options, "applicable": r.applicable, "na_reason": display_turn_refs(r.na_reason, max_index),
            "answer": r.answer, "score": r.score, "max_points": r.max_points, "weight": r.weight, "weight_applied": round(r.weight_applied, 4),
            "earned": round(r.earned, 4), "confidence": r.confidence, "explanation": display_turn_refs(r.explanation, max_index), "evidence": _evidence_out(r.evidence, turn_offsets),
            "checked_span": r.checked_span, "flags": r.flags or [], "claims": claims_out(r.claims, max_index), "votes": votes_out(r.votes, max_index),
            "review_state": r.review_state, "final_answer": (r.final_answer if r.final_answer is not None else (r.answer if r.applicable else None)),
            "final_score": (r.final_score if r.final_answer is not None else r.score),
            "final_earned": (r.final_earned if r.final_answer is not None else round(r.earned, 4)),
            "appeal": appeal_dict(current) if current else None, "appeals": [appeal_dict(a) for a in appeals],
            # the card's criterion detail (WP-1, WP-2): the definition, both answers typed like options[].value, the final share
            "section": cdef.section if cdef is not None else None, "is_critical": bool(cdef is not None and cdef.is_critical),
            "critical_value": cdef.critical_value if cdef is not None else None, "na_allowed": bool(cdef is not None and cdef.na_allowed),
            "na_allowed_for_channel": na_allowed_on_channel(cdef, channel),
            "answer_option": answer_option(r, opts), "final_option": final_option(r, opts), "final_breakdown": breakdown}


def evaluation_dict(db: Session, ev: Evaluation, include_criteria: bool = True) -> dict[str, Any]:
    defs = {c.code: c for c in db.execute(select(Criterion).where(Criterion.checklist_version_id == ev.checklist_version_id)).scalars()}
    d = {"id": ev.id, "conversation_id": ev.conversation_id, "checklist_version_id": ev.checklist_version_id, **version_info(ev), "status": ev.status,
         "provider": ev.provider, "mode": ev.mode, "model": ev.model, "prompt_version": ev.prompt_version, "total_score": ev.total_score,
         "final_total_score": ev.final_total_score, "critical_error": ev.critical_error, "sections": ev.sections or [], "cost_usd": ev.cost_usd,
         "input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens, "latency_ms": ev.latency_ms, "job_id": ev.job_id, "error": ev.error,
         "created_at": iso_utc(ev.created_at), "completed_at": iso_utc(ev.completed_at), "review_state": review_state_of(ev),
         "attention": attention(ev)}
    if include_criteria:
        rows = sorted(ev.criteria, key=lambda r: (defs[r.code].order if r.code in defs else 99))
        conv = ev.conversation
        offsets = {t.idx: t.offset_seconds for t in conv.turns} if conv is not None and conv.channel == "voice call" else None
        version = db.get(ChecklistVersion, ev.checklist_version_id) if ev.checklist_version_id is not None else None
        breakdown = final_breakdown(ev, defs, version.scoring_rules if version is not None else None)  # once per evaluation
        channel = conv.channel if conv is not None else None
        d["criteria"] = [criterion_result(r, defs[r.code].title if r.code in defs else None, defs[r.code].answer_type if r.code in defs else None,
                                          defs[r.code].options if r.code in defs else None, offsets, max_turn_index(conv),
                                          cdef=defs.get(r.code), channel=channel,
                                          breakdown=breakdown.get(r.code) if breakdown is not None else None) for r in rows]
    return d


def trace_dict(t: LlmTrace) -> dict[str, Any]:
    return {"id": t.id, "criterion_code": t.criterion_code, "kind": t.kind, "provider": t.provider, "model": t.model, "prompt_version": t.prompt_version,
            "request": t.request, "response": t.response, "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
            "cache_read_tokens": t.cache_read_tokens, "cost_usd": t.cost_usd, "latency_ms": t.latency_ms, "created_at": iso_utc(t.created_at)}
