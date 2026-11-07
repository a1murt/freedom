"""Package admin: SQLite snapshots (create, restore in place, list, delete, CLI) and the /api/admin router.

Everything runs on temporary files under tmp_path. The real demo database (api/data/cqa.db) is never opened:
settings.database_url / settings.data_dir point at tmp_path, cqa.db.engine / SessionLocal are swapped for a temp engine
in the API tests, and the hooks that could reach the global engine are proven to refuse a mismatched engine.
"""
from __future__ import annotations

import hashlib
import inspect as pyinspect
import json
import os
import sqlite3
import sys
import threading
import types
import warnings
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

import cqa
import cqa.db as cqa_db
from cqa.admin import snapshots as snap
from cqa.config import API_DIR, settings
from cqa.db import Base
from cqa.models import (
    Appeal,
    AuditEvent,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    Job,
    Turn,
)
from cqa.routers import admin as admin_router

REAL_DB = (API_DIR / "data" / "cqa.db").resolve()
BASE_COUNTS = {"conversations": 3, "evaluations": 3, "appeals": 1, "checklist_versions": 1}
MUTATED_COUNTS = {"conversations": 4, "evaluations": 4, "appeals": 0, "checklist_versions": 2}
ALINA = quote("Алина")  # X-CQA-User is URL-encoded UTF-8 (docs/ARCH_CONTRACTS.md §1)


# ----------------------------------------------------------------------------- fixtures and helpers


def _engine_for(path: Path):
    """An engine configured like cqa.db for a file database (WAL, foreign keys, busy timeout)."""
    eng = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return eng


def _seed(db: Session) -> None:
    checklist = Checklist(name="Freedom QA", description="тестовый чек-лист")
    db.add(checklist)
    db.flush()
    version = ChecklistVersion(checklist_id=checklist.id, version_no=1, is_active=True, change_note="v1")
    db.add(version)
    db.flush()
    crit = Criterion(checklist_version_id=version.id, code="C1", order=1, section="S1", title="Контакт",
                     instruction="Оператор поздоровался?", answer_type="binary",
                     options=[{"value": "yes", "points": 1}, {"value": "no", "points": 0}], max_points=1, weight=10)
    db.add(crit)
    db.flush()
    for i in range(1, 4):
        cid = f"p1-ru-00{i}"
        db.add(Conversation(id=cid, channel="agent chat", language="RU", operator_name="Алина", turn_count=1))
        db.flush()
        db.add(Turn(conversation_id=cid, idx=0, timestamp_raw="00:01", speaker="operator", text="Здравствуйте!", segment="operator"))
        ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed",
                        total_score=80.0 + i, final_total_score=80.0 + i)
        db.add(ev)
        db.flush()
        ec = EvaluationCriterion(evaluation_id=ev.id, criterion_id=crit.id, code="C1", answer="yes", score=1, max_points=1, weight=10)
        db.add(ec)
        db.flush()
        if i == 1:
            db.add(Appeal(evaluation_criterion_id=ec.id, comment="Не согласна с оценкой", author_role="operator", author_name="Алина"))
    db.commit()


def _mutate(make_session) -> None:
    """What a jury member could do during hands-on use: add a dialog and a version, delete an appeal, change a score."""
    with make_session() as db:
        version = db.execute(select(ChecklistVersion)).scalars().first()
        db.add(Conversation(id="jury-001", channel="agent chat", language="EN", turn_count=0))
        db.flush()
        db.add(Evaluation(conversation_id="jury-001", checklist_version_id=version.id, status="completed", total_score=10.0))
        db.add(ChecklistVersion(checklist_id=version.checklist_id, version_no=2, change_note="jury edit"))
        for appeal in db.execute(select(Appeal)).scalars():
            db.delete(appeal)
        ev = db.execute(select(Evaluation).where(Evaluation.conversation_id == "p1-ru-001")).scalars().one()
        ev.final_total_score = 0.0
        db.commit()


def _orm_counts(make_session) -> dict[str, int]:
    with make_session() as db:
        return {
            "conversations": db.scalar(select(func.count()).select_from(Conversation)),
            "evaluations": db.scalar(select(func.count()).select_from(Evaluation)),
            "appeals": db.scalar(select(func.count()).select_from(Appeal)),
            "checklist_versions": db.scalar(select(func.count()).select_from(ChecklistVersion)),
        }


def _file_counts(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as conn:
        return snap._counts(conn)


def _score_of(make_session, cid: str) -> float | None:
    with make_session() as db:
        return db.execute(select(Evaluation.final_total_score).where(Evaluation.conversation_id == cid)).scalars().first()


@pytest.fixture
def live(tmp_path):
    """A live database on a temp file, laid out like production: <data>/cqa.db and <data>/snapshots/."""
    data = tmp_path / "data"
    data.mkdir()
    path = data / "cqa.db"
    assert path.resolve() != REAL_DB and REAL_DB.parent not in path.resolve().parents
    eng = _engine_for(path)
    Base.metadata.create_all(eng)
    make_session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    with make_session() as db:
        _seed(db)
    yield SimpleNamespace(path=path, snapshots=data / "snapshots", engine=eng, Session=make_session)
    eng.dispose()


@pytest.fixture
def clock(monkeypatch):
    """Deterministic, strictly increasing snap.utc_now (61 s per call) for names and ordering."""
    state = {"t": datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)}

    def now() -> datetime:
        state["t"] += timedelta(seconds=61)
        return state["t"]

    monkeypatch.setattr(snap, "utc_now", now)
    return state


class ActorFromHeaders:
    """Stand-in for cqa.security.middleware (pure ASGI): puts the actor into request.state like the real one."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            if "x-cqa-role" in headers:
                user = headers.get("x-cqa-user")
                scope.setdefault("state", {})["actor"] = {"role": headers["x-cqa-role"], "name": unquote(user) if user else None,
                                                          "request_id": "req-test"}
        await self.app(scope, receive, send)


@pytest.fixture
def api(live, monkeypatch):
    """The admin router on a bare app; settings and the app engine point at the temp live database."""
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{live.path.as_posix()}")
    monkeypatch.setattr(settings, "data_dir", live.path.parent)
    monkeypatch.setattr(cqa_db, "engine", live.engine)
    monkeypatch.setattr(cqa_db, "SessionLocal", live.Session)
    monkeypatch.delenv(admin_router.ADMIN_TOKEN_ENV, raising=False)
    calls = SimpleNamespace(dispose=0, audits=[], cancelled=[])
    real_dispose = admin_router._dispose_engine

    def spy_dispose() -> None:
        calls.dispose += 1
        real_dispose()

    monkeypatch.setattr(admin_router, "_dispose_engine", spy_dispose)
    monkeypatch.setattr(admin_router, "_audit", lambda action, entity_id, summary, actor: calls.audits.append((action, entity_id, actor)))
    monkeypatch.setattr(admin_router, "_active_jobs", list)
    # the router resolves exactly the temp files, never the demo database
    assert admin_router._live_db_path() == live.path.resolve() != REAL_DB
    assert admin_router._snapshots_dir() == live.snapshots
    assert admin_router._engine_serves_live_db()
    app = FastAPI()
    app.add_middleware(ActorFromHeaders)
    app.include_router(admin_router.router)

    @app.exception_handler(HTTPException)
    async def _envelope(request, exc):  # same as cqa.main
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    return SimpleNamespace(client=TestClient(app), live=live, calls=calls)


def _fake_audit_module(monkeypatch, record) -> types.ModuleType:
    """Install a stand-in for cqa.audit (both in sys.modules and as the package attribute the real one may have set)."""
    fake = types.ModuleType("cqa.audit")
    fake.record = record
    monkeypatch.setitem(sys.modules, "cqa.audit", fake)
    monkeypatch.setattr(cqa, "audit", fake, raising=False)
    return fake


# ----------------------------------------------------------------------------- names and URLs


@pytest.mark.parametrize("name", ["a", "0", "baseline", "pre-control_1", "snap-20261107-180501", "a" * 40, "9-_"])
def test_valid_names(name):
    assert snap.validate_name(name) == name


@pytest.mark.parametrize("name", ["", "A", "Baseline", "-x", "_x", "a" * 41, "a/b", "../etc", "a.b", "a b", "имя",
                                  "abc\n", "abc\x00", None, 5])
def test_invalid_names(name):
    with pytest.raises(snap.InvalidName) as exc:
        snap.validate_name(name)
    assert exc.value.status == 422 and exc.value.extra["field"] == "name"


def test_default_name_is_utc_timestamp():
    almaty = datetime(2026, 11, 8, 4, 5, 1, tzinfo=timezone(timedelta(hours=5)))  # 23:05:01 UTC the day before
    assert snap.default_name(now=almaty) == "snap-20261107-230501"
    assert snap.default_name("pre-restore", now=almaty) == "pre-restore-20261107-230501"
    assert snap.validate_name(snap.default_name())


def test_resolve_sqlite_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert snap.resolve_sqlite_path("sqlite:////app/data/cqa.db") == Path("/app/data/cqa.db").resolve()
    assert snap.resolve_sqlite_path("sqlite:///rel/cqa.db") == (tmp_path / "rel" / "cqa.db").resolve()
    assert snap.resolve_sqlite_path(f"sqlite+pysqlite:///{tmp_path.as_posix()}/x.db") == (tmp_path / "x.db").resolve()
    assert snap.resolve_sqlite_path(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/y.db") == (tmp_path / "y.db").resolve()
    for url in ("sqlite://", "sqlite:///:memory:", "postgresql+psycopg://cqa:s3cr3t@db.internal:5432/cqa", "not a url"):
        with pytest.raises(snap.NotSupported) as exc:
            snap.resolve_sqlite_path(url)
        assert exc.value.status == 501 and "s3cr3t" not in exc.value.message


# ----------------------------------------------------------------------------- create


def test_create_snapshot_writes_standalone_file_and_metadata(live):
    actor = {"role": "admin", "name": "Алина", "request_id": "r1"}
    meta = snap.create_snapshot(live.path, live.snapshots, "baseline", note="Перед показом жюри", actor=actor)

    db_file, meta_file = live.snapshots / "baseline.db", live.snapshots / "baseline.json"
    assert db_file.is_file() and meta_file.is_file()
    assert meta["name"] == "baseline" and meta["kind"] == "manual" and meta["protected"] is True
    assert meta["note"] == "Перед показом жюри" and meta["actor"] == actor
    assert meta["counts"] == BASE_COUNTS == _file_counts(db_file)
    assert meta["integrity"] == "ok"
    assert meta["size_bytes"] == db_file.stat().st_size
    assert meta["sha256"] == hashlib.sha256(db_file.read_bytes()).hexdigest()
    assert datetime.fromisoformat(meta["created_at"]).utcoffset() == timedelta(0)
    stored = json.loads(meta_file.read_text(encoding="utf-8"))
    assert {"name", "created_at", "size_bytes", "note", "actor", "counts"} <= stored.keys()
    assert stored["counts"] == BASE_COUNTS and stored["actor"] == actor
    # a standalone rollback-journal file: header bytes 18/19 == 1 (2 would be WAL), nothing else next to it
    assert db_file.read_bytes()[18:20] == b"\x01\x01"
    assert sorted(p.name for p in live.snapshots.iterdir()) == ["baseline.db", "baseline.json"]
    # the snapshot is a full, usable copy of the data
    with closing(sqlite3.connect(db_file)) as conn:
        assert conn.execute("SELECT comment FROM appeals").fetchall() == [("Не согласна с оценкой",)]


def test_create_snapshot_includes_uncheckpointed_wal(live):
    writer = sqlite3.connect(live.path)  # stays open, so the WAL is not checkpointed away
    try:
        writer.execute("INSERT INTO datasets (group_id, source, imported_at, record_count) VALUES ('WAL', 'test', '2026-11-07 12:00:00.000000', 0)")
        writer.commit()
        assert live.path.with_name("cqa.db-wal").stat().st_size > 0
        snap.create_snapshot(live.path, live.snapshots, "walcheck")
    finally:
        writer.close()
    with closing(sqlite3.connect(live.snapshots / "walcheck.db")) as conn:
        assert conn.execute("SELECT group_id FROM datasets").fetchall() == [("WAL",)]


def test_default_names_do_not_collide_within_one_second(live, monkeypatch):
    fixed = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(snap, "utc_now", lambda: fixed)
    first = snap.create_snapshot(live.path, live.snapshots)
    second = snap.create_snapshot(live.path, live.snapshots)
    assert (first["name"], second["name"]) == ("snap-20261107-120000", "snap-20261107-120000-2")
    assert first["created_at"].startswith("2026-11-07T12:00:00")  # the name and created_at come from one instant


def test_existing_name_conflicts_unless_overwrite(live):
    snap.create_snapshot(live.path, live.snapshots, "pre-control")
    with pytest.raises(snap.SnapshotExists) as exc:
        snap.create_snapshot(live.path, live.snapshots, "pre-control")
    assert exc.value.status == 409
    _mutate(live.Session)
    meta = snap.create_snapshot(live.path, live.snapshots, "pre-control", overwrite=True)
    assert meta["counts"] == MUTATED_COUNTS == _file_counts(live.snapshots / "pre-control.db")
    assert not list(live.snapshots.glob(".*"))  # no temp files left behind


def test_overwriting_baseline_needs_force(live):
    snap.create_snapshot(live.path, live.snapshots, "baseline")
    _mutate(live.Session)
    with pytest.raises(snap.ProtectedSnapshot):
        snap.create_snapshot(live.path, live.snapshots, "baseline", overwrite=True)
    assert _file_counts(live.snapshots / "baseline.db") == BASE_COUNTS
    snap.create_snapshot(live.path, live.snapshots, "baseline", overwrite=True, force=True)
    assert _file_counts(live.snapshots / "baseline.db") == MUTATED_COUNTS


def test_create_never_creates_a_missing_live_database(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(snap.DatabaseMissing):
        snap.create_snapshot(missing, tmp_path / "snapshots", "x")
    assert not missing.exists()


def test_create_refuses_when_disk_is_almost_full(live, monkeypatch):
    monkeypatch.setattr(snap.shutil, "disk_usage", lambda _p: SimpleNamespace(total=10**9, used=10**9 - 4096, free=4096))
    with pytest.raises(snap.InsufficientDisk) as exc:
        snap.create_snapshot(live.path, live.snapshots, "big")
    assert exc.value.status == 507 and exc.value.extra["free_bytes"] == 4096
    assert list(live.snapshots.iterdir()) == []


def test_create_turns_filesystem_errors_into_snapshot_errors_and_cleans_up(live, monkeypatch):
    def full_disk(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(snap.os, "replace", full_disk)
    with pytest.raises(snap.SnapshotError) as exc:
        snap.create_snapshot(live.path, live.snapshots, "no-space")
    assert exc.value.status == 500 and exc.value.code == "snapshot_failed" and "No space left" in exc.value.message
    assert list(live.snapshots.iterdir()) == []  # neither the part file nor metadata is left behind


# ----------------------------------------------------------------------------- restore


def test_restore_reverts_data_in_place_and_keeps_open_handles(live, clock):
    snap.create_snapshot(live.path, live.snapshots, "baseline")
    held_raw = sqlite3.connect(live.path)  # a handle opened before the restore (like a worker's connection)
    held_session = live.Session()
    assert held_session.scalar(select(func.count()).select_from(Conversation)) == 3
    _mutate(live.Session)
    assert _orm_counts(live.Session) == MUTATED_COUNTS
    inode = os.stat(live.path).st_ino
    disposed: list[int] = []

    result = snap.restore_snapshot(live.path, live.snapshots, "baseline", lambda: (disposed.append(1), live.engine.dispose()),
                                   actor={"role": "admin", "name": "Алина"})

    assert len(disposed) == 2  # before and after the copy
    assert result["verified"] is True and result["integrity"] == "ok"
    assert result["counts"] == result["expected_counts"] == BASE_COUNTS
    assert os.stat(live.path).st_ino == inode  # copied INTO the file, never replaced
    assert _orm_counts(live.Session) == BASE_COUNTS
    assert _score_of(live.Session, "p1-ru-001") == 81.0
    with live.Session() as db:
        assert db.get(Conversation, "jury-001") is None
        assert [a.comment for a in db.execute(select(Appeal)).scalars()] == ["Не согласна с оценкой"]
    # handles opened before the restore stay valid and see the restored data
    assert held_raw.execute("SELECT COUNT(*) FROM conversations").fetchone() == (3,)
    held_raw.execute("INSERT INTO datasets (group_id, source, imported_at, record_count) VALUES ('after', 't', '2026-11-07 12:00:00.000000', 0)")
    held_raw.commit()
    held_raw.close()
    held_session.rollback()
    assert held_session.scalar(select(func.count()).select_from(Conversation)) == 3
    held_session.close()

    pre = result["pre_restore"]
    assert pre["name"].startswith("pre-restore-") and pre["kind"] == "pre-restore"
    assert pre["counts"] == MUTATED_COUNTS == _file_counts(live.snapshots / f"{pre['name']}.db")
    assert "baseline" in pre["note"] and pre["actor"] == {"role": "admin", "name": "Алина"}
    assert result["snapshot"]["name"] == "baseline" and result["pruned"] == []

    # undo: restoring the automatic snapshot brings the jury's changes back
    undo = snap.restore_snapshot(live.path, live.snapshots, pre["name"], live.engine.dispose)
    assert undo["verified"] and undo["counts"] == MUTATED_COUNTS
    assert _score_of(live.Session, "p1-ru-001") == 0.0


def test_restore_drops_tables_created_after_the_snapshot(live):
    snap.create_snapshot(live.path, live.snapshots, "baseline")
    with live.engine.begin() as conn:
        conn.execute(text("CREATE TABLE scratch (x INTEGER)"))
    snap.restore_snapshot(live.path, live.snapshots, "baseline", live.engine.dispose)
    assert "scratch" not in inspect(live.engine).get_table_names()


def test_restore_unknown_snapshot_changes_nothing(live):
    with pytest.raises(snap.SnapshotNotFound) as exc:
        snap.restore_snapshot(live.path, live.snapshots, "baseline", live.engine.dispose)
    assert exc.value.status == 404 and "POST /api/admin/snapshots" in exc.value.message
    assert not live.snapshots.exists() or list(live.snapshots.iterdir()) == []


@pytest.mark.parametrize("damage", ["garbage", "truncated"])
def test_restore_rejects_a_corrupt_snapshot_before_touching_anything(live, damage):
    meta = snap.create_snapshot(live.path, live.snapshots, "broken")
    path = live.snapshots / "broken.db"
    if damage == "garbage":
        path.write_bytes(b"definitely not a database " * 200)
    else:
        path.write_bytes(path.read_bytes()[: 2 * 4096])
    _mutate(live.Session)
    with pytest.raises(snap.SnapshotCorrupt) as exc:
        snap.restore_snapshot(live.path, live.snapshots, "broken", live.engine.dispose)
    assert exc.value.status == 409
    assert _orm_counts(live.Session) == MUTATED_COUNTS
    assert [m["name"] for m in snap.list_snapshots(live.snapshots)] == [meta["name"]]  # no pre-restore snapshot


def test_restore_gives_up_cleanly_when_the_database_stays_locked(live):
    snap.create_snapshot(live.path, live.snapshots, "baseline")
    _mutate(live.Session)
    blocker = sqlite3.connect(live.path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # holds the write lock like a stuck writer
    blocker.execute("INSERT INTO datasets (group_id, source, imported_at, record_count) VALUES ('X', 't', '2026-11-07 12:00:00.000000', 0)")
    try:
        with pytest.raises(snap.SnapshotBusy) as exc:
            snap.restore_snapshot(live.path, live.snapshots, "baseline", live.engine.dispose, wait_s=0.3)
        assert exc.value.status == 409 and exc.value.extra["pre_restore"].startswith("pre-restore-")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert _orm_counts(live.Session) == MUTATED_COUNTS  # nothing half-copied
    assert snap.restore_snapshot(live.path, live.snapshots, "baseline", live.engine.dispose)["verified"]


def test_restore_refuses_a_snapshot_with_another_page_size(live):
    live.snapshots.mkdir()
    with closing(sqlite3.connect(live.snapshots / "big.db")) as conn:
        conn.execute("PRAGMA page_size=8192")
        conn.execute("CREATE TABLE conversations (id TEXT)")
        conn.commit()
    with pytest.raises(snap.PageSizeMismatch):
        snap.restore_snapshot(live.path, live.snapshots, "big", live.engine.dispose)
    assert _orm_counts(live.Session) == BASE_COUNTS
    assert [m["name"] for m in snap.list_snapshots(live.snapshots)] == ["big"]


def test_old_pre_restore_snapshots_are_pruned(live, clock):
    snap.create_snapshot(live.path, live.snapshots, "baseline")
    snap.create_snapshot(live.path, live.snapshots, "keep-me")
    created, pruned = [], []
    for _ in range(4):
        result = snap.restore_snapshot(live.path, live.snapshots, "baseline", None, keep_pre_restore=2)
        created.append(result["pre_restore"]["name"])
        pruned += result["pruned"]
    assert pruned == created[:2]
    names = {m["name"] for m in snap.list_snapshots(live.snapshots)}
    assert names == {"baseline", "keep-me", *created[2:]}
    assert not any(p.name.startswith(created[0]) for p in live.snapshots.iterdir())


# ----------------------------------------------------------------------------- list / get / delete


def test_list_newest_first_and_tolerates_foreign_files(live, clock):
    for name in ("first", "second", "third"):
        snap.create_snapshot(live.path, live.snapshots, name)
    (live.snapshots / "second.json").unlink()  # metadata lost
    (live.snapshots / "third.json").write_text("{not json", encoding="utf-8")
    (live.snapshots / "notes.txt").write_text("x", encoding="utf-8")
    (live.snapshots / "Bad.db").write_bytes(b"x")
    (live.snapshots / ".tmp.db.part").write_bytes(b"x")
    (live.snapshots / "orphan.json").write_text("{}", encoding="utf-8")

    items = snap.list_snapshots(live.snapshots)
    by_name = {m["name"]: m for m in items}
    assert set(by_name) == {"first", "second", "third"}
    assert items[0]["name"] in {"second", "third"}  # file mtimes stand in for lost metadata
    assert by_name["first"]["kind"] == "manual" and by_name["first"]["protected"] is False
    assert by_name["second"]["metadata_missing"] is True and by_name["second"]["kind"] == "unknown"
    assert by_name["second"]["counts"] == BASE_COUNTS  # recounted from the file
    assert "metadata_error" in by_name["third"] and by_name["third"]["counts"] == BASE_COUNTS
    assert snap.list_snapshots(live.path.parent / "does-not-exist") == []


def test_list_order_follows_created_at(live, clock):
    for name in ("old", "mid", "new"):
        snap.create_snapshot(live.path, live.snapshots, name)
    assert [m["name"] for m in snap.list_snapshots(live.snapshots)] == ["new", "mid", "old"]


def test_get_and_delete(live):
    snap.create_snapshot(live.path, live.snapshots, "tmp-1")
    assert snap.get_snapshot(live.snapshots, "tmp-1")["counts"] == BASE_COUNTS
    deleted = snap.delete_snapshot(live.snapshots, "tmp-1")
    assert deleted["name"] == "tmp-1"
    assert list(live.snapshots.iterdir()) == []
    with pytest.raises(snap.SnapshotNotFound):
        snap.delete_snapshot(live.snapshots, "tmp-1")
    with pytest.raises(snap.SnapshotNotFound):
        snap.get_snapshot(live.snapshots, "tmp-1")
    with pytest.raises(snap.InvalidName):
        snap.delete_snapshot(live.snapshots, "../cqa")


def test_baseline_is_protected_from_deletion(live):
    snap.create_snapshot(live.path, live.snapshots, "baseline")
    with pytest.raises(snap.ProtectedSnapshot) as exc:
        snap.delete_snapshot(live.snapshots, "baseline")
    assert exc.value.status == 409 and (live.snapshots / "baseline.db").is_file()
    snap.delete_snapshot(live.snapshots, "baseline", force=True)
    assert not (live.snapshots / "baseline.db").exists() and not (live.snapshots / "baseline.json").exists()


def test_live_info(live, tmp_path):
    info = snap.live_info(live.path)
    assert info["counts"] == BASE_COUNTS and info["journal_mode"] == "wal" and info["size_bytes"] > 0
    assert snap.live_info(tmp_path / "missing.db") is None


# ----------------------------------------------------------------------------- CLI


def test_cli_create_list_restore_delete(live, capsys):
    common = ["--db", str(live.path), "--dir", str(live.snapshots)]
    assert snap.main([*common, "create", "--name", "cli-1", "--note", "из CLI"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["name"] == "cli-1" and created["note"] == "из CLI" and created["actor"]["role"] == "cli"

    _mutate(live.Session)
    assert snap.main([*common, "restore", "cli-1"]) == 2  # needs --yes
    assert "--yes" in capsys.readouterr().err
    assert _orm_counts(live.Session) == MUTATED_COUNTS
    assert snap.main([*common, "restore", "cli-1", "--yes"]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["verified"] is True and "schema_sync" not in restored  # explicit --db: no app schema sync
    assert _orm_counts(live.Session) == BASE_COUNTS

    assert snap.main([*common, "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {m["name"] for m in listed} == {"cli-1", restored["pre_restore"]["name"]}

    assert snap.main([*common, "delete", "nope"]) == 1
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "not_found"
    assert snap.main([*common, "delete", "cli-1"]) == 0


# ----------------------------------------------------------------------------- API


def test_router_is_registered_in_the_app():
    from fastapi.openapi.utils import get_openapi

    from cqa.main import app  # importing only builds the app: no lifespan, no request, no database access

    with warnings.catch_warnings():  # unrelated duplicate operation ids elsewhere in the app
        warnings.simplefilter("ignore")
        paths = get_openapi(title=app.title, version=app.version, routes=app.routes)["paths"]
    methods = {path: set(ops) for path, ops in paths.items() if path.startswith("/api/admin/")}
    assert methods == {
        "/api/admin/snapshots": {"get", "post"},
        "/api/admin/snapshots/{name}": {"get", "delete"},
        "/api/admin/snapshots/{name}/restore": {"post"},
        "/api/admin/snapshots/{name}/download": {"get"},
    }


def test_api_list_and_create(api):
    client = api.client
    empty = client.get("/api/admin/snapshots").json()
    assert empty["items"] == [] and empty["total"] == 0
    assert empty["live"]["counts"] == BASE_COUNTS and empty["protected"] == ["baseline"]
    assert empty["name_pattern"] == snap.NAME_PATTERN and empty["token_required"] is False
    assert empty["disk"]["free_bytes"] > 0

    r = client.post("/api/admin/snapshots")  # no body at all → default name
    assert r.status_code == 201, r.text
    auto = r.json()
    assert auto["name"].startswith("snap-") and auto["actor"] is None and auto["note"] is None

    r = client.post("/api/admin/snapshots", json={"name": " baseline ", "note": "  Перед показом  "},
                    headers={"X-CQA-Role": "admin", "X-CQA-User": ALINA})
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["name"] == "baseline" and meta["note"] == "Перед показом"
    assert meta["actor"] == {"role": "admin", "name": "Алина", "request_id": "req-test"}
    assert meta["counts"] == BASE_COUNTS and meta["protected"] is True
    assert meta["download_url"] == "/api/admin/snapshots/baseline/download"

    listing = client.get("/api/admin/snapshots").json()
    assert listing["total"] == 2 and {m["name"] for m in listing["items"]} == {"baseline", auto["name"]}
    assert all(m["download_url"].endswith("/download") for m in listing["items"])

    one = client.get("/api/admin/snapshots/baseline")
    assert one.status_code == 200 and one.json()["sha256"] == meta["sha256"]
    assert client.get("/api/admin/snapshots/nope").json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(("body", "code", "field"), [
    ({"name": "Bad Name"}, "invalid_name", "name"),
    ({"name": "a" * 41}, "invalid_name", "name"),
    ({"name": 5}, "validation_error", "name"),
    ({"note": "x" * 501}, "validation_error", "note"),
    ({"note": ["list"]}, "validation_error", "note"),
    ({"overwrite": "yes"}, "validation_error", "overwrite"),
    ([1, 2], "validation_error", "body"),
])
def test_api_create_validation_uses_the_error_envelope(api, body, code, field):
    r = api.client.post("/api/admin/snapshots", json=body)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == code and err["message"] and err["errors"][0]["field"] == field
    assert not api.live.snapshots.exists() or list(api.live.snapshots.iterdir()) == []


def test_api_rejects_malformed_json(api):
    r = api.client.post("/api/admin/snapshots", content=b"{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_json"


def test_api_duplicate_and_overwrite(api):
    client = api.client
    assert client.post("/api/admin/snapshots", json={"name": "pre-control"}).status_code == 201
    dup = client.post("/api/admin/snapshots", json={"name": "pre-control"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "snapshot_exists"
    _mutate(api.live.Session)
    again = client.post("/api/admin/snapshots", json={"name": "pre-control", "overwrite": True})
    assert again.status_code == 201 and again.json()["counts"] == MUTATED_COUNTS
    client.post("/api/admin/snapshots", json={"name": "baseline"})
    locked = client.post("/api/admin/snapshots", json={"name": "baseline", "overwrite": True})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "snapshot_protected"


def test_api_restore_flow(api):
    client, live = api.client, api.live
    assert client.post("/api/admin/snapshots", json={"name": "baseline"}).status_code == 201
    _mutate(live.Session)

    for body in ({"confirm": "wrong"}, {}, {"confirm": 1}, None):
        r = client.post("/api/admin/snapshots/baseline/restore", json=body)
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "confirm_mismatch" and r.json()["error"]["expected"] == "baseline"
    assert _orm_counts(live.Session) == MUTATED_COUNTS and api.calls.dispose == 0
    assert [m["name"] for m in snap.list_snapshots(live.snapshots)] == ["baseline"]  # nothing created by refusals

    r = client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline"},
                    headers={"X-CQA-Role": "admin", "X-CQA-User": ALINA})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["verified"] is True and res["counts"] == res["expected_counts"] == BASE_COUNTS
    assert res["schema_sync"] == "ok" and res["cancelled_jobs"] == []
    assert res["recovered_jobs"] == {"jobs": [], "evaluations": []}
    assert res["pre_restore"]["name"].startswith("pre-restore-") and res["pre_restore"]["counts"] == MUTATED_COUNTS
    assert res["pre_restore"]["actor"]["name"] == "Алина"
    assert res["pre_restore"]["download_url"].endswith("/download") and res["snapshot"]["name"] == "baseline"
    assert res["safety_snapshot"] == res["pre_restore"]["name"]  # flat copy captured by the audit route map
    assert "baseline" in res["message"] and res["pre_restore"]["name"] in res["message"]
    assert api.calls.dispose == 2
    assert _orm_counts(live.Session) == BASE_COUNTS and _score_of(live.Session, "p1-ru-001") == 81.0

    kinds = {m["name"]: m["kind"] for m in client.get("/api/admin/snapshots").json()["items"]}
    assert kinds == {"baseline": "manual", res["pre_restore"]["name"]: "pre-restore"}


def test_api_restore_unknown_or_invalid(api):
    r = api.client.post("/api/admin/snapshots/missing/restore", json={"confirm": "missing"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = api.client.post("/api/admin/snapshots/Bad/restore", json={"confirm": "Bad"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_name"
    assert api.calls.dispose == 0


def test_api_restore_waits_for_running_jobs_unless_forced(api, monkeypatch):
    client, live = api.client, api.live
    client.post("/api/admin/snapshots", json={"name": "baseline"})
    _mutate(live.Session)
    monkeypatch.setattr(admin_router, "_active_jobs", lambda: ["j_abc123"])
    monkeypatch.setattr(admin_router, "_cancel_jobs", lambda ids: api.calls.cancelled.extend(ids) or list(ids))

    r = client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "jobs_running" and r.json()["error"]["jobs"] == ["j_abc123"]
    assert _orm_counts(live.Session) == MUTATED_COUNTS and api.calls.cancelled == []

    bad = client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline", "force": "yes"})
    assert bad.status_code == 422 and bad.json()["error"]["errors"][0]["field"] == "force"

    r = client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline", "force": True})
    assert r.status_code == 200, r.text
    assert r.json()["cancelled_jobs"] == ["j_abc123"] == api.calls.cancelled
    assert _orm_counts(live.Session) == BASE_COUNTS


def test_api_restore_brings_an_older_snapshot_up_to_the_current_schema(api):
    live = api.live
    with live.engine.begin() as conn:  # simulate a snapshot taken by an older build
        conn.execute(text("DROP TABLE gold_labels"))
        conn.execute(text("ALTER TABLE appeals DROP COLUMN author_name"))
    assert api.client.post("/api/admin/snapshots", json={"name": "old-build"}).status_code == 201
    assert admin_router._sync_schema() == "ok"  # the current build's start-up adds them back
    assert "gold_labels" in inspect(live.engine).get_table_names()

    r = api.client.post("/api/admin/snapshots/old-build/restore", json={"confirm": "old-build"})
    assert r.status_code == 200, r.text
    assert r.json()["schema_sync"] == "ok" and r.json()["verified"] is True
    insp = inspect(live.engine)
    assert "gold_labels" in insp.get_table_names()
    assert "author_name" in {c["name"] for c in insp.get_columns("appeals")}
    with live.Session() as db:  # the ORM works against the restored data
        assert db.execute(select(Appeal)).scalars().one().author_name is None


def test_api_restore_closes_jobs_the_snapshot_captured_in_flight(api, monkeypatch):
    from cqa.jobs import runner

    if "session_factory" not in pyinspect.signature(runner.recover_stale_jobs).parameters:
        pytest.skip("cqa.jobs.runner.recover_stale_jobs without session_factory")
    live = api.live
    with live.Session() as db:  # a baseline taken while an evaluation job was running
        version_id = db.execute(select(ChecklistVersion.id)).scalars().first()
        db.add(Job(id="j_stale01", status="running", total=1))
        ev = Evaluation(conversation_id="p1-ru-002", checklist_version_id=version_id, status="running", job_id="j_stale01")
        db.add(ev)
        db.commit()
        stale_eval_id = ev.id
    monkeypatch.setattr(admin_router, "_active_jobs", lambda: ["j_stale01"])
    created = api.client.post("/api/admin/snapshots", json={"name": "mid-run"})
    assert created.status_code == 201 and "j_stale01" in created.json()["warnings"][0]
    monkeypatch.setattr(admin_router, "_active_jobs", list)  # the job has finished since
    with live.Session() as db:
        db.get(Job, "j_stale01").status = "completed"
        db.get(Evaluation, stale_eval_id).status = "completed"
        db.commit()

    r = api.client.post("/api/admin/snapshots/mid-run/restore", json={"confirm": "mid-run"})
    assert r.status_code == 200, r.text
    assert r.json()["recovered_jobs"] == {"jobs": ["j_stale01"], "evaluations": [stale_eval_id]}
    with live.Session() as db:
        assert db.get(Job, "j_stale01").status == "interrupted"
        assert db.get(Evaluation, stale_eval_id).status == "failed"


def test_recovery_hook_needs_a_session_factory_bound_to_the_live_engine(live, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{live.path.as_posix()}")
    monkeypatch.setattr(cqa_db, "engine", live.engine)  # engine matches, but SessionLocal is still the app's own
    assert admin_router._engine_serves_live_db() is True
    assert admin_router._recover_stale_jobs() == "skipped"


def test_api_download(api, tmp_path):
    client = api.client
    meta = client.post("/api/admin/snapshots", json={"name": "baseline"}).json()
    r = client.get("/api/admin/snapshots/baseline/download", headers={"X-CQA-Role": "admin", "X-CQA-User": ALINA})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.sqlite3"
    assert r.headers["content-disposition"] == 'attachment; filename="cqa-baseline.db"'
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-snapshot-sha256"] == meta["sha256"] == hashlib.sha256(r.content).hexdigest()
    assert r.content == (api.live.snapshots / "baseline.db").read_bytes()
    downloaded = tmp_path / "downloaded.db"
    downloaded.write_bytes(r.content)
    assert _file_counts(downloaded) == BASE_COUNTS
    assert api.calls.audits == [("snapshot.download", "baseline", {"role": "admin", "name": "Алина", "request_id": "req-test"})]

    assert client.get("/api/admin/snapshots/nope/download").status_code == 404
    assert client.get("/api/admin/snapshots/NOPE/download").status_code == 422
    assert len(api.calls.audits) == 1


def test_api_download_needs_the_admin_permission(api):
    api.client.post("/api/admin/snapshots", json={"name": "baseline"})
    for role in ("visitor", "operator", "analyst"):
        r = api.client.get("/api/admin/snapshots/baseline/download", headers={"X-CQA-Role": role})
        assert r.status_code == 403, (role, r.text)
        err = r.json()["error"]
        assert err["code"] == "forbidden" and err["role"] == role and err["permission"] == "admin" and err["message"]
    assert api.calls.audits == []
    assert api.client.get("/api/admin/snapshots/baseline/download", headers={"X-CQA-Role": "admin"}).status_code == 200
    assert api.client.get("/api/admin/snapshots/baseline/download").status_code == 200  # no headers: demo, every permission
    assert api.client.get("/api/admin/snapshots", headers={"X-CQA-Role": "visitor"}).status_code == 200  # metadata is a read


def test_api_delete(api):
    client = api.client
    client.post("/api/admin/snapshots", json={"name": "tmp-1"})
    client.post("/api/admin/snapshots", json={"name": "baseline"})
    r = client.delete("/api/admin/snapshots/tmp-1")
    assert r.status_code == 200 and r.json()["deleted"] == "tmp-1" and r.json()["snapshot"]["counts"] == BASE_COUNTS
    assert client.delete("/api/admin/snapshots/tmp-1").status_code == 404
    locked = client.delete("/api/admin/snapshots/baseline")
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "snapshot_protected"
    assert client.delete("/api/admin/snapshots/baseline?force=false").status_code == 409
    assert client.delete("/api/admin/snapshots/baseline?force=true").status_code == 200
    assert client.get("/api/admin/snapshots").json()["items"] == []


@pytest.mark.parametrize("url", ["postgresql+psycopg://cqa:s3cr3t@db.internal:5432/cqa", "sqlite://"])
def test_api_not_supported_without_a_sqlite_file(api, monkeypatch, url):
    monkeypatch.setattr(settings, "database_url", url)
    client = api.client
    responses = [
        client.get("/api/admin/snapshots"),
        client.post("/api/admin/snapshots", json={"name": "x"}),
        client.get("/api/admin/snapshots/x"),
        client.post("/api/admin/snapshots/x/restore", json={"confirm": "x"}),
        client.get("/api/admin/snapshots/x/download"),
        client.delete("/api/admin/snapshots/x"),
    ]
    for r in responses:
        assert r.status_code == 501, r.text
        assert r.json()["error"]["code"] == "not_supported" and r.json()["error"]["message"]
        assert "s3cr3t" not in r.text
    assert api.calls.dispose == 0


def test_api_optional_admin_token(api, monkeypatch):
    monkeypatch.setenv(admin_router.ADMIN_TOKEN_ENV, "letmein")
    client = api.client
    good = {admin_router.ADMIN_TOKEN_HEADER: "letmein"}
    assert client.get("/api/admin/snapshots").json()["token_required"] is True  # listing stays readable
    for headers in ({}, {admin_router.ADMIN_TOKEN_HEADER: "wrong"}):
        r = client.post("/api/admin/snapshots", json={"name": "baseline"}, headers=headers)
        assert r.status_code == 403 and r.json()["error"]["code"] == "admin_token_required"
    assert client.post("/api/admin/snapshots", json={"name": "baseline"}, headers=good).status_code == 201
    assert client.get("/api/admin/snapshots/baseline/download").status_code == 403
    assert client.get("/api/admin/snapshots/baseline/download", headers=good).status_code == 200
    assert client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline"}).status_code == 403
    assert client.post("/api/admin/snapshots/baseline/restore", json={"confirm": "baseline"}, headers=good).status_code == 200
    assert client.delete("/api/admin/snapshots/baseline?force=true").status_code == 403
    assert client.delete("/api/admin/snapshots/baseline?force=true", headers=good).status_code == 200


# ----------------------------------------------------------------------------- hooks


def test_active_jobs_lists_live_worker_threads(monkeypatch):
    from cqa.jobs import runner

    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, name="j_test_alive", daemon=True)
    monkeypatch.setitem(runner._registry, "j_test_alive", runner.JobState("j_test_alive"))
    monkeypatch.setitem(runner._registry, "j_test_finished", runner.JobState("j_test_finished"))
    worker.start()
    try:
        assert admin_router._active_jobs() == ["j_test_alive"]
        assert admin_router._cancel_jobs(["j_test_alive"]) == ["j_test_alive"]
        assert runner._registry["j_test_alive"].cancel.is_set()
    finally:
        stop.set()
        worker.join(5)
    assert admin_router._active_jobs() == []


def test_hooks_refuse_an_app_engine_that_is_not_the_live_database(live, tmp_path, monkeypatch):
    other = create_engine(f"sqlite:///{(tmp_path / 'other.db').as_posix()}")
    monkeypatch.setattr(cqa_db, "engine", other)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{live.path.as_posix()}")
    recorded: list[dict] = []
    _fake_audit_module(monkeypatch, lambda db, **kw: recorded.append(kw))

    assert admin_router._engine_serves_live_db() is False
    assert admin_router._sync_schema() == "skipped"
    assert admin_router._recover_stale_jobs() == "skipped"
    admin_router._audit("snapshot.download", "baseline", "test", None)
    assert recorded == []
    assert not (tmp_path / "other.db").exists()  # the mismatched engine was never even connected
    other.dispose()


def test_audit_hook_records_through_cqa_audit_when_available(live, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{live.path.as_posix()}")
    monkeypatch.setattr(cqa_db, "engine", live.engine)
    monkeypatch.setattr(cqa_db, "SessionLocal", live.Session)
    recorded: list[tuple[Session, dict]] = []
    fake_audit = _fake_audit_module(monkeypatch, lambda db, **kw: recorded.append((db, kw)))

    admin_router._audit("snapshot.download", "baseline", "Скачан снимок", {"role": "admin", "name": None})
    assert len(recorded) == 1
    db, kw = recorded[0]
    assert isinstance(db, Session)
    assert kw == {"action": "snapshot.download", "entity_type": "snapshot", "entity_id": "baseline", "summary": "Скачан снимок",
                  "details": {}, "actor": {"role": "admin", "name": None}}

    def broken(db, **kw):
        raise RuntimeError("audit down")

    fake_audit.record = broken
    admin_router._audit("snapshot.download", "baseline", "x", None)  # must not raise


def test_audit_hook_writes_a_real_audit_event_into_the_live_database(live, monkeypatch):
    pytest.importorskip("cqa.audit")  # package roles; skipped on checkouts without it
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{live.path.as_posix()}")
    monkeypatch.setattr(cqa_db, "engine", live.engine)
    monkeypatch.setattr(cqa_db, "SessionLocal", live.Session)
    admin_router._audit("snapshot.download", "baseline", "Скачан снимок базы «baseline»", {"role": "admin", "name": "Алина"})
    with live.Session() as db:  # the temp database, never the demo one
        events = db.execute(select(AuditEvent).where(AuditEvent.action == "snapshot.download")).scalars().all()
    assert [(e.entity_type, e.entity_id, e.actor_role, e.actor_name) for e in events] == [("snapshot", "baseline", "admin", "Алина")]
