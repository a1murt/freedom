"""Waveform peaks for the two-track call view (organizer mockup: "Клиент" and "Оператор" rows).

The recording is a mono mix, so the tracks are derived from the transcript timing: each bucket of the
waveform is attributed to the speaker whose turn covers that moment (client, operator, or other for
bot/IVR/system). The UI draws one row per speaker and highlights the evidence fragment on top.
"""

from __future__ import annotations

import array
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from .tools import FFMPEG

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000
MAX_TURN_SECONDS = 30.0  # a turn's span ends at the next turn, capped so long silences are not attributed


def _decode_pcm(path: Path, timeout: float = 120.0) -> array.array | None:
    try:
        proc = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("ffmpeg decode failed for %s: %s", path, exc)
        return None
    if proc.returncode != 0 or not proc.stdout:
        log.warning("ffmpeg decode returned %s for %s: %s", proc.returncode, path, proc.stderr[-300:])
        return None
    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) - (len(proc.stdout) % 2)])
    return pcm


def _speaker_spans(turns: list[dict[str, Any]], duration: float) -> list[tuple[float, float, str]]:
    timed = sorted((t for t in turns if t.get("offset_seconds") is not None and not t.get("is_duplicate")), key=lambda t: t["offset_seconds"])
    spans: list[tuple[float, float, str]] = []
    for i, t in enumerate(timed):
        start = float(t["offset_seconds"])
        nxt = float(timed[i + 1]["offset_seconds"]) if i + 1 < len(timed) else duration
        end = min(max(start, nxt), start + MAX_TURN_SECONDS, duration)
        sp = t.get("speaker")
        role = "client" if sp == "client" else "operator" if sp == "operator" and t.get("segment") != "internal" else "other"
        if end > start:
            spans.append((start, end, role))
    return spans


def compute_peaks(path: Path, turns: list[dict[str, Any]], buckets: int = 600) -> dict[str, Any]:
    """Return normalised peaks (0..1) plus per-speaker tracks and the turn spans used to build them."""
    buckets = max(50, min(int(buckets), 4000))
    pcm = _decode_pcm(path)
    if pcm is None or len(pcm) == 0:
        return {"duration_s": None, "buckets": 0, "peaks": [], "tracks": {"client": [], "operator": [], "other": []}, "spans": [], "available": False}
    n = len(pcm)
    duration = n / SAMPLE_RATE
    step = max(1, n // buckets)
    raw: list[int] = []
    for b in range(buckets):
        lo = b * step
        hi = n if b == buckets - 1 else min(n, lo + step)
        if lo >= n:
            raw.append(0)
            continue
        chunk = pcm[lo:hi]
        raw.append(max(max(chunk), -min(chunk)) if len(chunk) else 0)
    top = max(raw) or 1
    # soft compression so quiet speech is still visible next to loud segments
    peaks = [round((v / top) ** 0.6, 4) for v in raw]

    spans = _speaker_spans(turns, duration)
    tracks = {"client": [0.0] * buckets, "operator": [0.0] * buckets, "other": [0.0] * buckets}
    bucket_seconds = duration / buckets
    for b in range(buckets):
        t_mid = (b + 0.5) * bucket_seconds
        role = None
        for start, end, r in spans:
            if start <= t_mid < end:
                role = r
                break
        tracks[role or "other"][b] = peaks[b]
    return {
        "duration_s": round(duration, 2),
        "buckets": buckets,
        "bucket_seconds": round(bucket_seconds, 4),
        "peaks": peaks,
        "tracks": tracks,
        "spans": [{"start": round(s, 2), "end": round(e, 2), "speaker": r} for s, e, r in spans],
        "available": True,
    }


def _timing_key(turns: list[dict[str, Any]]) -> str:
    """Fingerprint of what the tracks depend on (who speaks when); a re-normalized transcript invalidates the cache."""
    import hashlib

    rows = [[t.get("idx"), t.get("speaker"), t.get("offset_seconds"), t.get("segment"), bool(t.get("is_duplicate"))] for t in turns]
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]


def cached_peaks(path: Path, turns: list[dict[str, Any]], buckets: int = 600) -> dict[str, Any]:
    cache = path.with_suffix(f".peaks{buckets}.json")
    key = _timing_key(turns)
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("timing_key") in (None, key) and (data.get("timing_key") == key or not turns):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    data = compute_peaks(path, turns, buckets)
    if data.get("available"):
        data["timing_key"] = key
        try:
            cache.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
    return data
