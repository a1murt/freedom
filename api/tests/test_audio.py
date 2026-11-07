"""Tests for the audio module: player-page parsing, feature extraction and the audio router.

The sample player page / MP3 live outside the repo (binaries are not committed); tests that
need them are skipped when the files are missing.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# The repo .env may point DATABASE_URL at an async driver; the sync engine in cqa.db must still
# import here. Environment variables take priority over dotenv values in pydantic-settings.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.audio import FEATURE_KEYS, AudioError, compute_features, extract_media_url
from cqa.audio import resolver as resolver_mod
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.models import AudioAsset, Conversation, Turn
from cqa.routers import audio as audio_router

SAMPLE_DIR = Path(
    "/private/tmp/claude-501/-Users-k4ssym--claude-spares-spare-sCVwL5/"
    "02572ec3-fc12-448e-97dd-11d655c1b409/scratchpad/data"
)
SAMPLE_HTML = SAMPLE_DIR / "p4-ru-001.audio"
SAMPLE_MP3 = SAMPLE_DIR / "p4-ru-001.mp3"
FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg")) or Path("/opt/homebrew/bin/ffmpeg").exists()

needs_sample_html = pytest.mark.skipif(not SAMPLE_HTML.is_file(), reason="sample player page not available")
needs_sample_mp3 = pytest.mark.skipif(
    not (SAMPLE_MP3.is_file() and FFMPEG_AVAILABLE), reason="sample MP3 or ffmpeg not available"
)

# A tiny "MP3": ID3 header followed by an MPEG frame sync, enough for the magic-byte check.
FAKE_MP3 = b"ID3" + bytes(13) + b"\xff\xfb" + bytes(range(256)) * 4

SAMPLE_TURNS = [
    {
        "idx": 0,
        "speaker": "bot",
        "text": "Здравствуйте, вы позвонили в Freedom Broker",
        "offset_seconds": 0.0,
        "segment": "pre",
        "is_duplicate": False,
    },
    {
        "idx": 1,
        "speaker": "operator",
        "text": "Здравствуйте, меня зовут Айгерим, чем могу помочь?",
        "offset_seconds": 18.0,
        "segment": "operator",
        "is_duplicate": False,
    },
    {
        "idx": 2,
        "speaker": "client",
        "text": "Добрый день, хочу уточнить по переводу",
        "offset_seconds": 24.0,
        "segment": "operator",
        "is_duplicate": False,
    },
    {
        "idx": 3,
        "speaker": "operator",
        "text": "Секунду, уточню информацию, оставайтесь на линии",
        "offset_seconds": 30.0,
        "segment": "operator",
        "is_duplicate": False,
    },
    {
        "idx": 4,
        "speaker": "system",
        "text": "[удержание]",
        "offset_seconds": 39.0,
        "segment": "internal",
        "is_duplicate": False,
    },
    {
        "idx": 5,
        "speaker": "operator",
        "text": "Спасибо за ожидание",
        "offset_seconds": 63.0,
        "segment": "operator",
        "is_duplicate": False,
    },
    {
        "idx": 6,
        "speaker": "client",
        "text": "Да",
        "offset_seconds": 63.2,
        "segment": "operator",
        "is_duplicate": False,
    },
    {
        "idx": 7,
        "speaker": "operator",
        "text": "Спасибо за ожидание",
        "offset_seconds": 70.0,
        "segment": "operator",
        "is_duplicate": False,
    },
    {
        "idx": 8,
        "speaker": "operator",
        "text": "Спасибо за ожидание",
        "offset_seconds": 70.0,
        "segment": "operator",
        "is_duplicate": True,
    },
    {
        "idx": 9,
        "speaker": "operator",
        "text": "Всего доброго, до свидания",
        "offset_seconds": 300.0,
        "segment": "post",
        "is_duplicate": False,
    },
]


# --- extract_media_url ----------------------------------------------------------------------


@needs_sample_html
def test_extract_media_url_from_sample_page():
    url = extract_media_url(SAMPLE_HTML.read_text(encoding="utf-8"))
    assert url is not None
    assert url.startswith("https://freedomholdingcorporation.crm11.dynamics.com/api/filedownload/")
    assert "&amp;" not in url
    assert "&rsct=audio%2Fmpeg" in url and "&sig=" in url


def test_extract_media_url_unescapes_and_prefers_source():
    html = (
        '<audio id="a" controls src="https://cdn.example/fallback.mp3">'
        '<source src="https://cdn.example/a.mp3?sp=r&amp;sig=x%2By&amp;rsct=audio%2Fmpeg" type="audio/mpeg">'
        "</audio>"
    )
    assert extract_media_url(html) == "https://cdn.example/a.mp3?sp=r&sig=x%2By&rsct=audio%2Fmpeg"


def test_extract_media_url_audio_src_fallback_and_quotes():
    assert (
        extract_media_url("<AUDIO controls src='https://cdn.example/b.mp3'></AUDIO>")
        == "https://cdn.example/b.mp3"
    )
    assert extract_media_url("<audio src=https://cdn.example/c.mp3 controls>") == "https://cdn.example/c.mp3"


def test_extract_media_url_none_when_absent():
    assert extract_media_url("<html><body><h3>Запись</h3></body></html>") is None
    assert extract_media_url("") is None
    assert extract_media_url('<source src="" type="audio/mpeg">') is None


# --- compute_features -----------------------------------------------------------------------


@needs_sample_mp3
def test_compute_features_on_sample_mp3():
    features = compute_features(SAMPLE_MP3, SAMPLE_TURNS)

    assert list(features) == list(FEATURE_KEYS)
    assert abs(features["duration_s"] - 312.26) < 1.0

    assert features["long_pauses"] is not None
    assert features["long_pauses_count"] == len(features["long_pauses"]) >= 1
    for pause in features["long_pauses"]:
        assert pause["duration"] >= 3.0
        assert pause["start"] < pause["end"]
        assert abs((pause["end"] - pause["start"]) - pause["duration"]) < 0.01
    assert isinstance(features["loudness_range_lu"], float) and features["loudness_range_lu"] > 0

    assert features["overlaps"] == 1  # turns 5 -> 6 are 0.2 s apart
    assert features["hold_segments"] == [{"start": 39.0, "end": 63.0}]
    assert features["repeated_operator_lines"] == 1  # "Спасибо за ожидание" x2, flagged duplicate ignored

    assert features["operator_talk_s"] > 0 and features["client_talk_s"] > 0
    assert 0 < features["talk_ratio_operator"] < 1
    assert features["operator_wpm"] > 0
    expected_label = (
        "slow" if features["operator_wpm"] < 100 else "fast" if features["operator_wpm"] > 170 else "normal"
    )
    assert features["speech_rate_label"] == expected_label

    assert features["summary_ru"].startswith("Темп ")
    assert "сл/мин" in features["summary_ru"] and "%" in features["summary_ru"]


def test_compute_features_degrades_without_audio(tmp_path):
    features = compute_features(tmp_path / "missing.mp3", SAMPLE_TURNS)

    assert list(features) == list(FEATURE_KEYS)
    assert features["duration_s"] == 300.0  # falls back to the last turn offset
    assert features["long_pauses"] is None and features["long_pauses_count"] is None
    assert features["loudness_range_lu"] is None
    assert features["overlaps"] == 1
    assert features["hold_segments"] == [{"start": 39.0, "end": 63.0}]
    assert features["repeated_operator_lines"] == 1
    assert features["operator_wpm"] is not None
    assert "паузы не измерены" in features["summary_ru"]


def test_compute_features_empty_turns(tmp_path):
    features = compute_features(tmp_path / "missing.mp3", [])
    assert list(features) == list(FEATURE_KEYS)
    assert features["duration_s"] is None
    assert features["operator_wpm"] is None and features["speech_rate_label"] is None
    assert features["talk_ratio_operator"] is None
    assert features["hold_segments"] == [] and features["overlaps"] == 0
    assert isinstance(features["summary_ru"], str) and features["summary_ru"]


# --- resolve_and_cache ------------------------------------------------------------------------


def _mock_client(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_resolve_and_cache_re_resolves_expired_link(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    page_hits: list[str] = []
    media_hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://flow.example/player"):
            page_hits.append(url)
            token = f"tok{len(page_hits) - 1}"
            html = (
                "<audio controls><source "
                f'src="https://blob.example/file?sp=r&amp;sig={token}&amp;rsct=audio%2Fmpeg" type="audio/mpeg">'
                "</audio>"
            )
            return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})
        media_hits.append(url)
        if "sig=tok0" in url:  # first SAS link "expired"
            return httpx.Response(
                403, text="<Error>AuthenticationFailed</Error>", headers={"content-type": "application/xml"}
            )
        return httpx.Response(200, content=FAKE_MP3, headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(resolver_mod, "_make_client", _mock_client(handler))
    path = resolver_mod.resolve_and_cache("conv-1", "https://flow.example/player?x=1")

    assert path == tmp_path / "audio" / "conv-1.mp3"
    assert path.read_bytes() == FAKE_MP3
    assert len(page_hits) == 2 and len(media_hits) == 2
    assert "&amp;" not in media_hits[0] and "sig=tok0" in media_hits[0] and "sig=tok1" in media_hits[1]
    assert not list((tmp_path / "audio").glob("*.part"))

    # Second call is served from the cache without touching the network.
    def no_network():
        raise AssertionError("network must not be used for a cached file")

    monkeypatch.setattr(resolver_mod, "_make_client", no_network)
    assert resolver_mod.resolve_and_cache("conv-1", "https://flow.example/player?x=1") == path


def test_resolve_and_cache_raises_readable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if "player" in str(request.url):
            return httpx.Response(
                200, text='<source src="https://blob.example/f?sig=1">', headers={"content-type": "text/html"}
            )
        return httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"})

    monkeypatch.setattr(resolver_mod, "_make_client", _mock_client(handler))
    with pytest.raises(AudioError) as excinfo:
        resolver_mod.resolve_and_cache("conv-2", "https://flow.example/player")
    assert "не аудио" in str(excinfo.value)
    assert not (tmp_path / "audio" / "conv-2.mp3").exists()
    assert not list((tmp_path / "audio").glob("*.part"))


def test_resolve_and_cache_page_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        resolver_mod, "_make_client", _mock_client(lambda request: httpx.Response(500, text="boom"))
    )
    with pytest.raises(AudioError, match="HTTP 500"):
        resolver_mod.resolve_and_cache("conv-3", "https://flow.example/player")


# --- router -------------------------------------------------------------------------------------


@pytest.fixture
def fake_mp3(tmp_path) -> Path:
    path = tmp_path / "fake.mp3"
    path.write_bytes(FAKE_MP3)
    return path


@pytest.fixture
def app_env(monkeypatch, fake_mp3):
    """A FastAPI app with only the audio router, an in-memory SQLite and a seeded voice call."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with TestSession() as db:
        db.add(
            Conversation(
                id="call-1",
                channel="voice call",
                language="RU",
                audio_url="https://flow.example/player",
                turn_count=3,
            )
        )
        db.add(Conversation(id="chat-1", channel="agent chat", language="RU", audio_url=None, turn_count=0))
        db.add_all(
            [
                Turn(
                    conversation_id="call-1",
                    idx=0,
                    timestamp_raw="00:00:00",
                    offset_seconds=0.0,
                    speaker="operator",
                    text="Здравствуйте",
                    segment="operator",
                ),
                Turn(
                    conversation_id="call-1",
                    idx=1,
                    timestamp_raw="00:00:05",
                    offset_seconds=5.0,
                    speaker="client",
                    text="Добрый день",
                    segment="operator",
                ),
                Turn(
                    conversation_id="call-1",
                    idx=2,
                    timestamp_raw="00:00:09",
                    offset_seconds=9.0,
                    speaker="operator",
                    text="Чем могу помочь?",
                    segment="operator",
                ),
            ]
        )
        db.commit()

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(audio_router.router)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        audio_router, "resolve_and_cache", lambda conversation_id, audio_url, force=False: fake_mp3
    )
    return {"app": app, "Session": TestSession, "mp3": fake_mp3}


@pytest.fixture
def client(app_env) -> TestClient:
    with TestClient(app_env["app"]) as c:
        yield c


def test_audio_range_request(client):
    resp = client.get("/api/audio/call-1", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.content == FAKE_MP3[:100]
    assert resp.headers["content-range"] == f"bytes 0-99/{len(FAKE_MP3)}"
    assert resp.headers["content-length"] == "100"
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["cache-control"] == "private, max-age=3600"

    tail = client.get("/api/audio/call-1", headers={"Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == FAKE_MP3[-50:]


def test_audio_full_get_and_head(client, app_env):
    resp = client.get("/api/audio/call-1")
    assert resp.status_code == 200
    assert resp.content == FAKE_MP3
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["cache-control"] == "private, max-age=3600"

    head = client.head("/api/audio/call-1")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(FAKE_MP3))

    with app_env["Session"]() as db:
        asset = db.get(AudioAsset, "call-1")
        assert asset is not None and asset.local_path == str(app_env["mp3"]) and asset.fetched_at is not None


def test_audio_404_without_audio_url(client):
    resp = client.get("/api/audio/chat-1")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "no_audio"

    missing = client.get("/api/audio/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_audio_502_on_audio_error(client, monkeypatch):
    def boom(conversation_id, audio_url, force=False):
        raise AudioError("Страница плеера вернула HTTP 403")

    monkeypatch.setattr(audio_router, "resolve_and_cache", boom)
    for url in ("/api/audio/call-1", "/api/audio/call-1/features"):
        resp = client.get(url)
        assert resp.status_code == 502
        assert resp.json() == {
            "error": {"code": "audio_unavailable", "message": "Страница плеера вернула HTTP 403"}
        }


def test_features_computed_once_then_cached_and_refreshed(client, app_env, monkeypatch):
    calls: list[list[dict]] = []

    def fake_compute(path, turns):
        calls.append(turns)
        return {"duration_s": 12.5, "operator_wpm": 120.0, "summary_ru": f"запуск {len(calls)}"}

    monkeypatch.setattr(audio_router, "compute_features", fake_compute)

    first = client.get("/api/audio/call-1/features")
    assert first.status_code == 200
    assert first.json()["summary_ru"] == "запуск 1"
    assert len(calls) == 1
    assert [t["idx"] for t in calls[0]] == [0, 1, 2]
    assert calls[0][1] == {
        "idx": 1,
        "speaker": "client",
        "text": "Добрый день",
        "offset_seconds": 5.0,
        "segment": "operator",
        "is_duplicate": False,
    }

    with app_env["Session"]() as db:
        asset = db.get(AudioAsset, "call-1")
        assert asset.features["summary_ru"] == "запуск 1"
        assert asset.duration_s == 12.5
        assert asset.local_path == str(app_env["mp3"]) and asset.fetched_at is not None

    second = client.get("/api/audio/call-1/features")
    assert second.json()["summary_ru"] == "запуск 1" and len(calls) == 1

    refreshed = client.get("/api/audio/call-1/features", params={"refresh": "1"})
    assert refreshed.json()["summary_ru"] == "запуск 2" and len(calls) == 2
    with app_env["Session"]() as db:
        assert db.get(AudioAsset, "call-1").features["summary_ru"] == "запуск 2"

    assert client.get("/api/audio/chat-1/features").status_code == 404


@needs_sample_mp3
def test_features_endpoint_end_to_end(client, monkeypatch):
    monkeypatch.setattr(
        audio_router, "resolve_and_cache", lambda conversation_id, audio_url, force=False: SAMPLE_MP3
    )
    resp = client.get("/api/audio/call-1/features")
    assert resp.status_code == 200
    body = resp.json()
    assert list(body) == list(FEATURE_KEYS)
    assert abs(body["duration_s"] - 312.26) < 1.0
    assert body["long_pauses_count"] >= 1
