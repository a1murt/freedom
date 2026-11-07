"""Verdict validation against the transcript and the criterion definition (docs/ARCHITECTURE.md §6.4)."""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from ..ingest.normalize import AGENT_JOINED_RE, RECORD_START_RE, NormTurn, parse_iso
from ..scoring.engine import answer_points, normalize_answer
from .contract import Evidence, Verdict
from .packer import CriterionPack

QUOTE_MATCH_THRESHOLD = 70

# The contact criterion (C1) on calls whose opening the recording missed (docs/ORGANIZER_UPDATES.md §5). The results
# format allows only true/false for contact, and a deduction needs a fragment one can listen to and contest: an operator
# opening said, wholly or in part, before «Запись и транскрибирование запущены» is credited by rule (the runner does not
# even call the model, cqa.judge.runner.judge_criterion), the text the transcript kept is quoted for the reviewer, and
# the call is listed as a recording gap (cqa.analytics.recording).
CONTACT_OUTSIDE_RECORDING_FLAG = "rule_contact_outside_recording"
CONTACT_EXPORT_ID = "contact"
# turn timestamps of calls mark the END of an utterance (measured on the DEV audio: 74 % of the client/operator stamps
# sit at the end of a speech segment); the start is estimated from the word count at a calm speaking rate
WORDS_PER_SECOND = 3.5  # calibrated on the DEV calls: operator openings run at 3.4-6.8 words/s
PARTIAL_MIN_MISSING_S = 1.0  # less than a second before the start is within timing noise
MIN_OPENING_WORDS = 3  # «Алло?» is not the opening: the first operator turn with at least this many words is


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


def is_contact_criterion(crit: CriterionPack) -> bool:
    """The version's contact criterion: exported as "contact" (scoring_rules.export_ids, else C1), binary with a «yes»
    option and not the gross-error criterion. A checklist whose C1 means something else never gets the call rule."""
    from ..checklist.validation import default_export_id

    export_id = getattr(crit, "export_id", None) or default_export_id(crit.code)
    return (export_id == CONTACT_EXPORT_ID and not crit.is_critical
            and any(str(o.get("value")).lower() == "yes" for o in (crit.options or [])))


@dataclass
class RecordingGap:
    """The operator's opening of a call was said, wholly (`partial` False) or in part, before the recording started."""
    op: NormTurn            # the operator's first turn
    start: NormTurn         # the recording-start event the audio clock counts from
    lost_s: float | None    # seconds from the first event to the recording start
    partial: bool = False   # the turn ended after the start but began before it
    missing_s: float | None = None  # partial: estimated seconds of the opening said before the start


def _seconds(a, b) -> float | None:
    return (b - a).total_seconds() if a is not None and b is not None else None


def opening_outside_recording(turns: list[NormTurn]) -> RecordingGap | None:
    """The call's recording gap for the contact criterion, or None.

    The audio clock of cqa.ingest.normalize._call_clock decides: its anchor is the first recording-start event with a
    parseable time (offset 0.0), and turns said before it carry no offset. The opening is the operator's first turn of at
    least MIN_OPENING_WORDS words («Алло?» alone is not it), else the first operator turn.

    - Full gap: the opening has no offset and comes before the start, by position or by its stamp (DEV p4-ru-002), or it
      ended exactly at the start.
    - Partial gap: the opening ended after the start, but a stamp marks the END of an utterance, so its start is
      estimated from the word count at WORDS_PER_SECOND. The estimate never reaches before the previous spoken turn,
      which must have a stamp, or before «Агент присоединился», the moment the operator can start talking. If the agent
      joined at or after the recording start, the whole opening was recorded. At least PARTIAL_MIN_MISSING_S must
      precede the start.
    - Without a start event or a clock there is nothing to compare with, and the criterion is judged as usual."""
    start = next((t for t in turns if t.speaker == "system" and t.offset_seconds is not None and RECORD_START_RE.search(t.text or "")), None)
    if start is None:
        return None
    operator_turns = [t for t in turns if t.speaker == "operator" and not t.is_duplicate]
    in_segment = [t for t in operator_turns if t.segment == "operator"] or operator_turns
    op = next((t for t in in_segment if len((t.text or "").split()) >= MIN_OPENING_WORDS), None) or (in_segment[0] if in_segment else None)
    if op is None:
        return None
    first, began, ended = parse_iso(turns[0].timestamp_raw or ""), parse_iso(start.timestamp_raw or ""), parse_iso(op.timestamp_raw or "")
    lost = _seconds(first, began)
    lost = round(lost, 1) if lost is not None and lost >= 0 else None
    after_start = _seconds(began, ended)
    if op.offset_seconds is None:
        before = op.idx < start.idx or (after_start is not None and after_start < 0)
        return RecordingGap(op=op, start=start, lost_s=lost) if before else None
    if after_start is None:
        return None
    if after_start <= 0:  # ended exactly at the start: all of it was said before
        return RecordingGap(op=op, start=start, lost_s=lost)
    estimated = len((op.text or "").split()) / WORDS_PER_SECOND
    # the previous spoken turn must have a stamp; without one the bound is unknown, and no gap is claimed
    prev = next((t for t in reversed(turns) if t.idx < op.idx and t.speaker in ("client", "operator", "bot")), None)
    if prev is not None:
        prev_at = parse_iso(prev.timestamp_raw or "")
        if prev_at is None:
            return None
        estimated = min(estimated, max(_seconds(prev_at, ended) or 0.0, 0.0))
    # the operator cannot speak before joining: if that happened at or after the recording start, all was recorded
    joined = next((t for t in reversed(turns) if t.idx < op.idx and t.speaker == "system" and AGENT_JOINED_RE.search(t.text or "")
                   and parse_iso(t.timestamp_raw or "") is not None), None)
    if joined is not None:
        joined_at = parse_iso(joined.timestamp_raw)
        if _seconds(began, joined_at) is not None and _seconds(began, joined_at) >= 0:
            return None
        estimated = min(estimated, max(_seconds(joined_at, ended) or 0.0, 0.0))
    missing = estimated - after_start
    if missing < PARTIAL_MIN_MISSING_S:
        return None
    return RecordingGap(op=op, start=start, lost_s=lost, partial=True, missing_s=round(missing, 1))


def _yes_value(crit: CriterionPack) -> str:
    options = crit.options or []
    yes = next((str(o.get("value")) for o in options if str(o.get("value")).lower() == "yes"), None)
    if yes is None and options:
        yes = str(max(options, key=lambda o: float(o.get("points", 0) or 0)).get("value"))
    return yes or "yes"


def _safe_quote(text: str) -> str:
    """The turn text for «…» in an explanation: one line, at most 300 characters, inner guillemets as curly quotes (the
    turn-number shifter protects «…» spans and cannot nest them), no trailing full stop."""
    q = " ".join((text or "").split())[:300].replace("«", "“").replace("»", "”")
    return q[:-1] if q.endswith(".") and not q.endswith("..") else q


def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def credit_unrecorded_opening(v: Verdict, crit: CriterionPack, gap: RecordingGap, *, model_answer: str | None = None) -> Verdict:
    """Turn `v` into the rule verdict for a call whose opening the recording missed: «Да», confidence 1.0, the operator's
    turn as context evidence, the rule flag only (flags and claims of a model answer it replaces are dropped: they
    describe a verdict that no longer stands). Explanations keep the judge's 0-based labels; the output shifts them."""
    op, start = gap.op, gap.start
    quote = _safe_quote(op.text)
    when = f" через {_num(gap.lost_s)} с от начала разговора" if gap.lost_s else ""
    if gap.partial:
        heard = (f"Первая реплика оператора [{op.idx}] закончилась уже после старта записи (реплика [{start.idx}]{when}), но по длине "
                 f"началась раньше: примерно {_num(gap.missing_s or 0)} с её начала, где звучат приветствие и представление, в аудиозаписи нет.")
    else:
        heard = (f"Запись и транскрибирование запущены на реплике [{start.idx}]{when}, а первая реплика оператора [{op.idx}] "
                 "прозвучала раньше, и в аудиозаписи её нет.")
    v.applicable, v.na_reason, v.answer = True, "", _yes_value(crit)
    v.evidence = [Evidence(turn_idx=op.idx, speaker="operator", quote=quote, role="context")]
    v.checked_span = None
    v.confidence = 1.0
    v.claims = []
    v.flags = [CONTACT_OUTSIDE_RECORDING_FLAG]
    # «Да» here means "no penalty applied", never "the greeting was heard": the explanation says so in words
    v.explanation = (f"Установление контакта — Да по правилу для звонков: начало разговора не записано. {heard} "
                     f"В тексте транскрипта реплика есть: «{quote}». Приветствие по записи не проверено, поэтому штраф по критерию "
                     "не применён: снизить балл можно только по фрагменту, который можно прослушать и оспорить. "
                     "Звонок отмечен как пробел записи.")
    v.step_results = [f"Первая реплика оператора: [{op.idx}], событие начала записи: [{start.idx}].",
                      ("Начало реплики оператора прозвучало до старта записи." if gap.partial
                       else "Реплика оператора прозвучала до старта записи: в аудиофайле её нет."),
                      "Правило системы: снижение только по проверяемому фрагменту, критерий засчитан."]
    if model_answer is not None:
        v.step_results.append(f"Ответ модели, заменённый правилом: {model_answer}.")
    return v


def unusable_reason(v: Verdict, crit: CriterionPack, channel: str | None) -> str | None:
    """Why a verdict cannot be stored as a result (an N/A the checklist forbids on this channel, an answer outside the
    options), or None. Such a verdict would put a null or an unknown value into results.json."""
    if not v.applicable:
        return None if effective_na(crit, channel)[0] else "N/A is not allowed for this criterion on this channel"
    if answer_points(crit.options, normalize_answer(crit.options, v.answer)) is None:
        return f"answer {v.answer!r} is not an allowed option"
    return None


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

    # the contact criterion on a call whose opening the recording missed: decided by rule before the answer checks, so a
    # model N/A or «Нет» neither triggers a retry nor survives (the runner normally skips the model for it altogether)
    if channel == "voice call" and is_contact_criterion(crit):
        gap = opening_outside_recording(turns)
        if gap is not None:
            model_answer = (v.answer or "") if v.applicable else "N/A"
            if not (v.applicable and str(v.answer).lower() == "yes"):
                issues.append(f"{crit.code} credited by rule: operator opening (turn {gap.op.idx}) precedes the recording start (turn {gap.start.idx})")
            credit_unrecorded_opening(v, crit, gap, model_answer=model_answer)
            penalty = 0.0  # the model's own evidence was replaced: its quote mismatches no longer apply

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
