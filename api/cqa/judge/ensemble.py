"""Cross-model voting (docs/ARCHITECTURE.md §7.5)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from ..scoring.engine import answer_points
from .contract import Verdict
from .packer import CriterionPack


@dataclass
class VoteOutcome:
    merged: Verdict
    votes: list[dict[str, Any]]
    disagreement: bool
    detail: str


def _snap_to_option(crit: CriterionPack, value: float) -> str:
    best = min(crit.options, key=lambda o: abs(float(o["value"]) - value))
    return str(best["value"])


def vote(verdicts: list[tuple[str, str, Verdict]], crit: CriterionPack) -> VoteOutcome:
    """verdicts: list of (provider, model, verdict)."""
    votes = [{"provider": p, "model": m, "applicable": v.applicable, "answer": v.answer, "confidence": v.confidence,
              "explanation": v.explanation} for p, m, v in verdicts]
    if len(verdicts) == 1:
        return VoteOutcome(verdicts[0][2], votes, False, "single judge")

    # applicability by majority (ties → applicable)
    na_count = sum(1 for _, _, v in verdicts if not v.applicable)
    applicable = na_count * 2 < len(verdicts) or (na_count * 2 == len(verdicts))
    if not applicable:
        pool = [v for _, _, v in verdicts if not v.applicable]
        best = max(pool, key=lambda v: v.confidence)
        disagreement = any(v.applicable for _, _, v in verdicts)
        return VoteOutcome(best, votes, disagreement, f"N/A by {na_count}/{len(verdicts)}")

    pool = [v for _, _, v in verdicts if v.applicable]
    if crit.answer_type == "binary":
        counts: dict[str, float] = {}
        conf: dict[str, float] = {}
        for v in pool:
            counts[v.answer] = counts.get(v.answer, 0) + 1
            conf[v.answer] = conf.get(v.answer, 0) + v.confidence
        top = sorted(counts.items(), key=lambda kv: (kv[1], conf[kv[0]]), reverse=True)
        answer = top[0][0]
        disagreement = len(counts) > 1 and top[0][1] * 2 <= len(pool)
        detail = f"{int(top[0][1])}/{len(pool)} for {answer}"
    else:
        nums = [float(v.answer) for v in pool if v.answer not in ("", None)]
        med = statistics.median(nums) if nums else 0.0
        answer = _snap_to_option(crit, med)
        spread = (max(nums) - min(nums)) if nums else 0
        steps = sorted({float(o["value"]) for o in crit.options}, reverse=True)
        one_step = (steps[0] - steps[1]) if len(steps) > 1 else 1
        disagreement = spread > one_step
        detail = f"median {med} → {answer}, spread {spread}"

    winners = [v for v in pool if v.answer == answer] or pool
    best = max(winners, key=lambda v: v.confidence)
    merged = best.model_copy(deep=True)
    merged.confidence = round(sum(v.confidence for v in pool) / len(pool), 3)
    seen = {e.turn_idx for e in merged.evidence}
    for v in winners:
        for e in v.evidence:
            if e.turn_idx not in seen:
                merged.evidence.append(e)
                seen.add(e.turn_idx)
    if disagreement:
        merged.flags.append("judges_disagree")
    return VoteOutcome(merged, votes, disagreement, detail)


def arbiter_prompt(crit: CriterionPack, verdicts: list[tuple[str, str, Verdict]]) -> str:
    lines = [f"Independent judges disagree on criterion {crit.code} ({crit.title}). Their verdicts:"]
    for p, m, v in verdicts:
        lines.append(f"- {p}/{m}: applicable={v.applicable} answer={v.answer!r} confidence={v.confidence}: {v.explanation}")
    lines.append("Re-read the transcript, resolve the disagreement by following the decision steps strictly, and return the final verdict JSON. Mention in the explanation which reading you rejected and why.")
    return "\n".join(lines)
