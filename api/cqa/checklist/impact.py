"""What-if: how a draft checklist (new weights, options, scoring rules, added or removed criteria) would change the
totals of dialogs already evaluated with a base version. Pure arithmetic with the scoring engine over the stored
answers: no model call, nothing written (docs/USER_STORIES.md CHK-04, CHK-11, CHK-18; live ask «изменить вес»).

- Answers per criterion: the reviewer's final answer when one exists (``basis="final"``, default), else the judge's;
  ``basis="ai"`` uses the judge's answers only.
- A criterion that is new in the draft has no answers yet: it is left out of the preview (treated like N/A) and listed
  in ``pending_codes``; its real effect needs a partial re-evaluation.
- A criterion whose judge-relevant fields changed (wording, options, N/A rule, overrides, gross-error flag) keeps its
  stored answer in the preview and is listed in ``rejudge_codes``: a partial re-evaluation may change the answer.
- A stored answer that is no longer one of the draft's options (e.g. a scale step was removed) is counted as N/A and
  reported in ``invalid_answers``.
"""
from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy.orm import Session

from ..analytics.service import criterion_rows, latest_evaluations
from ..models import ChecklistVersion
from ..scoring.engine import CriterionInput, answer_points, compute_score
from .compiler import judge_relevant_signature
from .service import _criterion_dict, weights_summary
from .validation import ChecklistValidationError, prepare_checklist_payload

GOOD_THRESHOLD = 85.0
LOW_THRESHOLD = 60.0
ITEMS_LIMIT = 500
BASES = ("final", "ai")


def _max_points(c: dict[str, Any]) -> float:
    if c.get("max_points") not in (None, ""):
        return float(c["max_points"])
    pts = [float(o.get("points") or 0) for o in (c.get("options") or []) if isinstance(o, dict)]
    return max(pts) if pts else 1.0


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), 2)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def impact(db: Session, base: ChecklistVersion, draft: dict[str, Any], *, conversation_ids: list[str] | None = None,
           dataset_id: int | None = None, basis: str = "final", limit: int = ITEMS_LIMIT) -> dict[str, Any]:
    if basis not in BASES:
        raise ChecklistValidationError([{"field": "basis", "message": "basis: final (с решениями проверяющих) или ai (ответы модели)."}])
    prepared = prepare_checklist_payload(draft, inherited_sections=base.sections or [], inherited_scoring_rules=base.scoring_rules or {})
    if prepared.errors:
        raise ChecklistValidationError(prepared.errors, prepared.warnings)
    criteria: list[dict[str, Any]] = prepared.payload.get("criteria") or []
    rules = prepared.payload.get("scoring_rules") or base.scoring_rules or {}
    sections = prepared.payload.get("sections") or base.sections or []

    base_defs = {c.code: _criterion_dict(c) for c in base.criteria}
    draft_codes = [c["code"] for c in criteria]
    added = [code for code in draft_codes if code not in base_defs]
    removed = [code for code in base_defs if code not in draft_codes]
    rejudge, weight_changed, other_changed = [], [], []
    for c in criteria:
        b = base_defs.get(c["code"])
        if b is None:
            continue
        if judge_relevant_signature(c) != judge_relevant_signature(b):
            rejudge.append(c["code"])
        if float(c.get("weight") or 0) != float(b.get("weight") or 0):
            weight_changed.append(c["code"])
        if (c.get("section") != b.get("section")) or (_max_points(c) != float(b.get("max_points") or _max_points(b))):
            other_changed.append(c["code"])
    rules_changed = (prepared.payload.get("scoring_rules") is not None) and prepared.payload.get("scoring_rules") != (base.scoring_rules or {})

    evaluations = latest_evaluations(db, base.id)
    if conversation_ids is not None:
        wanted = {str(x) for x in conversation_ids}
        evaluations = [ev for ev in evaluations if ev.conversation_id in wanted]
    if dataset_id is not None:
        evaluations = [ev for ev in evaluations if ev.conversation is not None and ev.conversation.dataset_id == dataset_id]
    rows_by_ev = criterion_rows(db, evaluations)

    items: list[dict[str, Any]] = []
    invalid: dict[str, int] = {}
    for ev in evaluations:
        rows = {r.code: r for r in rows_by_ev.get(ev.id, [])}
        inputs: list[CriterionInput] = []
        for c in criteria:
            r = rows.get(c["code"])
            if r is None:
                inputs.append(CriterionInput(c["code"], float(c.get("weight") or 0), _max_points(c), None, False, c.get("section"),
                                             bool(c.get("is_critical")), False))
                continue
            use_final = basis == "final" and r.final_answer is not None
            answer = r.final_answer if use_final else r.answer
            applicable = (r.final_answer != "N/A") if use_final else bool(r.applicable)
            pts = answer_points(c.get("options") or [], answer) if (applicable and answer not in (None, "N/A")) else None
            if applicable and answer not in (None, "N/A") and pts is None:
                invalid[c["code"]] = invalid.get(c["code"], 0) + 1
            critical_hit = bool(c.get("is_critical") and applicable and answer is not None
                                and str(answer).lower() == str(c.get("critical_value") or "yes").lower())
            inputs.append(CriterionInput(c["code"], float(c.get("weight") or 0), _max_points(c), pts, bool(applicable and pts is not None),
                                         c.get("section"), bool(c.get("is_critical")), critical_hit))
        score = compute_score(rules, inputs, sections)
        before = ev.final_total_score if basis == "final" else ev.total_score
        before_critical = bool(ev.critical_error) if basis == "final" else any(
            r.code in base_defs and base_defs[r.code].get("is_critical") and r.applicable
            and str(r.answer).lower() == str(base_defs[r.code].get("critical_value") or "yes").lower() for r in rows.values())
        after = score.total
        conv = ev.conversation
        items.append({"conversation_id": ev.conversation_id, "evaluation_id": ev.id,
                      "language": conv.language if conv else None, "channel": conv.channel if conv else None,
                      "operator_name": conv.operator_name if conv else None,
                      "before": _round(before), "after": _round(after),
                      "delta": _round(after - before) if (after is not None and before is not None) else None,
                      "critical_before": before_critical, "critical_after": bool(score.critical)})

    deltas = [i["delta"] for i in items if i["delta"] is not None]
    befores = [i["before"] for i in items if i["before"] is not None]
    afters = [i["after"] for i in items if i["after"] is not None]
    changed = [d for d in deltas if abs(d) >= 0.005]
    summary = {
        "n": len(items), "mean_before": _mean(befores), "mean_after": _mean(afters),
        "mean_delta": _round(_mean(afters) - _mean(befores)) if befores and afters else None,
        "changed": len(changed), "up": sum(1 for d in changed if d > 0), "down": sum(1 for d in changed if d < 0),
        "max_up": max(changed) if any(d > 0 for d in changed) else 0.0, "max_down": min(changed) if any(d < 0 for d in changed) else 0.0,
        "below_low_before": sum(1 for b in befores if b < LOW_THRESHOLD), "below_low_after": sum(1 for a in afters if a < LOW_THRESHOLD),
        "good_before": sum(1 for b in befores if b >= GOOD_THRESHOLD), "good_after": sum(1 for a in afters if a >= GOOD_THRESHOLD),
        "critical_before": sum(1 for i in items if i["critical_before"]), "critical_after": sum(1 for i in items if i["critical_after"]),
        "good_threshold": GOOD_THRESHOLD, "low_threshold": LOW_THRESHOLD,
    }
    items.sort(key=lambda i: (-(abs(i["delta"]) if i["delta"] is not None else -1), i["conversation_id"]))
    notes = []
    if added:
        notes.append(f"Новые критерии ({', '.join(added)}) ещё не оценены: в предпросмотре они не учитываются, как N/A. "
                     "Точный итог — после частичной переоценки.")
    if rejudge:
        notes.append(f"Изменена формулировка или правила ({', '.join(rejudge)}): в предпросмотре — прежние ответы, "
                     "после частичной переоценки ответы могут измениться.")
    if invalid:
        notes.append("Сохранённые ответы больше не входят в варианты: " + ", ".join(f"{k} ({v})" for k, v in invalid.items())
                     + " — учтены как N/A.")
    return {
        "base_version_id": base.id, "checklist_id": base.checklist_id, "basis": basis, "model_calls": 0,
        "scope": {"evaluations": len(items), "conversation_ids": conversation_ids, "dataset_id": dataset_id},
        "changes": {"added": added, "removed": removed, "rejudge_codes": added + rejudge, "pending_codes": added,
                    "weight_changed": weight_changed, "other_changed": other_changed, "scoring_rules_changed": rules_changed,
                    "weight_only": not (added or rejudge or rules_changed) and bool(weight_changed or removed or other_changed)},
        "weights": weights_summary(criteria),
        "summary": summary,
        "invalid_answers": invalid,
        "notes": notes,
        "warnings": prepared.warnings,
        "items": items[:max(0, limit)],
        "items_truncated": len(items) > limit,
    }
