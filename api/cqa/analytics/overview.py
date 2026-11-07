"""Executive overview for directors and QA leads: ``GET /api/overview`` (see docs/arch/overview.md).

One call returns every panel of the executive page: coverage, quality distribution, gross violations, the appeals
funnel, AI-vs-reviewer agreement, quality by language and channel, per-criterion pass rates and lost points, the top
failing criteria with an example, a daily trend, cost, first response and operators at risk.

Definitions
-----------
* Scope: the latest completed evaluation per conversation (``service.latest_evaluations``), optionally restricted to
  one checklist version (no filter = every version, exactly like ``/api/analytics/summary``).
* Final values everywhere: totals are ``final_total_score`` and the critical flag is ``critical_error`` (both
  recomputed after reviewer decisions); a criterion's answer is the reviewer's answer where a decision exists,
  otherwise the AI answer. Only the ``agreement`` panel compares AI answers with final answers.
* Lost points of a criterion in one evaluation = ``weight_applied - earned`` on the 0-100 total scale, recomputed with
  the scoring engine from the final answers, so a reviewer's N/A decision redistributes the weights exactly as the
  final total does. Per evaluation, the lost points of all criteria add up to ``100 - final total``.
* ``pass_rate`` (binary criteria): share of passing answers among applicable answered rows; passing is "yes" for an
  ordinary criterion and "not the critical value" for a critical one (C8 «Грубые ошибки»: pass = no gross error).
  ``mean_ratio`` (scale criteria): mean of points / max_points among applicable answered rows. Rows the judge could
  not answer (``answer = null`` on an applicable row) are left out of every rate.
* Shares and rates are fractions 0..1 (4 decimals); ``coverage.pct`` is a percentage 0..100.
* The trend buckets the latest evaluations by the UTC date of ``completed_at`` over the last ``days`` days, one item
  per day (empty days included). Other panels describe the current state and do not depend on ``days``, except
  ``cost.period_usd``.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Appeal,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    LlmTrace,
)
from ..scoring.engine import CriterionInput, answer_points, compute_score
from .service import (
    UNKNOWN_OPERATOR,
    cohen_kappa,
    criterion_definitions,
    criterion_rows,
    first_response_threshold,
    latest_evaluations,
)
from .violations import VIOLATION_TYPES, violations
from ..turnrefs import display_turn_refs

GOOD_TOTAL = 85.0  # quality.share_ge_85
LOW_TOTAL = 60.0  # quality.share_lt_60 and operators at risk (mean below this)
DEFAULT_DAYS = 30
MAX_DAYS = 365
TOP_FAILING = 3
MAX_EVIDENCE = 3
NA = "N/A"
UNKNOWN = UNKNOWN_OPERATOR  # "—": missing operator name, language or channel
EPS = 1e-9
_NA_CATEGORY = "n/a"
_EVIDENCE_ORDER = {"supports": 0, "contradicts": 1, "context": 2}
_YES = frozenset({"yes", "да", "иә", "true", "1"})


# --------------------------------------------------------------------------- small helpers


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite gives back naive datetimes (stored as UTC), Postgres aware ones: normalise both to aware UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    value = as_utc(dt)
    return value.isoformat() if value is not None else None


def rnd(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def mean(values: Sequence[float], digits: int = 2) -> float | None:
    return rnd(statistics.fmean(values), digits) if values else None


def rate(part: float, whole: float, digits: int = 4) -> float | None:
    return round(part / whole, digits) if whole else None


def percentile(sorted_values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default) over an ascending list."""
    if not sorted_values:
        return None
    h = (len(sorted_values) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def is_na(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == NA


def is_yes(answer: Any) -> bool:
    """Same semantics as cqa.analytics.service: "yes", "Да", "иә", true and 1 count as yes."""
    if isinstance(answer, bool):
        return answer
    return str(answer).strip().lower() in _YES


def category(answer: Any) -> str | None:
    """Canonical answer category for agreement statistics (15, "15" and 15.0 are one answer; "N/A" -> "n/a")."""
    if answer is None:
        return None
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    if isinstance(answer, (int, float)):
        return repr(float(answer))
    text = str(answer).strip().lower()
    try:
        return repr(float(text))
    except ValueError:
        return text


def split_model(provider: str | None, model: str | None) -> tuple[str, str]:
    """``Evaluation.model`` is "provider:model" for one judge or a comma-separated list for an ensemble."""
    provider = provider or UNKNOWN
    if not model:
        return provider, UNKNOWN
    head, sep, tail = model.partition(":")
    if sep and "," not in model and head == provider and tail:
        return provider, tail
    return provider, model


# --------------------------------------------------------------------------- final (post-review) views


@dataclass
class RowView:
    """One criterion result with final (post-review) values and points recomputed by the scoring engine."""

    row: EvaluationCriterion
    definition: Criterion | None
    answer_type: str  # binary | scale
    is_critical: bool
    na: bool  # the final answer is N/A
    answer: Any  # final answer; None when N/A or when the judge gave no answer
    points: float | None  # final points
    ratio: float | None  # answered rows: scale points / max_points, binary 1.0 (pass) or 0.0
    passed: bool | None  # answered binary rows
    weight_applied: float = 0.0
    earned: float = 0.0

    @property
    def code(self) -> str:
        return self.row.code

    @property
    def answered(self) -> bool:
        return not self.na and self.answer is not None and self.points is not None

    @property
    def changed(self) -> bool:
        return self.row.review_state == "changed"

    @property
    def lost(self) -> float:
        return max(0.0, self.weight_applied - self.earned) if self.answered else 0.0

    @property
    def ai_answer(self) -> Any:
        return self.row.answer if self.row.applicable else NA

    @property
    def final_answer(self) -> Any:
        return NA if self.na else self.answer


@dataclass
class EvalView:
    """Latest completed evaluation of one conversation with its final criterion rows."""

    ev: Evaluation
    rows: list[RowView]

    @property
    def conv(self) -> Conversation | None:
        return self.ev.conversation

    @property
    def total(self) -> float | None:
        return None if self.ev.final_total_score is None else float(self.ev.final_total_score)

    @property
    def critical(self) -> bool:
        return bool(self.ev.critical_error)

    @property
    def operator_name(self) -> str:
        return ((self.conv.operator_name if self.conv is not None else None) or "").strip() or UNKNOWN

    @property
    def language(self) -> str:
        return (self.conv.language if self.conv is not None else None) or UNKNOWN

    @property
    def channel(self) -> str:
        return (self.conv.channel if self.conv is not None else None) or UNKNOWN

    @property
    def completed_at(self) -> datetime | None:
        return as_utc(self.ev.completed_at)


def _answer_type(r: EvaluationCriterion, d: Criterion | None) -> str:
    if d is not None and d.answer_type:
        return d.answer_type
    return "scale" if isinstance(r.answer, (int, float)) and not isinstance(r.answer, bool) else "binary"


def row_view(r: EvaluationCriterion, d: Criterion | None) -> RowView:
    answer_type = _answer_type(r, d)
    is_critical = bool(d is not None and d.is_critical)
    points: float | None
    if r.final_answer is not None:  # a reviewer decided (confirm keeps the AI answer, change replaces it)
        if is_na(r.final_answer):
            na, answer, points = True, None, None
        else:
            na, answer = False, r.final_answer
            points = r.final_score if r.final_score is not None else (answer_points(d.options or [], answer) if d is not None else None)
    elif not r.applicable:
        na, answer, points = True, None, None
    else:
        na, answer, points = False, r.answer, r.score
    passed: bool | None = None
    ratio: float | None = None
    if not na and answer is not None and points is not None:
        if answer_type == "scale":
            max_points = float(r.max_points or (d.max_points if d is not None else 0) or 0)
            ratio = max(0.0, min(1.0, float(points) / max_points)) if max_points > 0 else None
        else:
            if is_critical:
                critical_value = str((d.critical_value if d is not None else None) or "yes").strip().lower()
                passed = str(answer).strip().lower() != critical_value
            else:
                passed = is_yes(answer)
            ratio = 1.0 if passed else 0.0
    return RowView(row=r, definition=d, answer_type=answer_type, is_critical=is_critical, na=na, answer=answer,
                   points=None if points is None else float(points), ratio=ratio, passed=passed)


def _apply_scoring(rows: list[RowView], rules: dict[str, Any] | None) -> None:
    """weight_applied / earned of every row from the final answers (same engine and inputs as review.recompute_final)."""
    items: list[CriterionInput] = []
    for rv in rows:
        d, r = rv.definition, rv.row
        weight = float(d.weight if d is not None else (r.weight or 0.0))
        max_points = float(d.max_points if d is not None else (r.max_points or 0.0))
        items.append(CriterionInput(code=rv.code, weight=weight, max_points=max_points, points=rv.points if rv.answered else None,
                                    applicable=rv.answered, section=d.section if d is not None else None,
                                    is_critical=rv.is_critical, critical_hit=bool(rv.is_critical and rv.passed is False)))
    result = compute_score(rules or {}, items)
    for rv in rows:
        cr = result.criteria.get(rv.code)
        if cr is not None:
            rv.weight_applied, rv.earned = cr.weight_applied, cr.earned


def _versions(db: Session, ids: Iterable[int]) -> dict[int, ChecklistVersion]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    stmt = select(ChecklistVersion).where(ChecklistVersion.id.in_(wanted)).options(selectinload(ChecklistVersion.criteria))
    return {v.id: v for v in db.scalars(stmt)}


def load_views(db: Session, checklist_version_id: int | None = None) -> list[EvalView]:
    """Latest completed evaluation per conversation (ordered by conversation id) with final criterion rows."""
    latest = latest_evaluations(db, checklist_version_id)
    rows_by_eval = criterion_rows(db, latest)
    definitions = criterion_definitions(db, [r for ev in latest for r in rows_by_eval.get(ev.id, [])])
    versions = _versions(db, (ev.checklist_version_id for ev in latest))
    views: list[EvalView] = []
    for ev in latest:
        version = versions.get(ev.checklist_version_id)
        by_code = {c.code: c for c in version.criteria} if version is not None else {}
        rows = [row_view(r, definitions.get(r.criterion_id) or by_code.get(r.code)) for r in rows_by_eval.get(ev.id, [])]
        _apply_scoring(rows, version.scoring_rules if version is not None else None)
        views.append(EvalView(ev=ev, rows=rows))
    return views


def scope_definitions(db: Session, checklist_version_id: int | None, views: Sequence[EvalView]) -> dict[str, Criterion]:
    """Criterion definitions that label the panels, by code.

    The requested version; without a filter the version most latest evaluations use, else the active one. Codes seen
    only in evaluations of other versions keep the definition of the newest version that has them.
    """
    version_id = checklist_version_id
    if version_id is None and views:
        counts = Counter(v.ev.checklist_version_id for v in views)
        version_id = max(counts, key=lambda vid: (counts[vid], vid))
    if version_id is None:
        version_id = db.scalar(
            select(ChecklistVersion.id).where(ChecklistVersion.is_active.is_(True)).order_by(ChecklistVersion.id.desc()).limit(1)
        )
    out: dict[str, Criterion] = {}
    if version_id is not None:
        stmt = select(Criterion).where(Criterion.checklist_version_id == version_id).order_by(Criterion.order, Criterion.id)
        out = {c.code: c for c in db.scalars(stmt)}
    for v in sorted(views, key=lambda v: v.ev.checklist_version_id, reverse=True):
        for rv in v.rows:
            if rv.code not in out and rv.definition is not None:
                out[rv.code] = rv.definition
    return out


def ordered_codes(definitions: dict[str, Criterion], views: Sequence[EvalView]) -> list[str]:
    codes = set(definitions) | {rv.code for v in views for rv in v.rows}
    return sorted(codes, key=lambda code: (definitions[code].order if code in definitions else 10**6, code))


# --------------------------------------------------------------------------- panels shared with coaching


def criteria_stats(views: Sequence[EvalView], definitions: dict[str, Criterion], codes: Sequence[str]) -> list[dict[str, Any]]:
    """Per-criterion pass rate / mean ratio, N/A rate and lost points over the given evaluations (checklist order)."""
    acc: dict[str, dict[str, Any]] = {}

    def bucket(code: str) -> dict[str, Any]:
        return acc.setdefault(code, {"na": 0, "answered": 0, "passed": 0, "ratios": [], "lost": 0.0, "losing": 0, "type": None})

    for code in codes:
        bucket(code)
    for v in views:
        for rv in v.rows:
            a = bucket(rv.code)
            a["type"] = a["type"] or rv.answer_type
            if rv.na:
                a["na"] += 1
            elif rv.answered:
                a["answered"] += 1
                a["passed"] += 1 if rv.passed else 0
                if rv.ratio is not None:
                    a["ratios"].append(rv.ratio)
            if rv.lost > EPS:
                a["lost"] += rv.lost
                a["losing"] += 1
    total_lost = sum(a["lost"] for a in acc.values())
    out: list[dict[str, Any]] = []
    for code in codes:
        a = acc[code]
        d = definitions.get(code)
        answer_type = (d.answer_type if d is not None else None) or a["type"] or "binary"
        is_scale = answer_type == "scale"
        n = a["answered"] + a["na"]
        out.append({
            "code": code,
            "title": d.title if d is not None else code,
            "section": d.section if d is not None else None,
            "weight": rnd(d.weight) if d is not None else None,
            "answer_type": answer_type,
            "is_critical": bool(d is not None and d.is_critical),
            "n": n,
            "n_applicable": a["answered"],
            "pass_rate": None if is_scale else rate(a["passed"], a["answered"]),
            "mean_ratio": rnd(statistics.fmean(a["ratios"]), 4) if is_scale and a["ratios"] else None,
            "na_rate": rate(a["na"], n),
            "lost_points": round(a["lost"], 2),
            "lost_points_share": rate(a["lost"], total_lost) if total_lost > EPS else None,
            "dialogs_losing_points": a["losing"],
        })
    return out


def example_rank(item: tuple[EvalView, RowView]) -> tuple[Any, ...]:
    """Most representative failure first: verdict not overturned, has an explanation, biggest loss, lowest total."""
    v, rv = item
    return (rv.changed, not (rv.row.explanation or "").strip(), -rv.lost, v.total if v.total is not None else 101.0, v.ev.conversation_id)


def evidence_items(conv: Conversation | None, evidence: Any, limit: int = MAX_EVIDENCE) -> list[dict[str, Any]]:
    """Up to ``limit`` quotes, the ones that support the verdict first (transcript order kept within a role)."""
    turns = {t.idx: t for t in conv.turns} if conv is not None else {}
    is_call = conv is not None and conv.channel == "voice call"
    items = sorted((e for e in evidence or [] if isinstance(e, dict)), key=lambda e: _EVIDENCE_ORDER.get(e.get("role"), len(_EVIDENCE_ORDER)))
    out: list[dict[str, Any]] = []
    for e in items:
        t = turns.get(e.get("turn_idx"))
        offset = e.get("offset_seconds")
        out.append({
            "turn_idx": e.get("turn_idx"),
            "speaker": e.get("speaker") or (t.speaker if t is not None else None),
            "quote": e.get("quote"),
            "role": e.get("role"),
            "timestamp": t.timestamp_raw if t is not None else None,
            # calls: the seek time of the current transcript (offsets may be re-derived after judging, cqa.ingest.renormalize)
            "offset_seconds": (t.offset_seconds if (t is not None and is_call) else
                               (offset if offset is not None else (t.offset_seconds if t is not None else None))),
        })
        if len(out) >= limit:
            break
    return out


def reviewer_reasons(db: Session, row_ids: Iterable[int]) -> dict[int, str]:
    """Reason of the latest reviewer decision per evaluation criterion row."""
    ids = sorted(set(row_ids))
    if not ids:
        return {}
    out: dict[int, str] = {}
    stmt = select(Appeal).where(Appeal.evaluation_criterion_id.in_(ids)).options(selectinload(Appeal.decision)).order_by(Appeal.id)
    for ap in db.scalars(stmt):
        if ap.decision is not None and ap.decision.reason:
            out[ap.evaluation_criterion_id] = ap.decision.reason
    return out


def example_items(db: Session, chosen: Sequence[tuple[EvalView, RowView]]) -> list[dict[str, Any]]:
    reasons = reviewer_reasons(db, [rv.row.id for _, rv in chosen if rv.row.review_state in ("confirmed", "changed")])
    return [
        {
            "conversation_id": v.ev.conversation_id,
            "evaluation_id": v.ev.id,
            "evaluation_criterion_id": rv.row.id,
            "operator_name": v.conv.operator_name if v.conv is not None else None,
            "channel": v.channel,
            "language": v.language,
            "final_total_score": rnd(v.total),
            "ai_answer": rv.ai_answer,
            "final_answer": rv.final_answer,
            "lost_points": round(rv.lost, 2),
            "explanation": display_turn_refs(rv.row.explanation, (v.conv.turn_count - 1) if v.conv is not None and v.conv.turn_count else None),
            "evidence": evidence_items(v.conv, rv.row.evidence),
            "review_state": rv.row.review_state,
            "reviewer_reason": reasons.get(rv.row.id),
        }
        for v, rv in chosen
    ]


def appeal_funnel(appeals: Sequence[Appeal], now: datetime) -> dict[str, Any]:
    """Counts, overturn rate and decision speed over the given appeals."""
    decided = [a for a in appeals if a.decision is not None or a.status == "decided"]
    open_ = [a for a in appeals if a.status == "open" and a.decision is None]
    changed = sum(1 for a in decided if a.decision is not None and a.decision.action == "change")
    confirmed = sum(1 for a in decided if a.decision is not None and a.decision.action == "confirm")
    hours: list[float] = []
    for a in decided:
        start = as_utc(a.created_at)
        end = as_utc(a.decision.created_at) if a.decision is not None else None
        if start is not None and end is not None:
            hours.append(max(0.0, (end - start).total_seconds() / 3600.0))
    waits = [max(0.0, (now - created).total_seconds() / 3600.0) for created in (as_utc(a.created_at) for a in open_) if created is not None]
    return {
        "total": len(appeals),
        "open": len(open_),
        "decided": len(decided),
        "changed": changed,
        "confirmed": confirmed,
        "overturn_rate": rate(changed, len(decided)),
        "mean_hours_to_decision": mean(hours),
        "oldest_open_hours": rnd(max(waits)) if waits else None,
    }


# --------------------------------------------------------------------------- overview-only panels


def _agreement(views: Sequence[EvalView], definitions: dict[str, Criterion], codes: Sequence[str]) -> list[dict[str, Any]]:
    """AI answer vs final answer on reviewed rows (confirmed or changed); N/A is a category of its own."""
    pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for v in views:
        for rv in v.rows:
            r = rv.row
            if r.review_state not in ("confirmed", "changed"):
                continue
            ai = _NA_CATEGORY if not r.applicable else category(r.answer)
            if ai is None:
                continue
            final = category(r.final_answer) if r.final_answer is not None else ai
            pairs[rv.code].append((ai, final or ai))
    out = []
    for code in codes:
        p = pairs.get(code, [])
        d = definitions.get(code)
        out.append({
            "code": code,
            "title": d.title if d is not None else code,
            "n_reviewed": len(p),
            "agreement_rate": rate(sum(1 for a, b in p if a == b), len(p)),
            "kappa": cohen_kappa(p),
        })
    return out


def _groups(views: Sequence[EvalView], attr: str) -> list[dict[str, Any]]:
    groups: dict[str, list[EvalView]] = defaultdict(list)
    for v in views:
        groups[getattr(v, attr)].append(v)
    return [
        {attr: name, "n": len(vs), "mean": mean([v.total for v in vs if v.total is not None]), "critical": sum(1 for v in vs if v.critical)}
        for name, vs in sorted(groups.items())
    ]


def _top_failing(db: Session, views: Sequence[EvalView], criteria: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted((c for c in criteria if c["lost_points"] > 0), key=lambda c: -c["lost_points"])[:TOP_FAILING]
    out = []
    for c in ranked:
        candidates = [(v, rv) for v in views for rv in v.rows if rv.code == c["code"] and rv.lost > EPS]
        examples = example_items(db, [min(candidates, key=example_rank)]) if candidates else []
        out.append({
            "code": c["code"],
            "title": c["title"],
            "answer_type": c["answer_type"],
            "weight": c["weight"],
            "lost_points": c["lost_points"],
            "lost_points_share": c["lost_points_share"],
            "dialogs_losing_points": c["dialogs_losing_points"],
            "pass_rate": c["pass_rate"],
            "mean_ratio": c["mean_ratio"],
            "example": examples[0] if examples else None,
        })
    return out


def _trend(views: Sequence[EvalView], start: date, end: date) -> list[dict[str, Any]]:
    buckets: dict[date, list[EvalView]] = {start + timedelta(days=i): [] for i in range((end - start).days + 1)}
    for v in views:
        ts = v.completed_at
        if ts is not None and ts.date() in buckets:
            buckets[ts.date()].append(v)
    return [
        {
            "date": day.isoformat(),
            "evaluated": len(vs),
            "mean_total": mean([v.total for v in vs if v.total is not None]),
            "critical": sum(1 for v in vs if v.critical),
        }
        for day, vs in sorted(buckets.items())
    ]


def _critical(db: Session, checklist_version_id: int | None, views: Sequence[EvalView], scope_ids: set[str] | None = None) -> dict[str, Any]:
    listed = violations(db, checklist_version_id=checklist_version_id)
    if scope_ids is not None:
        items = [i for i in listed.get("items") or [] if i.get("conversation_id") in scope_ids]
        counts_scoped: dict[str, int] = {k: 0 for k in VIOLATION_TYPES}
        for i in items:
            for k in i.get("types") or []:
                counts_scoped[k] = counts_scoped.get(k, 0) + 1
        listed = {**listed, "items": items, "counts": counts_scoped}
    counts = listed.get("counts") or {}
    count = sum(1 for v in views if v.critical)
    return {
        "count": count,
        "rate": rate(count, len(views)),
        "by_type": [{"code": code, **labels, "count": int(counts.get(code, 0))} for code, labels in VIOLATION_TYPES.items()],
        "reviewed": sum(1 for item in listed.get("items") or [] if item.get("review_state") in ("confirmed", "changed")),
    }


def _appeals(db: Session, checklist_version_id: int | None, now: datetime, scope_ids: set[str] | None = None) -> dict[str, Any]:
    stmt = select(Appeal).options(selectinload(Appeal.decision)).order_by(Appeal.id)
    if checklist_version_id is not None or scope_ids is not None:
        stmt = (
            stmt.join(EvaluationCriterion, Appeal.evaluation_criterion_id == EvaluationCriterion.id)
            .join(Evaluation, EvaluationCriterion.evaluation_id == Evaluation.id)
        )
        if checklist_version_id is not None:
            stmt = stmt.where(Evaluation.checklist_version_id == checklist_version_id)
        if scope_ids is not None:
            stmt = stmt.where(Evaluation.conversation_id.in_(scope_ids or {""}))
    return appeal_funnel(list(db.scalars(stmt)), now)


def _cost(db: Session, checklist_version_id: int | None, period_start: datetime, evaluated: int,
          scope_ids: set[str] | None = None) -> dict[str, Any]:
    """Spend over every evaluation run in scope (any status), split by model from the LLM traces.

    Evaluations without traces (older data, failures before any call) are attributed to their own provider/model.
    ``per_dialog_usd`` is fully loaded (all spend ÷ evaluated dialogs); ``per_evaluation_usd`` is the price of one full
    evaluation: completed runs except partial re-evaluations (they judge only the changed criteria), averaged over
    ``evaluations_completed`` of them — the same figure as ``per_evaluation`` of GET /api/analytics/summary.
    """
    ev_stmt = select(Evaluation.id, Evaluation.provider, Evaluation.model, Evaluation.cost_usd, Evaluation.input_tokens,
                     Evaluation.output_tokens, Evaluation.created_at, Evaluation.completed_at, Evaluation.status, Evaluation.mode)
    tr_stmt = (
        select(LlmTrace.evaluation_id, LlmTrace.provider, LlmTrace.model, func.count(LlmTrace.id), func.sum(LlmTrace.cost_usd),
               func.sum(LlmTrace.input_tokens), func.sum(LlmTrace.output_tokens), func.sum(LlmTrace.cache_read_tokens))
        .group_by(LlmTrace.evaluation_id, LlmTrace.provider, LlmTrace.model)
    )
    if checklist_version_id is not None or scope_ids is not None:
        tr_stmt = tr_stmt.join(Evaluation, Evaluation.id == LlmTrace.evaluation_id)
    if checklist_version_id is not None:
        ev_stmt = ev_stmt.where(Evaluation.checklist_version_id == checklist_version_id)
        tr_stmt = tr_stmt.where(Evaluation.checklist_version_id == checklist_version_id)
    if scope_ids is not None:
        ev_stmt = ev_stmt.where(Evaluation.conversation_id.in_(scope_ids or {""}))
        tr_stmt = tr_stmt.where(Evaluation.conversation_id.in_(scope_ids or {""}))
    evaluations = db.execute(ev_stmt).all()
    models: dict[tuple[str, str], dict[str, float]] = {}

    def add(provider: str, model: str, requests: int, cost: float | None, tokens_in: int | None, tokens_out: int | None) -> None:
        m = models.setdefault((provider, model), {"requests": 0, "cost": 0.0, "in": 0, "out": 0})
        m["requests"] += int(requests or 0)
        m["cost"] += float(cost or 0.0)
        m["in"] += int(tokens_in or 0)
        m["out"] += int(tokens_out or 0)

    traced: set[int] = set()
    cache_tokens = 0
    for eval_id, provider, model, requests, cost, tokens_in, tokens_out, cache in db.execute(tr_stmt):
        traced.add(eval_id)
        add(provider or UNKNOWN, model or UNKNOWN, requests, cost, tokens_in, tokens_out)
        cache_tokens += int(cache or 0)
    for e in evaluations:
        if e.id in traced or not (e.cost_usd or e.input_tokens or e.output_tokens):
            continue
        add(*split_model(e.provider, e.model), 0, e.cost_usd, e.input_tokens, e.output_tokens)

    total = sum(float(e.cost_usd or 0.0) for e in evaluations)
    full = [e for e in evaluations if e.status == "completed" and e.mode != "partial"]
    period = 0.0
    for e in evaluations:
        ts = as_utc(e.completed_at) or as_utc(e.created_at)
        if ts is not None and ts >= period_start:
            period += float(e.cost_usd or 0.0)
    model_total = sum(m["cost"] for m in models.values())
    by_model = [
        {
            "provider": provider,
            "model": model,
            "requests": int(m["requests"]),
            "tokens_in": int(m["in"]),
            "tokens_out": int(m["out"]),
            "cost_usd": round(m["cost"], 4),
            "share": rate(m["cost"], model_total),
        }
        for (provider, model), m in models.items()
    ]
    by_model.sort(key=lambda m: (-m["cost_usd"], m["provider"], m["model"]))
    return {
        "total_usd": round(total, 4),
        "per_dialog_usd": round(total / evaluated, 4) if evaluated else None,
        # fmean (exact sum ÷ n) as in the summary, so both endpoints show the same number
        "per_evaluation_usd": rnd(statistics.fmean(float(e.cost_usd or 0.0) for e in full), 4) if full else None,
        "evaluations_completed": len(full),
        "evaluations": len(evaluations),
        "tokens_in": sum(int(e.input_tokens or 0) for e in evaluations),
        "tokens_out": sum(int(e.output_tokens or 0) for e in evaluations),
        "cache_read_tokens": cache_tokens,
        "period_usd": round(period, 4),
        "by_model": by_model,
    }


def _first_response(db: Session, checklist_version_id: int | None, scope_ids: set[str] | None = None) -> dict[str, Any]:
    """Same figures as /api/analytics/summary: every conversation with a measured first response."""
    threshold = first_response_threshold(db, checklist_version_id)
    stmt = select(Conversation.first_response_seconds).where(Conversation.first_response_seconds.is_not(None))
    if scope_ids is not None:
        stmt = stmt.where(Conversation.id.in_(scope_ids or {""}))
    values = [float(v) for v in db.scalars(stmt)]
    return {
        "mean_seconds": rnd(statistics.fmean(values), 1) if values else None,
        "over_threshold_share": rate(sum(1 for v in values if v > threshold), len(values)),
        "threshold_seconds": threshold,
        "n": len(values),
    }


def _operators(db: Session, views: Sequence[EvalView], scope_ids: set[str] | None = None) -> dict[str, Any]:
    names_stmt = select(Conversation.operator_name).distinct()
    if scope_ids is not None:
        names_stmt = names_stmt.where(Conversation.id.in_(scope_ids or {""}))
    named = {n for n in db.scalars(names_stmt) if n and n.strip()}
    groups: dict[str, list[EvalView]] = defaultdict(list)
    for v in views:
        groups[v.operator_name].append(v)
    at_risk = []
    for name, vs in groups.items():
        totals = [v.total for v in vs if v.total is not None]
        raw_mean = statistics.fmean(totals) if totals else None
        critical_count = sum(1 for v in vs if v.critical)
        reasons = (["critical"] if critical_count else []) + (["low_mean"] if raw_mean is not None and raw_mean < LOW_TOTAL else [])
        if reasons:
            at_risk.append({"operator_name": name, "n": len(vs), "mean_total": rnd(raw_mean), "critical_count": critical_count,
                            "reasons": reasons})
    at_risk.sort(key=lambda o: (o["operator_name"] == UNKNOWN, -o["critical_count"],
                                o["mean_total"] if o["mean_total"] is not None else 101.0, o["operator_name"]))
    return {
        "count": len(named),
        "evaluated": sum(1 for name in groups if name != UNKNOWN),
        "at_risk_threshold": LOW_TOTAL,
        "at_risk": at_risk,
    }


# --------------------------------------------------------------------------- entry point


def scope_conversation_ids(db: Session, *, language: str | None = None, channel: str | None = None, dataset_id: int | None = None,
                           operator: str | None = None) -> set[str] | None:
    """Conversation ids matching the scope filters (None when no filter is set: everything is in scope). Operator "—" is the
    unnamed-operator group (no name or a blank one), as in GET /api/operators and the scorecard GET /api/operators/—."""
    if not (language or channel or dataset_id is not None or operator):
        return None
    stmt = select(Conversation.id)
    if language:
        stmt = stmt.where(Conversation.language == language.upper())
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if dataset_id is not None:
        stmt = stmt.where(Conversation.dataset_id == dataset_id)
    if operator:
        if operator.strip() == UNKNOWN:
            stmt = stmt.where(or_(Conversation.operator_name.is_(None), func.trim(Conversation.operator_name) == ""))
        else:
            stmt = stmt.where(Conversation.operator_name == operator)
    return set(db.scalars(stmt))


def overview(db: Session, checklist_version_id: int | None = None, days: int = DEFAULT_DAYS, *, now: datetime | None = None,
             language: str | None = None, channel: str | None = None, dataset_id: int | None = None,
             operator: str | None = None) -> dict[str, Any]:
    """Executive KPIs; see the module docstring for definitions and docs/arch/overview.md for the response shape.
    Optional scope (language, channel, dataset, operator) narrows every panel to those conversations (ANL-01)."""
    now = as_utc(now) or datetime.now(UTC)
    scope_ids = scope_conversation_ids(db, language=language, channel=channel, dataset_id=dataset_id, operator=operator)
    days = max(1, min(int(days), MAX_DAYS))
    end = now.date()
    start = end - timedelta(days=days - 1)
    period_start = datetime.combine(start, time.min, tzinfo=UTC)

    views = load_views(db, checklist_version_id)
    if scope_ids is not None:
        views = [v for v in views if v.ev.conversation_id in scope_ids]
    definitions = scope_definitions(db, checklist_version_id, views)
    codes = ordered_codes(definitions, views)
    criteria = criteria_stats(views, definitions, codes)

    conversations = len(scope_ids) if scope_ids is not None else int(db.scalar(select(func.count()).select_from(Conversation)) or 0)
    evaluated_ids = {v.ev.conversation_id for v in views}
    failed_stmt = select(Evaluation.conversation_id).where(Evaluation.status == "failed").distinct()
    if checklist_version_id is not None:
        failed_stmt = failed_stmt.where(Evaluation.checklist_version_id == checklist_version_id)
    failed = set(db.scalars(failed_stmt)) - evaluated_ids
    if scope_ids is not None:
        failed &= scope_ids

    totals = sorted(v.total for v in views if v.total is not None)
    window = [v for v in views if v.completed_at is not None and start <= v.completed_at.date() <= end]
    return {
        "generated_at": now.isoformat(),
        "checklist_version_id": checklist_version_id,
        "days": days,
        "scope": {"language": language.upper() if language else None, "channel": channel, "dataset_id": dataset_id, "operator": operator},
        "coverage": {
            "conversations": conversations,
            "evaluated": len(views),
            "not_evaluated": max(0, conversations - len(views)),
            "failed": len(failed),
            "pct": round(100.0 * len(views) / conversations, 2) if conversations else None,
        },
        "quality": {
            "n": len(totals),
            "mean": mean(totals),
            "median": rnd(statistics.median(totals)) if totals else None,
            "p25": rnd(percentile(totals, 0.25)),
            "p75": rnd(percentile(totals, 0.75)),
            "share_ge_85": rate(sum(1 for t in totals if t >= GOOD_TOTAL), len(totals)),
            "share_lt_60": rate(sum(1 for t in totals if t < LOW_TOTAL), len(totals)),
            "good_threshold": GOOD_TOTAL,
            "low_threshold": LOW_TOTAL,
        },
        "critical": _critical(db, checklist_version_id, views, scope_ids),
        "appeals": _appeals(db, checklist_version_id, now, scope_ids),
        "agreement": _agreement(views, definitions, codes),
        "by_language": _groups(views, "language"),
        "by_channel": _groups(views, "channel"),
        "criteria": criteria,
        "top_failing": _top_failing(db, views, criteria),
        "trend": _trend(views, start, end),
        "period": {
            "days": days,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "evaluated": len(window),
            "mean_total": mean([v.total for v in window if v.total is not None]),
            "critical": sum(1 for v in window if v.critical),
        },
        "cost": _cost(db, checklist_version_id, period_start, len(views), scope_ids),
        "first_response": _first_response(db, checklist_version_id, scope_ids),
        "operators": _operators(db, views, scope_ids),
    }
