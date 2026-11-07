"""Transcript normalization: offsets, duplicates, segments, operator name, terminal events.
Pure functions over the organizer record format. See docs/DATASET_NOTES.md §5–§6."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PAUSE_RE = re.compile(r"(приостанов|paused|тоқтатыл)", re.I)
# "Запись и транскрибирование запущены." / "Жазу және транскрипция басталды." / "Recording and transcription started."
PRE_START_TOLERANCE_S = 1.0
RECORD_START_RE = re.compile(r"(запис\w*\s+и\s+транскриб\w*\s+(?:запущен|начат)|recording\s+and\s+transcription\s+(?:started|began|have\s+started)"
                             r"|жазу\s+және\s+транскрипция\s+басталд)", re.I)
RESUME_RE = re.compile(r"(возобнов|resumed|жалғас)", re.I)
AGENT_JOINED_RE = re.compile(r"(агент присоединился|agent joined|оператор подключ|агент қосылды)", re.I)
ESCALATION_RE = re.compile(r"(эскалац|переведен|transferred|жіберілді|передан оператору)", re.I)
CONSULT_START_RE = re.compile(r"(консультац.*агент|удержани|on hold|hold)", re.I)
CONSULT_END_RE = re.compile(r"(агент вышел|больше не находится на удержании|no longer on hold)", re.I)
CLIENT_ENDED_RE = re.compile(r"(клиент завершил|customer ended|клиент аяқтады)", re.I)
OPERATOR_CLOSED_RE = re.compile(r"(оператор закрыл|agent closed|оператор чатты жапты)", re.I)
CONNECTION_LOST_RE = re.compile(r"(соединение.*прервано|connection.*lost|байланыс үзілді)", re.I)
CLIENT_LEFT_RE = re.compile(r"(клиент покинул|customer left|клиент шығып кетті)", re.I)
IDENTITY_OK_RE = re.compile(r"(личность.*подтвержден|identity verified|жеке басы растал)", re.I)

NAME_PATTERNS = [
    re.compile(r"меня зовут\s+([A-ZА-ЯЁӘІҢҒҮҰҚӨҺ][\w\-ёӘІҢҒҮҰҚӨҺәіңғүұқөһ]+)", re.I),
    re.compile(r"\bя\s+([A-ZА-ЯЁ][а-яё\-]+)[,\.\s]", re.I),
    re.compile(r"\bмен\s+([A-ZА-ЯЁӘІҢҒҮҰҚӨҺ][\wәіңғүұқөһ\-]+?)(?:мын|мін|бын|бін|пын|пін)\b", re.I),
    re.compile(r"\bмен\s+([A-ZА-ЯЁӘІҢҒҮҰҚӨҺ][\wәіңғүұқөһ\-]+)\b", re.I),
    re.compile(r"\b(?:I'm|I am|this is|my name is)\s+([A-Z][a-z\-]+)", re.I),
]
NAME_STOPWORDS = {"оператор", "менеджер", "специалист", "сотрудник", "поддержка", "support", "agent", "жасанды", "aim", "ai", "бот"}


@dataclass
class NormTurn:
    idx: int
    timestamp_raw: str
    offset_seconds: float | None
    speaker: str
    text: str
    is_duplicate: bool = False
    segment: str = "pre"


@dataclass
class NormalizedConversation:
    turns: list[NormTurn]
    operator_name: str | None
    first_response_seconds: float | None
    terminal_event: str | None
    operator_start_idx: int | None
    meta: dict[str, Any] = field(default_factory=dict)


def parse_clock(ts: str) -> float | None:
    """'MM:SS' or 'H:MM:SS' → seconds."""
    parts = ts.strip().split(":")
    if not all(p.isdigit() for p in parts) or not parts:
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def parse_iso(ts: str) -> datetime | None:
    s = ts.strip()
    # Python's fromisoformat handles up to 6 fractional digits; the organizer gives 7.
    m = re.match(r"^(.*\.\d{6})\d+([+-]\d\d:\d\d|Z)?$", s)
    if m:
        s = m.group(1) + (m.group(2) or "")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _is_system(t: dict[str, Any]) -> bool:
    return t.get("speaker") == "system"


def normalize(record: dict[str, Any]) -> NormalizedConversation:
    raw: list[dict[str, Any]] = record.get("transcript") or []
    channel = record.get("channel", "agent chat")
    is_call = channel == "voice call"

    # --- offsets -------------------------------------------------------------
    # Calls have two clocks. `timeline` = seconds since the first event minus recording pauses (first response time and
    # duplicate detection, unchanged). `offsets` = position in the audio file: counted from the recording-start event
    # ("Запись и транскрибирование запущены") when the transcript has one, so evidence seeks land on the right moment;
    # turns before that event are not in the recording (offset None, listed in meta.not_in_recording).
    offsets: list[float | None] = []
    timeline: list[float | None] = []
    call_meta: dict[str, Any] = {}
    if is_call:
        stamps = [parse_iso(str(t.get("timestamp", ""))) for t in raw]
        timeline = _call_clock(raw, stamps, base_index=None)
        start_i = next((i for i, t in enumerate(raw) if _is_system(t) and stamps[i] is not None
                        and RECORD_START_RE.search(str(t.get("text", "")))), None)
        if start_i is None:
            offsets = list(timeline)
        else:
            offsets = _call_clock(raw, stamps, base_index=start_i)
            first = next((dt for dt in stamps if dt is not None), None)
            not_in = [i for i, o in enumerate(offsets) if o is None and stamps[i] is not None]
            call_meta = {"recording_start_idx": start_i,
                         "recording_start_offset_s": round(max(0.0, (stamps[start_i] - first).total_seconds()), 3) if first else 0.0}
            if not_in:
                call_meta["not_in_recording"] = not_in
    else:
        for t in raw:
            offsets.append(parse_clock(str(t.get("timestamp", ""))))
        if raw and all(o is None for o in offsets):
            # chats from other sources (Dynamics 365) carry ISO timestamps: seconds since the first parseable one
            stamps = [parse_iso(str(t.get("timestamp", ""))) for t in raw]
            base_dt = next((dt for dt in stamps if dt is not None), None)
            if base_dt is not None:
                offsets = [max(0.0, (dt - base_dt).total_seconds()) if dt is not None and (dt.tzinfo is None) == (base_dt.tzinfo is None)
                           else None for dt in stamps]

    clock = timeline if is_call else offsets  # first response and duplicates use the dialog's own clock

    # --- duplicates ----------------------------------------------------------
    turns: list[NormTurn] = []
    prev: NormTurn | None = None
    prev_clock: float | None = None
    for i, t in enumerate(raw):
        nt = NormTurn(idx=i, timestamp_raw=str(t.get("timestamp", "")), offset_seconds=offsets[i],
                      speaker=str(t.get("speaker", "client")), text=str(t.get("text", "")).strip())
        if prev is not None and prev.speaker == nt.speaker and prev.text == nt.text:
            close = (prev_clock is None or clock[i] is None or abs(clock[i] - prev_clock) <= 2.0)
            nt.is_duplicate = close
        turns.append(nt)
        prev = nt
        prev_clock = clock[i]

    # --- segments ------------------------------------------------------------
    operator_start: int | None = None
    if is_call:
        for t in turns:
            if t.speaker == "system" and AGENT_JOINED_RE.search(t.text):
                operator_start = t.idx + 1
                break
    if operator_start is None:
        for t in turns:
            if t.speaker == "operator":
                operator_start = t.idx
                break

    escalation_idx: int | None = None
    for t in turns:
        if t.speaker == "system" and (ESCALATION_RE.search(t.text) or AGENT_JOINED_RE.search(t.text)):
            escalation_idx = t.idx
            if is_call and AGENT_JOINED_RE.search(t.text):
                break
            if not is_call:
                break

    post_start: int | None = None
    for t in turns:
        if t.speaker == "system" and CLIENT_ENDED_RE.search(t.text):
            post_start = t.idx
            break

    in_internal = False
    for t in turns:
        if operator_start is not None and t.idx < operator_start:
            t.segment = "pre"
            continue
        if post_start is not None and t.idx > post_start:
            t.segment = "post"
            continue
        if is_call and t.speaker == "system":
            if CONSULT_START_RE.search(t.text) and not CONSULT_END_RE.search(t.text):
                in_internal = True
            elif CONSULT_END_RE.search(t.text):
                in_internal = False
        t.segment = "internal" if (in_internal and t.speaker != "client") else "operator"

    # --- operator name -------------------------------------------------------
    operator_name: str | None = None
    for t in turns:
        if t.speaker == "operator":
            operator_name = extract_name(t.text)
            break

    # --- first response -----------------------------------------------------
    first_response: float | None = None
    if operator_start is not None:
        first_op = next((t for t in turns if t.speaker == "operator" and t.idx >= operator_start), None)
        anchor = next((t for t in turns if t.idx == escalation_idx), None) if escalation_idx is not None else None
        if first_op and clock[first_op.idx] is not None:
            if anchor and clock[anchor.idx] is not None:
                first_response = max(0.0, clock[first_op.idx] - clock[anchor.idx])
            else:
                first_response = clock[first_op.idx]

    # --- terminal event --------------------------------------------------------
    terminal: str | None = None
    identity_confirmed = False
    for t in turns:
        if t.speaker != "system":
            continue
        if IDENTITY_OK_RE.search(t.text):
            identity_confirmed = True
        if OPERATOR_CLOSED_RE.search(t.text):
            terminal = "closed_by_operator"
        elif CONNECTION_LOST_RE.search(t.text):
            terminal = "connection_lost"
        elif CLIENT_LEFT_RE.search(t.text):
            terminal = "client_left"
        elif CLIENT_ENDED_RE.search(t.text):
            terminal = "client_ended_call"

    meta = {
        "identity_confirmed_event": identity_confirmed,
        "escalation_idx": escalation_idx,
        "post_start_idx": post_start,
        "duplicates": sum(1 for t in turns if t.is_duplicate),
        "speakers": {s: sum(1 for t in turns if t.speaker == s) for s in ("client", "operator", "bot", "system")},
        **call_meta,
    }
    from ..judge.steering import steering_turns  # local import: normalize stays importable on its own

    steering = steering_turns(turns)
    if steering:
        meta["steering_turns"] = steering
    return NormalizedConversation(turns=turns, operator_name=operator_name, first_response_seconds=first_response,
                                  terminal_event=terminal, operator_start_idx=operator_start, meta=meta)


def _call_clock(raw: list[dict[str, Any]], stamps: list[datetime | None], *, base_index: int | None) -> list[float | None]:
    """Seconds per turn from the base event (the first timestamp, or raw[base_index]) minus recording pauses; while the
    recording is paused everything sits at the pause point. Turns before the base event get None."""
    base: datetime | None = stamps[base_index] if base_index is not None else None
    paused_total = 0.0
    pause_start: datetime | None = None
    out: list[float | None] = []
    for i, t in enumerate(raw):
        dt = stamps[i]
        if dt is None:
            out.append(None)
            continue
        if base is None:
            base = dt
        if base_index is not None and (i < base_index or dt < base):
            # said before the recording started (system notes logged within a second of the start count as at 0)
            out.append(0.0 if (base - dt).total_seconds() <= PRE_START_TOLERANCE_S and _is_system(t) else None)
            continue
        text = t.get("text", "")
        if _is_system(t) and RESUME_RE.search(text) and pause_start is not None:
            paused_total += max(0.0, (dt - pause_start).total_seconds())
            pause_start = None
        if pause_start is not None:
            out.append(max(0.0, (pause_start - base).total_seconds() - paused_total))
        else:
            out.append(max(0.0, (dt - base).total_seconds() - paused_total))
        if _is_system(t) and PAUSE_RE.search(text) and not RESUME_RE.search(text):
            pause_start = dt
    return out


def extract_name(text: str) -> str | None:
    for pat in NAME_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).strip(" .,!")
            if name.lower() in NAME_STOPWORDS or len(name) < 2:
                continue
            return name
    return None


def pack_transcript(turns: list[NormTurn], include_duplicates: bool = False) -> str:
    """Render turns as '[idx] speaker (ts): text' for the judge prompt."""
    lines = []
    for t in turns:
        if t.is_duplicate and not include_duplicates:
            continue
        seg = "" if t.segment == "operator" else f" <{t.segment}>"
        lines.append(f"[{t.idx}] {t.speaker} ({t.timestamp_raw}){seg}: {t.text}")
    return "\n".join(lines)
