"""Deterministic scoring engine.

Implements the organizer rules (CQA_Chat_Quality_Checklist, page 2, "Порядок расчёта"):
1. N/A only where allowed; 2. Да = full weight, Нет = 0, scale = chosen step;
3. scale → points proportional to weight (top step = full weight);
4. exclude N/A, redistribute weights proportionally, max stays 100;
5. round to two decimals; no total if no applicable weighted criteria; gross errors flagged separately.

Pure functions, no I/O. The LLM never does arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass
class CriterionInput:
    code: str
    weight: float
    max_points: float
    points: float | None  # None when not applicable
    applicable: bool = True
    section: str | None = None
    is_critical: bool = False
    critical_hit: bool = False


@dataclass
class CriterionResult:
    code: str
    weight_applied: float
    earned: float


@dataclass
class SectionResult:
    code: str
    score: float
    max: float


@dataclass
class ScoreResult:
    total: float | None
    total_raw: float | None
    critical: bool
    criteria: dict[str, CriterionResult] = field(default_factory=dict)
    sections: list[SectionResult] = field(default_factory=list)
    applicable_weight: float = 0.0


def _round(value: float, decimals: int, mode: str = "half_up") -> float:
    q = Decimal(1).scaleb(-decimals)
    rounding = ROUND_HALF_UP if mode == "half_up" else ROUND_HALF_UP
    return float(Decimal(repr(value)).quantize(q, rounding=rounding))


def answer_points(options: list[dict[str, Any]], answer: Any) -> float | None:
    """Map an answer to its points using the criterion options. Returns None if not found."""
    if answer is None:
        return None
    for opt in options:
        val = opt.get("value")
        if val == answer:
            return float(opt.get("points", 0))
        # tolerate "20" vs 20 and "Да"/"Нет" labels
        if str(val).lower() == str(answer).lower():
            return float(opt.get("points", 0))
        if str(opt.get("label", "")).lower() == str(answer).lower():
            return float(opt.get("points", 0))
    return None


def normalize_answer(options: list[dict[str, Any]], answer: Any) -> Any:
    """Return the canonical option value for an answer (e.g. "20" -> 20, "Да" -> "yes")."""
    for opt in options:
        val = opt.get("value")
        if val == answer or str(val).lower() == str(answer).lower() or str(opt.get("label", "")).lower() == str(answer).lower():
            return val
    return answer


def compute_score(rules: dict[str, Any], items: list[CriterionInput], sections: list[dict[str, Any]] | None = None) -> ScoreResult:
    total_scale = float(rules.get("total_scale", 100))
    decimals = int(rules.get("rounding", {}).get("decimals", 2))
    mode = rules.get("rounding", {}).get("mode", "half_up")
    critical_rule = rules.get("critical", {"mode": "flag_only"})

    applicable = [i for i in items if i.applicable and i.weight > 0 and i.points is not None]
    weight_sum = sum(i.weight for i in applicable)
    critical = any(i.is_critical and i.critical_hit for i in items)

    result = ScoreResult(total=None, total_raw=None, critical=critical, applicable_weight=weight_sum)
    for i in items:
        result.criteria[i.code] = CriterionResult(code=i.code, weight_applied=0.0, earned=0.0)

    if weight_sum <= 0:
        return result  # no total per rule 5

    factor = total_scale / weight_sum
    total_raw = 0.0
    for i in applicable:
        weight_applied = i.weight * factor
        ratio = (i.points / i.max_points) if i.max_points > 0 else 0.0
        ratio = max(0.0, min(1.0, ratio))
        earned = weight_applied * ratio
        result.criteria[i.code] = CriterionResult(code=i.code, weight_applied=weight_applied, earned=earned)
        total_raw += earned

    if critical and critical_rule.get("mode") == "flag_and_cap":
        total_raw = min(total_raw, float(critical_rule.get("cap_total", 0)))

    result.total_raw = total_raw
    result.total = _round(total_raw, decimals, mode)

    if sections:
        for s in sections:
            code = s.get("code")
            members = [i for i in applicable if i.section == code]
            score = sum(result.criteria[i.code].earned for i in members)
            mx = sum(result.criteria[i.code].weight_applied for i in members)
            result.sections.append(SectionResult(code=code, score=_round(score, decimals, mode), max=_round(mx, decimals, mode)))
    return result
