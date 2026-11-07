"""Resolve the organizer's audio player page to an MP3 and cache it on disk.

``Conversation.audio_url`` (voice calls only) points at a Power Automate page that returns a
small HTML player, not audio. The real media lives in its ``<source src="...">``: a Dynamics
``filedownload`` link with a SAS token valid for roughly one hour. We fetch the page, pull the
link out, stream the MP3 into ``data/audio/{conversation_id}.mp3`` and serve it from there.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Iterable
from html import unescape
from itertools import chain
from pathlib import Path
from urllib.parse import urljoin

import httpx

from ..config import settings
from .tools import FFPROBE, run_tool

log = logging.getLogger(__name__)

PAGE_TIMEOUT_S = 60.0
CHUNK_SIZE = 256 * 1024
USER_AGENT = "cqa-audio/1.0 (+https://github.com/freedom-cqa)"


class AudioError(Exception):
    """Audio could not be resolved or downloaded; the message is safe to show to a user."""


class _DownloadFailed(AudioError):
    """The media link did not yield audio (expired SAS token, HTTP error, HTML body); retried once."""


# <source src="..."> first, then <audio src="...">. Quoted (either quote) or bare values.
_SRC_RE = {
    tag: re.compile(
        rf"<{tag}\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        re.IGNORECASE | re.DOTALL,
    )
    for tag in ("source", "audio")
}
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def extract_media_url(html: str) -> str | None:
    """Return the first ``<source src>`` (or ``<audio src>``) of a player page, HTML-unescaped."""
    if not html:
        return None
    for tag in ("source", "audio"):
        for match in _SRC_RE[tag].finditer(html):
            raw = next((g for g in match.groups() if g is not None), "")
            url = unescape(raw).strip()
            if url:
                return url
    return None


def audio_dir() -> Path:
    return Path(settings.data_dir) / "audio"


def cache_path(conversation_id: str) -> Path:
    safe = _SAFE_ID_RE.sub("_", str(conversation_id)).strip("._") or "conversation"
    return audio_dir() / f"{safe}.mp3"


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(conversation_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(conversation_id, threading.Lock())


def _make_client() -> httpx.Client:
    """Factory for the HTTP client (patched in tests with a MockTransport)."""
    return httpx.Client(
        timeout=httpx.Timeout(PAGE_TIMEOUT_S),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )


def resolve_and_cache(conversation_id: str, audio_url: str, force: bool = False) -> Path:
    """Return the local MP3 for a conversation, downloading it through the player page if needed.

    Raises :class:`AudioError` with a readable message when the audio cannot be obtained.
    """
    dest = cache_path(conversation_id)
    if not force and dest.is_file() and dest.stat().st_size > 0:
        return dest
    if not audio_url:
        raise AudioError(f"У разговора {conversation_id} нет ссылки на аудиозапись")

    with _lock_for(conversation_id):
        # Another request may have finished the download while we waited for the lock.
        if not force and dest.is_file() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _make_client() as client:
                _resolve_into(client, conversation_id, audio_url, dest)
        except AudioError:
            raise
        except httpx.HTTPError as exc:
            raise AudioError(f"Сетевая ошибка при загрузке аудио {conversation_id}: {exc}") from exc
        except OSError as exc:
            raise AudioError(f"Не удалось сохранить аудио {conversation_id}: {exc}") from exc
    return dest


def _resolve_into(client: httpx.Client, conversation_id: str, audio_url: str, dest: Path) -> None:
    media_url = _resolve_media_url(client, audio_url, dest)
    if media_url is None:  # the "player" URL served the audio itself and it is already saved
        return
    try:
        _download(client, media_url, dest)
        return
    except _DownloadFailed as first:
        log.info("audio %s: media link failed (%s); re-resolving player page", conversation_id, first)
        first_error = str(first)

    # The SAS token may have expired between resolving and downloading: resolve once more.
    media_url = _resolve_media_url(client, audio_url, dest)
    if media_url is None:
        return
    try:
        _download(client, media_url, dest)
    except _DownloadFailed as second:
        raise AudioError(
            f"Не удалось скачать аудио {conversation_id}: первая попытка — {first_error}; "
            f"повтор после обновления ссылки — {second}"
        ) from second


def _resolve_media_url(client: httpx.Client, page_url: str, dest: Path) -> str | None:
    """GET the player page and return the media URL; ``None`` if the page was audio and got saved."""
    with client.stream("GET", page_url) as resp:
        if resp.status_code != 200:
            raise AudioError(f"Страница плеера вернула HTTP {resp.status_code}")
        content_type = _content_type(resp)
        if content_type.startswith("audio/"):
            _atomic_write(dest, resp.iter_bytes(chunk_size=CHUNK_SIZE))
            return None
        body = resp.read()
    if _looks_like_audio(content_type, body[:16]):
        _atomic_write(dest, [body])
        return None
    text = body.decode(resp.encoding or "utf-8", errors="replace")
    media = extract_media_url(text)
    if not media:
        raise AudioError("Страница плеера не содержит ссылки на аудио (<source>/<audio> src)")
    return urljoin(str(resp.url), media)


def _download(client: httpx.Client, media_url: str, dest: Path) -> None:
    with client.stream("GET", media_url) as resp:
        if resp.status_code != 200:
            raise _DownloadFailed(f"HTTP {resp.status_code} по ссылке на медиафайл")
        content_type = _content_type(resp)
        chunks = resp.iter_bytes(chunk_size=CHUNK_SIZE)
        head = next(chunks, b"")
        if not _looks_like_audio(content_type, head[:16]):
            raise _DownloadFailed(f"ссылка вернула не аудио (content-type {content_type or 'не указан'!r})")
        _atomic_write(dest, chain([head], chunks))


def _atomic_write(dest: Path, chunks: Iterable[bytes]) -> None:
    """Write chunks to a temp file next to ``dest`` and rename it into place; never leave partials behind."""
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dest.parent, prefix=f".{dest.stem}.", suffix=".part", delete=False
        ) as tmp:
            tmp_name = tmp.name
            size = 0
            for chunk in chunks:
                tmp.write(chunk)
                size += len(chunk)
        if size == 0:
            raise _DownloadFailed("ссылка вернула пустой файл")
        os.replace(tmp_name, dest)
    except BaseException:
        if tmp_name:
            _unlink_quietly(tmp_name)
        raise


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _content_type(resp: httpx.Response) -> str:
    return resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _looks_like_audio(content_type: str, head: bytes) -> bool:
    if content_type.startswith("audio/"):
        return True
    if content_type in ("", "application/octet-stream", "binary/octet-stream"):
        return _has_mp3_magic(head)
    return False


def _has_mp3_magic(head: bytes) -> bool:
    if head.startswith(b"ID3"):
        return True
    return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0  # MPEG frame sync


def probe_duration(path: Path) -> float | None:
    """Duration in seconds via ``ffprobe`` JSON output; ``None`` if it cannot be determined."""
    path = Path(path)
    if not path.is_file():
        return None
    proc = run_tool(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=60,
    )
    if proc is None or proc.returncode != 0:
        return None
    try:
        info = json.loads(proc.stdout or "{}")
    except ValueError:
        return None
    candidates = [info.get("format", {}).get("duration")]
    candidates += [s.get("duration") for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    for value in candidates:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return None
