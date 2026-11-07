"""Acoustic and conversational features for a voice call.

Everything here degrades gracefully: when ffmpeg/ffprobe is missing or the file is unreadable
the affected fields are ``None`` and the rest is still computed from the transcript turns.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .resolver import probe_duration
from .tools import FFMPEG, run_tool

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT_S = 120
SILENCE_FILTER = "silencedetect=noise=-35dB:d=3"
LOUDNESS_FILTER = "ebur128=framelog=quiet"
TURN_MIN_S = 2.0
TURN_MAX_S = 30.0
OVERLAP_WINDOW_S = 0.3
WORDS_PER_SECOND_FALLBACK = 2.5  # 150 wpm, used only when a turn has no measurable gap

FEATURE_KEYS = (
    "duration_s",
    "operator_talk_s",
    "client_talk_s",
    "talk_ratio_operator",
    "operator_wpm",
    "long_pauses",
    "long_pauses_count",
    "overlaps",
    "loudness_range_lu",
    "hold_segments",
    "repeated_operator_lines",
    "speech_rate_label",
    "summary_ru",
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)\s*\|\s*silence_duration:\s*(-?\d+(?:\.\d+)?)")
_LRA_RE = re.compile(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU")
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


@dataclass
class _Turn:
    idx: int
    speaker: str
    text: str
    offset: float | None
    segment: str
    is_duplicate: bool
    words: int
    gap: float | None = None  # seconds until the next turn (clipped), None if unknown


def compute_features(path: Path, turns: list[dict]) -> dict:
    """Compute the audio feature dict for ``path`` using the transcript ``turns``.

    Each turn dict carries ``idx, speaker, text, offset_seconds, segment, is_duplicate``.
    Returns exactly the keys in :data:`FEATURE_KEYS`; never raises for missing tools or data.
    """
    path = Path(path)
    rows = _normalize_turns(turns)
    active = [t for t in rows if not t.is_duplicate]

    duration = probe_duration(path)
    silence_stderr = _run_filter(path, SILENCE_FILTER)
    loudness_stderr = _run_filter(path, LOUDNESS_FILTER)
    if duration is None:
        duration = _duration_from_stderr(silence_stderr) or _duration_from_stderr(loudness_stderr)
    if duration is None:
        offsets = [t.offset for t in rows if t.offset is not None]
        duration = max(offsets) if offsets else None

    long_pauses = _parse_silences(silence_stderr, duration) if silence_stderr is not None else None
    loudness_range = _parse_lra(loudness_stderr) if loudness_stderr is not None else None

    _fill_gaps(active, duration)
    operator_talk, client_talk = _talk_time(active)
    total_talk = operator_talk + client_talk
    talk_ratio = (operator_talk / total_talk) if total_talk > 0 else None
    wpm = _operator_wpm(active, rows)
    hold_segments = _hold_segments(rows, duration)

    features: dict[str, Any] = {
        "duration_s": _round(duration, 2),
        "operator_talk_s": _round(operator_talk, 2),
        "client_talk_s": _round(client_talk, 2),
        "talk_ratio_operator": _round(talk_ratio, 3),
        "operator_wpm": _round(wpm, 1),
        "long_pauses": long_pauses,
        "long_pauses_count": len(long_pauses) if long_pauses is not None else None,
        "overlaps": _overlaps(active),
        "loudness_range_lu": _round(loudness_range, 1),
        "hold_segments": hold_segments,
        "repeated_operator_lines": _repeated_operator_lines(rows),
        "speech_rate_label": _speech_rate_label(wpm),
        "summary_ru": None,
    }
    features["summary_ru"] = _summary_ru(features)
    return {key: features[key] for key in FEATURE_KEYS}


# --- transcript side -----------------------------------------------------------------------


def _get(turn: Any, key: str, default: Any = None) -> Any:
    if isinstance(turn, dict):
        return turn.get(key, default)
    return getattr(turn, key, default)


def _normalize_turns(turns: list[dict]) -> list[_Turn]:
    rows: list[_Turn] = []
    for position, raw in enumerate(turns or []):
        try:
            idx = int(_get(raw, "idx", position))
        except (TypeError, ValueError):
            idx = position
        offset = _get(raw, "offset_seconds")
        try:
            offset = float(offset) if offset is not None else None
        except (TypeError, ValueError):
            offset = None
        text = str(_get(raw, "text", "") or "")
        rows.append(
            _Turn(
                idx=idx,
                speaker=str(_get(raw, "speaker", "") or "").strip().lower(),
                text=text,
                offset=offset,
                segment=str(_get(raw, "segment", "pre") or "pre").strip().lower(),
                is_duplicate=bool(_get(raw, "is_duplicate", False)),
                words=len(_WORD_RE.findall(text)),
            )
        )
    rows.sort(key=lambda t: t.idx)
    return rows


def _clip(seconds: float) -> float:
    return max(TURN_MIN_S, min(TURN_MAX_S, seconds))


def _fill_gaps(active: list[_Turn], duration: float | None) -> None:
    """Set ``gap`` (clipped seconds until the next timed turn) on every turn with an offset."""
    timed = [t for t in active if t.offset is not None]
    for i, turn in enumerate(timed):
        if i + 1 < len(timed):
            turn.gap = _clip(timed[i + 1].offset - turn.offset)  # type: ignore[operator]
        elif duration is not None and duration > turn.offset:
            turn.gap = _clip(duration - turn.offset)
        else:
            turn.gap = None


def _estimated_length(turn: _Turn) -> float:
    if turn.gap is not None:
        return turn.gap
    if turn.words <= 0:
        return 0.0
    return _clip(turn.words / WORDS_PER_SECOND_FALLBACK)


def _talk_time(active: list[_Turn]) -> tuple[float, float]:
    operator = sum(_estimated_length(t) for t in active if t.speaker == "operator")
    client = sum(_estimated_length(t) for t in active if t.speaker == "client")
    return operator, client


def _operator_wpm(active: list[_Turn], rows: list[_Turn]) -> float | None:
    eligible = [t for t in active if t.speaker == "operator" and t.segment == "operator"]
    if not eligible and not any(t.segment == "operator" for t in rows):
        # No segmentation available at all: fall back to every operator turn.
        eligible = [t for t in active if t.speaker == "operator"]
    measured = [t for t in eligible if t.words > 0 and t.gap]
    total_words = sum(t.words for t in measured)
    if total_words <= 0:
        return None
    weighted = sum(t.words * (t.words / t.gap * 60.0) for t in measured)  # type: ignore[operator]
    return weighted / total_words


def _overlaps(active: list[_Turn]) -> int:
    count = 0
    for prev, cur in pairwise(active):
        if prev.offset is None or cur.offset is None or prev.speaker == cur.speaker:
            continue
        if abs(cur.offset - prev.offset) <= OVERLAP_WINDOW_S:
            count += 1
    return count


def _hold_segments(rows: list[_Turn], duration: float | None) -> list[dict[str, float]]:
    segments: list[dict[str, float]] = []
    i = 0
    while i < len(rows):
        if rows[i].segment != "internal":
            i += 1
            continue
        j = i
        while j < len(rows) and rows[j].segment == "internal":
            j += 1
        run = rows[i:j]
        offsets = [t.offset for t in run if t.offset is not None]
        if offsets:
            start = min(offsets)
            end = next((t.offset for t in rows[j:] if t.offset is not None), None)
            if end is None or end < start:
                end = duration if duration is not None and duration > max(offsets) else max(offsets)
            segments.append({"start": round(start, 2), "end": round(end, 2)})
        i = j
    return segments


def _repeated_operator_lines(rows: list[_Turn]) -> int:
    counts = Counter(
        " ".join(t.text.split())
        for t in rows
        if t.speaker == "operator" and not t.is_duplicate and t.text.strip()
    )
    return sum(1 for n in counts.values() if n >= 2)


def _speech_rate_label(wpm: float | None) -> str | None:
    if wpm is None:
        return None
    if wpm < 100:
        return "slow"
    if wpm > 170:
        return "fast"
    return "normal"


# --- ffmpeg side ----------------------------------------------------------------------------


def _run_filter(path: Path, audio_filter: str) -> str | None:
    """Run ffmpeg with an audio filter to the null muxer and return its stderr, or ``None``."""
    if not path.is_file():
        return None
    proc = run_tool(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-nostdin",
            "-i",
            str(path),
            "-vn",
            "-af",
            audio_filter,
            "-f",
            "null",
            "-",
        ],
        timeout=FFMPEG_TIMEOUT_S,
    )
    if proc is None:
        return None
    if proc.returncode != 0:
        log.warning("ffmpeg %s failed (%s): %s", audio_filter, proc.returncode, (proc.stderr or "")[-300:])
        return None
    return proc.stderr or ""


def _parse_silences(stderr: str, duration: float | None) -> list[dict[str, float]]:
    pauses: list[dict[str, float]] = []
    start: float | None = None
    for line in stderr.splitlines():
        try:
            m_start = _SILENCE_START_RE.search(line)
            if m_start:
                start = float(m_start.group(1))
                continue
            m_end = _SILENCE_END_RE.search(line)
            if m_end:
                end = float(m_end.group(1))
                length = float(m_end.group(2))
                begin = start if start is not None else end - length
                pauses.append(
                    {"start": round(max(begin, 0.0), 3), "end": round(end, 3), "duration": round(length, 3)}
                )
                start = None
        except ValueError:
            continue
    if start is not None and duration is not None and duration > start:
        pauses.append(
            {"start": round(start, 3), "end": round(duration, 3), "duration": round(duration - start, 3)}
        )
    return pauses


def _parse_lra(stderr: str) -> float | None:
    matches = _LRA_RE.findall(stderr)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _duration_from_stderr(stderr: str | None) -> float | None:
    if not stderr:
        return None
    matches = _TIME_RE.findall(stderr)
    if not matches:
        return None
    hours, minutes, seconds = matches[-1]
    try:
        value = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return value if value > 0 else None


# --- presentation ---------------------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _summary_ru(f: dict[str, Any]) -> str:
    parts: list[str] = []
    wpm = f.get("operator_wpm")
    parts.append(f"Темп {round(wpm)} сл/мин" if wpm is not None else "Темп речи не определён")

    pauses = f.get("long_pauses_count")
    if pauses is None:
        parts.append("паузы не измерены")
    elif pauses == 0:
        parts.append("без длинных пауз")
    else:
        parts.append(f"{pauses} {_plural_ru(pauses, 'длинная пауза', 'длинные паузы', 'длинных пауз')}")

    ratio = f.get("talk_ratio_operator")
    if ratio is not None:
        parts.append(f"доля речи оператора {round(ratio * 100)}%")
    else:
        parts.append("доля речи оператора не определена")

    holds = f.get("hold_segments") or []
    if holds:
        parts.append(f"{len(holds)} {_plural_ru(len(holds), 'удержание', 'удержания', 'удержаний')}")
    return ", ".join(parts)
