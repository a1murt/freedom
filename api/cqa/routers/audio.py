"""Audio endpoints: stream a voice call's MP3 (with HTTP Range) and expose its computed features."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from ..audio import AudioError, compute_features, resolve_and_cache
from ..db import get_db
from ..models import AudioAsset, Conversation, utcnow

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])

AUDIO_HEADERS = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}

DbSession = Annotated[Session, Depends(get_db)]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _load_voice_conversation(db: Session, conversation_id: str) -> Conversation | JSONResponse:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return _error(404, "not_found", f"Разговор {conversation_id} не найден")
    if not conv.audio_url:
        return _error(404, "no_audio", f"У разговора {conversation_id} нет аудиозаписи")
    return conv


def _ensure_asset(db: Session, conversation_id: str) -> AudioAsset:
    asset = db.get(AudioAsset, conversation_id)
    if asset is None:
        asset = AudioAsset(conversation_id=conversation_id)
        db.add(asset)
    return asset


@router.get("/{conversation_id}")
@router.head("/{conversation_id}", include_in_schema=False)  # same handler; kept out of OpenAPI (no duplicate operation id)
def get_audio(conversation_id: str, db: DbSession):
    """Serve the cached MP3 for a voice call. Supports ``Range`` requests for seeking."""
    conv = _load_voice_conversation(db, conversation_id)
    if isinstance(conv, JSONResponse):
        return conv
    try:
        path = Path(resolve_and_cache(conv.id, conv.audio_url))
    except AudioError as exc:
        log.warning("audio %s unavailable: %s", conversation_id, exc)
        return _error(502, "audio_unavailable", str(exc))

    asset = _ensure_asset(db, conv.id)
    if asset.local_path != str(path):
        asset.local_path = str(path)
        asset.fetched_at = utcnow()
        db.commit()

    return FileResponse(path, media_type="audio/mpeg", headers=AUDIO_HEADERS)


@router.get("/{conversation_id}/peaks")
def get_audio_peaks(
    conversation_id: str,
    db: DbSession,
    buckets: Annotated[int, Query(ge=50, le=4000, description="Number of waveform buckets")] = 600,
):
    """Waveform peaks with client/operator tracks derived from transcript timing (two-track call view)."""
    from ..audio.peaks import cached_peaks

    conv = _load_voice_conversation(db, conversation_id)
    if isinstance(conv, JSONResponse):
        return conv
    try:
        path = Path(resolve_and_cache(conv.id, conv.audio_url))
    except AudioError as exc:
        log.warning("audio %s unavailable: %s", conversation_id, exc)
        return _error(502, "audio_unavailable", str(exc))
    turns = [
        {"idx": t.idx, "speaker": t.speaker, "offset_seconds": t.offset_seconds, "segment": t.segment, "is_duplicate": t.is_duplicate}
        for t in conv.turns
    ]
    return cached_peaks(path, turns, buckets)


@router.get("/{conversation_id}/features")
def get_audio_features(
    conversation_id: str,
    db: DbSession,
    refresh: Annotated[bool, Query(description="Recompute even if features are already stored")] = False,
):
    """Return the audio features for a voice call, computing and storing them on first access."""
    conv = _load_voice_conversation(db, conversation_id)
    if isinstance(conv, JSONResponse):
        return conv

    asset = db.get(AudioAsset, conv.id)
    if asset is not None and asset.features and not refresh:
        return asset.features

    try:
        path = Path(resolve_and_cache(conv.id, conv.audio_url))
    except AudioError as exc:
        log.warning("audio %s unavailable: %s", conversation_id, exc)
        return _error(502, "audio_unavailable", str(exc))

    turns = [
        {
            "idx": t.idx,
            "speaker": t.speaker,
            "text": t.text,
            "offset_seconds": t.offset_seconds,
            "segment": t.segment,
            "is_duplicate": t.is_duplicate,
        }
        for t in conv.turns
    ]
    try:
        features = compute_features(path, turns)
    except Exception as exc:  # compute_features degrades internally; this is a last resort
        log.exception("audio %s: feature extraction failed", conversation_id)
        return _error(502, "audio_unavailable", f"Не удалось вычислить признаки аудио: {exc}")

    asset = _ensure_asset(db, conv.id)
    asset.local_path = str(path)
    asset.duration_s = features.get("duration_s")
    asset.fetched_at = utcnow()
    asset.features = features
    db.commit()
    return features
