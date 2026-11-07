"""Verdict validation against the transcript and the criterion definition (docs/ARCHITECTURE.md §6.4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from ..ingest.normalize import NormTurn
from ..scoring.engine import answer_points, normalize_answer
from .contract import Verdict
from .packer import CriterionPack

QUOTE_MATCH_THRESHOLD = 70


@dataclass
class ValidationResult:
    verdict: Verdict
    issues: list[str] = field(default_factory=list)
    needs_retry: bool = False
    confidence_penalty: float = 0.0


def effective_na(crit: CriterionPack, channel: str | None) -> tuple[bool, str | None]:
    """N/A availability for this channel: a channel override may allow N/A where the base criterion does not."""
    ov = (crit.channel_overrides or {}).get(channel or "", {}) or {}
    if ov.get("na_allowed"):
        return True, ov.get("na_condition") or crit.na_condition
    return crit.na_allowed, crit.na_condition


def validate_verdict(v: Verdict, turns: list[NormTurn], crit: CriterionPack, *, conversation_terminal: str | None = None, channel: str | None = None) -> ValidationResult:
    issues: list[str] = []
    retry = False
    penalty = 0.0
    by_idx = {t.idx: t for t in turns}

    # evidence
    kept = []
    for ev in v.evidence:
        t = by_idx.get(ev.turn_idx)
        if t is None:
            issues.append(f"evidence turn {ev.turn_idx} does not exist")
            penalty += 0.1
            continue
        if ev.speaker != t.speaker:
            issues.append(f"evidence turn {ev.turn_idx} speaker mismatch ({ev.speaker} vs {t.speaker})")
            ev.speaker = t.speaker  # type: ignore[assignment]
        score = fuzz.partial_ratio(ev.quote.lower(), t.text.lower()) if ev.quote else 0
        if score < QUOTE_MATCH_THRESHOLD:
            issues.append(f"evidence quote for turn {ev.turn_idx} does not match the turn text (score {score})")
            ev.quote = t.text[:300]
            penalty += 0.05
        kept.append(ev)
    v.evidence = kept

    # applicability
    na_ok, _na_cond = effective_na(crit, channel)
    if not v.applicable:
        if not na_ok:
            issues.append("N/A given but not allowed for this criterion")
            retry = True
        elif not v.na_reason.strip():
            issues.append("N/A without na_reason")
            retry = True
        v.answer = ""
    else:
        norm = normalize_answer(crit.options, v.answer)
        pts = answer_points(crit.options, norm)
        if pts is None:
            issues.append(f"answer '{v.answer}' is not an allowed option")
            retry = True
        else:
            v.answer = str(norm)

    # evidence requirement for negative / reduced answers
    if v.applicable and not retry:
        pts = answer_points(crit.options, v.answer)
        reduced = pts is not None and pts < float(crit.options[0].get("points", 1)) if crit.options else False
        if crit.is_critical:
            reduced = str(v.answer).lower() == "yes"
        if reduced and not v.evidence and v.checked_span is None:
            issues.append("negative or reduced answer without evidence or checked_span")
            retry = True

    if v.checked_span is not None:
        a, b = v.checked_span.from_turn, v.checked_span.to_turn
        if a not in by_idx or b not in by_idx or a > b:
            issues.append("checked_span out of range")
            v.checked_span = None

    # hard rules from the checklist that the model must not violate
    if crit.code == "C7" and conversation_terminal in ("connection_lost", "client_left") and v.applicable and crit.na_allowed:
        issues.append("C7 forced to N/A: conversation ended by connection loss / client leaving")
        v.applicable = False
        v.answer = ""
        v.na_reason = v.na_reason or "Чат оборвался до возможности его завершить (системное событие об обрыве)."
        v.flags.append("rule_na_terminal_event")

    v.confidence = max(0.0, min(1.0, v.confidence - penalty))
    return ValidationResult(verdict=v, issues=issues, needs_retry=retry, confidence_penalty=penalty)


def retry_instruction(issues: list[str]) -> str:
    return ("Your previous verdict had problems: " + "; ".join(issues) +
            ". Return a corrected verdict JSON: cite only existing turn indices with verbatim quotes, "
            "use only allowed answers, give na_reason when not applicable, and provide evidence or checked_span for negative answers.")
