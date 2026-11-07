"""Round 3, disk: the free-space warning follows the live database, not a fixed 500 MiB.

The hosted Railway volume is 500 MB in all (454 MB usable), so `disk_low` was permanent although 355 MB were free next
to a 26 MB database. One rule (cqa.admin.snapshots.disk_status) now judges the data volume for the health warning and
agrees with the snapshot guard: low while fewer than 4 more snapshots of the live database (file + WAL) fit with the
32 MiB margin, and always below 64 MiB free. The review's repro is pinned on the hosted numbers: the 26 824 704-byte
database next to 355 MB free has no `disk_low`, and the warning starts below 4 × database + 32 MiB (about 134 МБ).

Temporary SQLite files and monkeypatched disk numbers only: no network, no Claude, never api/data.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cqa.admin import snapshots as snap
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.routers import system as system_router

MIB = 1024 * 1024
HOSTED_DB = 26_824_704  # the live hosted database in bytes (a copy: no WAL)
HOSTED_THRESHOLD = 4 * HOSTED_DB + 32 * MIB  # 140 853 248 bytes: below it `disk_low`, not below a fixed 500 MB
HEALTH_FIELDS = {"ok", "version", "uptime_s", "started_at", "db", "providers", "jobs", "disk_free_bytes", "budget", "snapshots",
                 "warnings"}


# ------------------------------------------------------------------------------------------------ the rule


@pytest.mark.parametrize("free_mb,db_mb,low,fit", [
    (355, 26, False, 12),  # the hosted instance: no warning
    (136, 26, False, 4),  # exactly 4 snapshots and the margin
    (135, 26, True, 3),
    (90, 26, True, 2),
    (58, 26, True, 1),  # one snapshot and the margin
    (57, 26, True, 0),
    (63, 1, True, 31),  # a tiny database: the 64 MiB floor decides
    (64, 1, False, 32),
    (4096, 1024, True, 3),  # a 1 GiB database wants 4 GiB + 32 MiB
])
def test_rule_counts_snapshots_of_the_live_database(free_mb, db_mb, low, fit):
    assert snap.disk_status(free_mb * MIB, db_mb * MIB) == {
        "low": low, "threshold_bytes": max(64 * MIB, 4 * db_mb * MIB + 32 * MIB), "snapshot_bytes": db_mb * MIB, "snapshots_fit": fit}


@pytest.mark.parametrize("db_bytes", [None, 0])
def test_rule_falls_back_to_the_floor_when_the_database_size_is_unknown(db_bytes):
    assert snap.disk_status(63 * MIB, db_bytes) == {"low": True, "threshold_bytes": 64 * MIB, "snapshot_bytes": None, "snapshots_fit": None}
    assert snap.disk_status(64 * MIB, db_bytes)["low"] is False
    assert snap.disk_status(355 * MIB, db_bytes)["low"] is False


def _creates_allowed(directory, monkeypatch, free: int, db: int) -> int:
    """How many snapshots _ensure_disk lets through one after another on `free` bytes."""
    disk = {"free": free}
    monkeypatch.setattr(snap.shutil, "disk_usage", lambda _p: SimpleNamespace(total=500 * MIB, used=0, free=disk["free"]))
    created = 0
    while True:
        try:
            snap._ensure_disk(directory, db)
        except snap.InsufficientDisk:
            return created
        created += 1
        disk["free"] -= db  # every snapshot takes the size of the database


@pytest.mark.parametrize("free_mb,db_mb", [(355, 26), (90, 26), (58, 26), (57, 26), (10, 26), (200, 3)])
def test_snapshots_fit_is_how_many_creates_the_guard_allows(free_mb, db_mb, tmp_path, monkeypatch):
    assert _creates_allowed(tmp_path, monkeypatch, free_mb * MIB, db_mb * MIB) == snap.disk_status(free_mb * MIB, db_mb * MIB)["snapshots_fit"]


# ------------------------------------------------------------------------------------------------ the health message


@pytest.mark.parametrize("free_mb,db_bytes,message", [
    (355, 26 * MIB, None),
    (90, 26 * MIB, "Мало места на диске с данными: свободно 90 МБ, это примерно 2 снимка базы по 26 МБ с запасом 32 МБ."),
    (80, 26 * MIB, "Мало места на диске с данными: свободно 80 МБ, это примерно 1 снимок базы по 26 МБ с запасом 32 МБ."),
    (130, int(26.4 * MIB), "Мало места на диске с данными: свободно 130 МБ, это примерно 3 снимка базы по 26.4 МБ с запасом 32 МБ."),
    (63, 3 * MIB // 2, "Мало места на диске с данными: свободно 63 МБ, это примерно 20 снимков базы по 1.5 МБ с запасом 32 МБ."),
    (40, 26 * MIB, "Мало места на диске с данными: свободно 40 МБ, а для снимка базы (26 МБ) нужно не меньше 58 МБ."),
    (50, None, "Мало места на диске с данными: свободно 50 МБ."),
    (100, None, None),
])
def test_disk_low_message_says_how_many_snapshots_still_fit(free_mb, db_bytes, message):
    assert system_router._disk_warning(free_mb * MIB, db_bytes) == message


def test_snapshot_size_is_file_plus_wal_and_only_for_sqlite():
    assert system_router._live_db_bytes({"kind": "sqlite", "size_bytes": 20 * MIB, "wal_bytes": 6 * MIB}) == 26 * MIB
    assert system_router._live_db_bytes({"kind": "sqlite", "size_bytes": 20 * MIB}) == 20 * MIB
    assert system_router._live_db_bytes({"kind": "sqlite", "size_bytes": None, "wal_bytes": 0}) is None  # no file yet
    assert system_router._live_db_bytes({"kind": "postgres", "size_bytes": 10**9}) is None  # not on the data volume


# ------------------------------------------------------------------------------------------------ GET /api/system/health


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'health.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    monkeypatch.setattr(settings, "data_dir", tmp_path)  # free space and the snapshot summary: never api/data
    monkeypatch.setattr(settings, "local_endpoint", None)  # no probe of a local model
    system_router._probe_cache.clear()
    app = FastAPI()
    app.include_router(system_router.router)

    def override():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    engine.dispose()


def _free(monkeypatch, free_bytes: int) -> None:
    monkeypatch.setattr(system_router.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=500 * MIB, used=500 * MIB - free_bytes, free=free_bytes))


def _disk_low(body: dict) -> list[str]:
    return [w["message"] for w in body["warnings"] if w["code"] == "disk_low"]


@pytest.mark.parametrize("free_mb,expected", [
    (355, []),
    (90, ["Мало места на диске с данными: свободно 90 МБ, это примерно 2 снимка базы по 26 МБ с запасом 32 МБ."]),
])
def test_health_judges_the_hosted_volume_by_the_database(client, monkeypatch, free_mb, expected):
    real_db_info = system_router._db_info
    monkeypatch.setattr(system_router, "_db_info", lambda db: {**real_db_info(db), "size_bytes": 20 * MIB, "wal_bytes": 6 * MIB})
    _free(monkeypatch, free_mb * MIB)
    body = client.get("/api/system/health").json()
    assert set(body) >= HEALTH_FIELDS and body["ok"] is True and body["disk_free_bytes"] == free_mb * MIB  # same fields as before
    assert (body["db"]["size_bytes"], body["db"]["wal_bytes"]) == (20 * MIB, 6 * MIB)
    assert all(set(w) == {"code", "message"} for w in body["warnings"])
    assert _disk_low(body) == expected


def test_health_with_a_small_database_warns_only_below_the_floor(client, monkeypatch):
    _free(monkeypatch, 64 * MIB)
    body = client.get("/api/system/health").json()
    assert 0 < body["db"]["size_bytes"] < 8 * MIB and _disk_low(body) == []  # 4 × size + 32 MiB < 64 MiB: the floor decides
    _free(monkeypatch, 63 * MIB)
    [message] = _disk_low(client.get("/api/system/health").json())
    assert message.startswith("Мало места на диске с данными: свободно 63 МБ, это примерно ") and message.endswith(" с запасом 32 МБ.")


# ------------------------------------------------------------------------------------------------ the hosted numbers (review repro)


@pytest.mark.parametrize("free,fit,message", [
    (355_000_000, 11, None),  # the free space Railway shows (decimal MB): the old fixed 500 MiB rule warned here
    (355 * MIB, 12, None),
    (HOSTED_THRESHOLD, 4, None),  # exactly 4 snapshots and the margin
    (HOSTED_THRESHOLD - 1, 3, "Мало места на диске с данными: свободно 134 МБ, это примерно 3 снимка базы по 25.6 МБ с запасом 32 МБ."),
    (90_000_000, 2, "Мало места на диске с данными: свободно 85 МБ, это примерно 2 снимка базы по 25.6 МБ с запасом 32 МБ."),
    (57_000_000, 0, "Мало места на диске с данными: свободно 54 МБ, а для снимка базы (25.6 МБ) нужно не меньше 57.6 МБ."),
], ids=["355MB", "355MiB", "threshold", "threshold-1", "90MB", "57MB"])
def test_health_on_the_hosted_database_numbers(client, monkeypatch, tmp_path, free, fit, message):
    real_db_info = system_router._db_info
    monkeypatch.setattr(system_router, "_db_info", lambda db: {**real_db_info(db), "size_bytes": HOSTED_DB, "wal_bytes": 0})
    _free(monkeypatch, free)
    body = client.get("/api/system/health").json()
    assert body["disk_free_bytes"] == free and _disk_low(body) == ([message] if message else [])
    # the count in the message is what the snapshot guard still lets through
    assert snap.disk_status(free, HOSTED_DB)["snapshots_fit"] == fit == _creates_allowed(tmp_path, monkeypatch, free, HOSTED_DB)
