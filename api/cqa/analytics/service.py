"""Analytics summary.

Pure-Python aggregation over ORM rows. The data set is small (a few thousand evaluations at most),
so we load the completed evaluations once and aggregate in memory instead of writing one SQL
query per panel.

Definitions
-----------
* "latest evaluation" of a conversation: the completed evaluation with the greatest id for the
  requested checklist version (any version when not filtered).
* totals use ``final_total_score`` (reviewer decisions applied; equals ``total_score`` when there
  was no decision).
* criteria aggregates use the AI answer/score over applicable rows only.
* a "reviewed" criterion row has ``review_state`` in {confirmed, changed}.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion

BUCKETS: tuple[str, ...] = ("0-59", "60-69", "70-79", "80-89", "90-100")
DEFAULT_FIRST_RESPONSE_THRESHOLD = 180.0
UNKNOWN_OPERATOR = "—"
ALL = "ALL"
_CHUNK = 500


# --------------------------------------------------------------------------- helpers


def _chunked(items: Sequence[Any], size: int = _CHUNK) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _mean(values: list[float]) -> float | None:
    return _round(statistics.fmean(values)) if values else None


def _is_yes(answer: Any) -> bool:
    if isinstance(answer, bool):
        return answer
    return str(answer).strip().lower() in {"yes", "да", "иә", "true", "1"}


def _category(answer: Any) -> str | None:
    """Canonical category for agreement statistics (15 and "15" are the same answer)."""
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


def _bucket(score: float) -> str:
    if score < 60:
        return BUCKETS[0]
    if score < 70:
        return BUCKETS[1]
    if score < 80:
        return BUCKETS[2]
    if score < 90:
        return BUCKETS[3]
    return BUCKETS[4]


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for a list of (rater_a, rater_b) categorical pairs.

    Returns None when fewer than two pairs or when the statistic is undefined (expected
    agreement equals 1, i.e. both raters always gave the same single category).
    """
    n = len(pairs)
    if n < 2:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    categories = {a for a, _ in pairs} | {b for _, b in pairs}
    expected = 0.0
    for cat in categories:
        p_a = sum(1 for a, _ in pairs if a == cat) / n
        p_b = sum(1 for _, b in pairs if b == cat) / n
        expected += p_a * p_b
    if abs(1.0 - expected) < 1e-12:
        return None
    return round((observed - expected) / (1.0 - expected), 4)


# --------------------------------------------------------------------------- data access


def completed_evaluations(db: Session, checklist_version_id: int | None = None) -> list[Evaluation]:
    """All completed evaluations (every version unless filtered), ordered by id, with conversations loaded."""
    stmt = (
        select(Evaluation)
        .where(Evaluation.status == "completed")
        .options(selectinload(Evaluation.conversation))
        .order_by(Evaluation.id)
    )
    if checklist_version_id is not None:
        stmt = stmt.where(Evaluation.checklist_version_id == checklist_version_id)
    return list(db.scalars(stmt))


def latest_evaluations(db: Session, checklist_version_id: int | None = None) -> list[Evaluation]:
    """Latest completed evaluation per conversation (greatest id), ordered by conversation id."""
    latest: dict[str, Evaluation] = {}
    for ev in completed_evaluations(db, checklist_version_id):  # ascending id → last write wins
        latest[ev.conversation_id] = ev
    return [latest[k] for k in sorted(latest)]


def criterion_rows(db: Session, evaluations: Sequence[Evaluation]) -> dict[int, list[EvaluationCriterion]]:
    """EvaluationCriterion rows grouped by evaluation id."""
    out: dict[int, list[EvaluationCriterion]] = defaultdict(list)
    ids = [ev.id for ev in evaluations]
    for chunk in _chunked(ids):
        stmt = (
            select(EvaluationCriterion)
            .where(EvaluationCriterion.evaluation_id.in_(list(chunk)))
            .order_by(EvaluationCriterion.evaluation_id, EvaluationCriterion.id)
        )
        for row in db.scalars(stmt):
            out[row.evaluation_id].append(row)
    return out


def criterion_definitions(db: Session, rows: Iterable[EvaluationCriterion]) -> dict[int, Criterion]:
    ids = sorted({r.criterion_id for r in rows if r.criterion_id is not None})
    out: dict[int, Criterion] = {}
    for chunk in _chunked(ids):
        for c in db.scalars(select(Criterion).where(Criterion.id.in_(list(chunk)))):
            out[c.id] = c
    return out


def first_response_threshold(db: Session, checklist_version_id: int | None) -> float:
    version: ChecklistVersion | None = None
    if checklist_version_id is not None:
        version = db.get(ChecklistVersion, checklist_version_id)
    else:
        version = db.scalars(select(ChecklistVersion).where(ChecklistVersion.is_active.is_(True))).first()
    rules = (version.scoring_rules or {}) if version else {}
    try:
        return float(rules.get("first_response_threshold_seconds", DEFAULT_FIRST_RESPONSE_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_FIRST_RESPONSE_THRESHOLD


# --------------------------------------------------------------------------- summary


def summary(db: Session, checklist_version_id: int | None = None) -> dict[str, Any]:
    """Dashboard summary; field names follow docs/API.md."""
    all_completed = completed_evaluations(db, checklist_version_id)
    latest_by_conv: dict[str, Evaluation] = {}
    for ev in all_completed:
        latest_by_conv[ev.conversation_id] = ev
    latest = [latest_by_conv[k] for k in sorted(latest_by_conv)]
    rows_by_eval = criterion_rows(db, latest)
    all_rows = [r for ev in latest for r in rows_by_eval.get(ev.id, [])]
    defs = criterion_definitions(db, all_rows)

    # --- totals -----------------------------------------------------------------------------
    totals = [float(ev.final_total_score) for ev in latest if ev.final_total_score is not None]
    distribution_counts = {b: 0 for b in BUCKETS}
    for t in totals:
        distribution_counts[_bucket(t)] += 1

    def _group(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[float | None]] = defaultdict(list)
        for ev in latest:
            conv = ev.conversation
            value = getattr(conv, key, None) if conv is not None else None
            groups[value or "—"].append(ev.final_total_score)
        out = []
        for name in sorted(groups):
            scores = [float(s) for s in groups[name] if s is not None]
            out.append({key: name, "mean_total": _mean(scores), "n": len(groups[name])})
        return out

    # --- criteria heatmap / NA rates / agreement -------------------------------------------
    code_order: dict[str, tuple[int, str]] = {}
    code_title: dict[str, str] = {}
    code_type: dict[str, str] = {}
    for r in all_rows:
        d = defs.get(r.criterion_id)
        if d is not None:
            code_order.setdefault(r.code, (d.order, r.code))
            code_title.setdefault(r.code, d.title)
            code_type.setdefault(r.code, d.answer_type)
        else:
            code_order.setdefault(r.code, (10**6, r.code))
            code_title.setdefault(r.code, r.code)
            if r.applicable and isinstance(r.answer, (int, float)) and not isinstance(r.answer, bool):
                code_type.setdefault(r.code, "scale")
    codes = sorted(code_order, key=lambda c: code_order[c])

    # values per (code, language, channel): list of per-row numbers (1/0 for binary, score/max for scale)
    cell_values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    na_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [na, total]
    reviewed: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)  # (ai, final, confirmed)
    for ev in latest:
        conv = ev.conversation
        language = (conv.language if conv is not None else None) or "—"
        channel = (conv.channel if conv is not None else None) or "—"
        for r in rows_by_eval.get(ev.id, []):
            counts = na_counts[r.code]
            counts[1] += 1
            if not r.applicable:
                counts[0] += 1
            else:
                value: float | None
                if code_type.get(r.code, "binary") == "scale":
                    if r.score is not None and r.max_points and r.max_points > 0:
                        value = max(0.0, min(1.0, float(r.score) / float(r.max_points)))
                    else:
                        value = None
                else:
                    value = 1.0 if _is_yes(r.answer) else 0.0 if r.answer is not None else None
                if value is not None:
                    for key in ((r.code, ALL, ALL), (r.code, language, ALL), (r.code, language, channel)):
                        cell_values[key].append(value)
            if r.review_state in ("confirmed", "changed"):
                ai = _category(r.answer)
                final = _category(r.final_answer) if r.final_answer is not None else ai
                if ai is not None and final is not None:
                    reviewed[r.code].append((ai, final, r.review_state == "confirmed"))

    heatmap: list[dict[str, Any]] = []
    for code in codes:
        keys = sorted((k for k in cell_values if k[0] == code), key=lambda k: (k[1] != ALL, k[1], k[2] != ALL, k[2]))
        for key in keys:
            values = cell_values[key]
            value = _round(statistics.fmean(values), 4)
            heatmap.append(
                {
                    "code": code,
                    "title": code_title.get(code, code),
                    "language": key[1],
                    "channel": key[2],
                    "value": value,
                    "yes_rate_or_mean": value,
                    "n": len(values),
                }
            )

    na_rates = [
        {"code": code, "na_rate": _round(na_counts[code][0] / na_counts[code][1], 4) if na_counts[code][1] else None}
        for code in codes
    ]

    agreement: list[dict[str, Any]] = []
    for code in codes:
        pairs = reviewed.get(code, [])
        n_reviewed = len(pairs)
        agreement.append(
            {
                "code": code,
                "n_reviewed": n_reviewed,
                "agreement_rate": _round(sum(1 for p in pairs if p[2]) / n_reviewed, 4) if n_reviewed else None,
                "kappa": cohen_kappa([(a, f) for a, f, _ in pairs]),
            }
        )

    # --- operators --------------------------------------------------------------------------
    per_operator: dict[str, dict[str, Any]] = {}
    for ev in latest:
        conv = ev.conversation
        name = (conv.operator_name if conv is not None else None) or UNKNOWN_OPERATOR
        bucket = per_operator.setdefault(name, {"scores": [], "n": 0, "critical": 0})
        bucket["n"] += 1
        if ev.final_total_score is not None:
            bucket["scores"].append(float(ev.final_total_score))
        if ev.critical_error:
            bucket["critical"] += 1
    operators = [
        {"operator_name": name, "n": b["n"], "mean_total": _mean(b["scores"]), "critical_count": b["critical"]}
        for name, b in per_operator.items()
    ]
    operators.sort(key=lambda o: (o["mean_total"] is None, o["mean_total"] if o["mean_total"] is not None else 0.0, o["operator_name"]))

    # --- worst ------------------------------------------------------------------------------
    scored = [ev for ev in latest if ev.final_total_score is not None]
    scored.sort(key=lambda ev: (float(ev.final_total_score), ev.conversation_id))
    worst = [_conversation_row(ev) for ev in scored[:10]]

    # --- providers (all completed evaluations in scope: cost is cumulative) -----------------
    per_provider: dict[tuple[str, str | None], dict[str, Any]] = {}
    for ev in all_completed:
        key = (ev.provider, ev.model)
        bucket = per_provider.setdefault(key, {"n": 0, "cost": [], "latency": []})
        bucket["n"] += 1
        bucket["cost"].append(float(ev.cost_usd or 0.0))
        bucket["latency"].append(float(ev.latency_ms or 0))
    providers = [
        {
            "provider": provider,
            "model": model,
            "n": b["n"],
            "mean_cost_usd": _round(statistics.fmean(b["cost"]), 4) if b["cost"] else None,
            "mean_latency_ms": _round(statistics.fmean(b["latency"]), 0) if b["latency"] else None,
            "total_cost_usd": _round(sum(b["cost"]), 4),
        }
        for (provider, model), b in sorted(per_provider.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]

    # --- first response ---------------------------------------------------------------------
    threshold = first_response_threshold(db, checklist_version_id)
    frs = [
        float(v)
        for v in db.scalars(select(Conversation.first_response_seconds).where(Conversation.first_response_seconds.is_not(None)))
    ]
    first_response = {
        "mean_seconds": _round(statistics.fmean(frs), 1) if frs else None,
        "over_threshold_share": _round(sum(1 for v in frs if v > threshold) / len(frs), 4) if frs else None,
        "threshold_seconds": threshold,
        "n": len(frs),
    }

    return {
        "checklist_version_id": checklist_version_id,
        "count": len(latest),
        "mean_total": _mean(totals),
        "median_total": _round(statistics.median(totals)) if totals else None,
        "distribution": [{"bucket": b, "n": distribution_counts[b]} for b in BUCKETS],
        "by_language": _group("language"),
        "by_channel": _group("channel"),
        "criteria_heatmap": heatmap,
        "na_rates": na_rates,
        "operators": operators,
        "worst": worst,
        "critical_count": sum(1 for ev in latest if ev.critical_error),
        "agreement": agreement,
        "providers": providers,
        "first_response": first_response,
    }


def _conversation_row(ev: Evaluation) -> dict[str, Any]:
    conv = ev.conversation
    return {
        "conversation_id": ev.conversation_id,
        "evaluation_id": ev.id,
        "channel": conv.channel if conv is not None else None,
        "language": conv.language if conv is not None else None,
        "operator_name": (conv.operator_name if conv is not None else None) or None,
        "total_score": _round(ev.total_score),
        "final_total_score": _round(ev.final_total_score),
        "critical_error": bool(ev.critical_error),
    }
