"""Re-derive call timing from the stored transcripts after a normalizer change (docs/USER_STORIES.md CALL-03).

Stored turns keep everything ``normalize`` reads (raw timestamp, speaker, text), so the call clock can be recomputed
without the organizer API: audio offsets now count from the recording-start event. For every voice call whose offsets
differ, the turn offsets and the recording fields of ``conversations.meta`` are updated, the stored acoustic features are
recomputed from the cached recording when it is on disk (else cleared, so they are computed again on the next access),
and cached waveform peaks are dropped. Transcript text, segments, duplicates and evaluations are not touched.

Idempotent and never raises (start-up hook in ``cqa.main``); CLI: ``python -m cqa.ingest.renormalize``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AudioAsset, Conversation
from .normalize import normalize

log = logging.getLogger("cqa.ingest")

RECORDING_KEYS = ("recording_start_idx", "recording_start_offset_s", "not_in_recording")
EPS = 1e-6


def _differs(a: float | None, b: float | None) -> bool:
    return (a is None) != (b is None) or (a is not None and b is not None and abs(a - b) > EPS)


def _drop_peaks(conversation_id: str, audio_path: str | None) -> int:
    removed = 0
    candidates: list[Path] = []
    if audio_path:
        p = Path(audio_path)
        candidates += list(p.parent.glob(f"{p.stem}.peaks*.json"))
    try:
        from ..config import settings

        candidates += list((Path(settings.data_dir) / "audio").glob(f"{conversation_id}.peaks*.json"))
    except Exception:  # pragma: no cover - settings always import
        pass
    for c in set(candidates):
        try:
            c.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _refresh_features(db: Session, conv: Conversation) -> str:
    asset = db.get(AudioAsset, conv.id)
    if asset is None or not asset.features:
        return "none"
    path = Path(asset.local_path) if asset.local_path else None
    if path is not None and path.is_file():
        try:
            from ..audio import compute_features

            turns = [{"idx": t.idx, "speaker": t.speaker, "text": t.text, "offset_seconds": t.offset_seconds, "segment": t.segment,
                      "is_duplicate": t.is_duplicate} for t in sorted(conv.turns, key=lambda t: t.idx)]
            asset.features = compute_features(path, turns)
            return "recomputed"
        except Exception as e:  # ffmpeg missing or a broken file: clear rather than keep wrong timings
            log.warning("renormalize %s: features not recomputed (%s)", conv.id, e)
    asset.features = None
    return "cleared"


def renormalize_call_offsets(session_factory: Callable[[], Session] | None = None) -> dict[str, Any]:
    """Recompute audio offsets of stored voice calls; returns {"checked", "updated": [ids], "features": {id: action}}."""
    out: dict[str, Any] = {"checked": 0, "updated": [], "features": {}, "peaks_removed": 0}
    try:
        if session_factory is None:
            from ..db import SessionLocal as session_factory  # noqa: N813
        db = session_factory()
    except Exception as e:  # pragma: no cover - defensive
        log.warning("renormalize: no database session (%s)", e)
        return out
    try:
        for conv in db.execute(select(Conversation).where(Conversation.channel == "voice call")).scalars():
            out["checked"] += 1
            turns = sorted(conv.turns, key=lambda t: t.idx)
            if not turns:
                continue
            rec = {"conversation_id": conv.id, "channel": conv.channel, "language": conv.language,
                   "transcript": [{"timestamp": t.timestamp_raw, "speaker": t.speaker, "text": t.text} for t in turns]}
            norm = normalize(rec)
            if len(norm.turns) != len(turns):
                continue
            changed = False
            for t, nt in zip(turns, norm.turns):
                if t.idx == nt.idx and _differs(t.offset_seconds, nt.offset_seconds):
                    t.offset_seconds = nt.offset_seconds
                    changed = True
            meta = dict(conv.meta or {})
            new_rec = {k: norm.meta[k] for k in RECORDING_KEYS if k in norm.meta}
            if {k: meta.get(k) for k in RECORDING_KEYS if k in meta} != new_rec:
                for k in RECORDING_KEYS:
                    meta.pop(k, None)
                meta.update(new_rec)
                conv.meta = meta
                changed = True
            if changed:
                db.flush()
                out["updated"].append(conv.id)
                out["features"][conv.id] = _refresh_features(db, conv)
                asset = db.get(AudioAsset, conv.id)
                out["peaks_removed"] += _drop_peaks(conv.id, asset.local_path if asset else None)
        db.commit()
        if out["updated"]:
            log.info("renormalize: call offsets updated for %s (features %s)", ", ".join(out["updated"]), out["features"])
    except Exception as e:
        db.rollback()
        log.warning("renormalize failed: %s", e)
    finally:
        db.close()
    return out


if __name__ == "__main__":  # pragma: no cover
    import json

    print(json.dumps(renormalize_call_offsets(), ensure_ascii=False, indent=2))
