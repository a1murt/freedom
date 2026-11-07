"""Data-source adapters: organizer Power Automate GET, uploaded JSON, pasted text."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import settings

ORGANIZER_TIMEOUT = 60.0


class IngestError(Exception):
    pass


def fetch_organizer(group_id: str) -> dict[str, Any]:
    if not settings.organizer_api_url:
        raise IngestError("ORGANIZER_API_URL is not configured")
    url = settings.organizer_api_url
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}group_id={group_id}"
    with httpx.Client(timeout=ORGANIZER_TIMEOUT, follow_redirects=True) as client:
        r = client.get(full)
    if r.status_code != 200:
        raise IngestError(f"Organizer API returned HTTP {r.status_code}")
    try:
        payload = r.json()
    except json.JSONDecodeError as e:
        raise IngestError(f"Organizer API returned non-JSON: {e}") from e
    if not payload.get("isSuccess", False):
        raise IngestError(f"Organizer API error: {payload.get('error') or 'isSuccess=false'}")
    return payload


def records_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return payload["records"]
    if isinstance(payload, dict) and "transcript" in payload:
        return [payload]
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict) and "transcript" in p]
    raise IngestError("Unrecognized JSON shape: expected organizer payload, a record, or a list of records")


LINE_RE = re.compile(r"^\s*(?:\[(?P<ts>[^\]]+)\]\s*)?(?P<speaker>client|operator|bot|system|клиент|оператор|бот|система)\s*[:\t]\s*(?P<text>.+)$", re.I)
SPEAKER_MAP = {"клиент": "client", "оператор": "operator", "бот": "bot", "система": "system"}


def record_from_text(text: str, conversation_id: str, channel: str = "agent chat", language: str = "RU") -> dict[str, Any]:
    """Parse pasted text. Accepts 'speaker<TAB>text', 'speaker: text', or '[MM:SS] speaker: text' lines."""
    transcript = []
    sec = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            # continuation of previous line
            if transcript:
                transcript[-1]["text"] += " " + line.strip()
            continue
        speaker = m.group("speaker").lower()
        speaker = SPEAKER_MAP.get(speaker, speaker)
        ts = m.group("ts") or f"{sec // 60:02d}:{sec % 60:02d}"
        transcript.append({"timestamp": ts, "speaker": speaker, "text": m.group("text").strip()})
        sec += 10
    if not transcript:
        raise IngestError("No transcript lines recognized. Use 'speaker: text' or '[MM:SS] speaker: text' per line.")
    return {"conversation_id": conversation_id, "channel": channel, "language": language, "transcript": transcript, "audio_url": None}
