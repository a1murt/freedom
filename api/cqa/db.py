from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}


def _pool_args() -> dict[str, int]:
    """Room for every evaluation that may run at once (jobs x dialogs per job) plus request handlers. Evaluations do not
    hold a connection while the judges run, so this is headroom, not a requirement (MAX_CONCURRENT_JOBS=0 = no limit)."""
    url = settings.database_url
    if url.startswith("sqlite") and (":memory:" in url or url in ("sqlite://", "sqlite:///") or "mode=memory" in url):
        return {}  # in-memory SQLite uses a single-connection pool without these options
    jobs = int(settings.max_concurrent_jobs or 0) or 8
    per_job = max(int(settings.max_parallel_conversations or 1), 1)
    return {"pool_size": 5, "max_overflow": max(10, jobs * per_job + 10)}


engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True, **_pool_args())

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

# Bumped by an in-place snapshot restore (cqa.routers.admin) before the database content is replaced: an evaluation that
# started before the bump must not write its results into the restored database (cqa.judge.runner.evaluate_conversation).
_restore_generation = 0
_generation_lock = threading.Lock()


def restore_generation() -> int:
    return _restore_generation


def bump_restore_generation() -> int:
    global _restore_generation
    with _generation_lock:
        _restore_generation += 1
        return _restore_generation


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Additive column migrations for databases created by earlier versions (SQLite and Postgres both support ADD COLUMN).
ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("appeals", "author_role", "VARCHAR(32)"),
    ("appeals", "author_name", "VARCHAR(128)"),
    ("decisions", "reviewer_role", "VARCHAR(32)"),
    ("conversations", "agent_name", "VARCHAR(128)"),
]
# Indexes added after a table first shipped (create_all only indexes new tables). IF NOT EXISTS: SQLite and Postgres.
ADDITIVE_INDEXES: list[tuple[str, str, str]] = [
    ("audit_events", "ix_audit_events_entity", "entity_type, entity_id"),
]
# VARCHAR columns widened later: Postgres enforces lengths (SQLite does not), so existing PG tables are altered.
WIDENED_COLUMNS: list[tuple[str, str, int]] = [
    ("audit_events", "request_id", 64),
]


def _apply_additive_migrations() -> list[str]:
    from sqlalchemy import inspect, text

    applied: list[str] = []
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in ADDITIVE_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                applied.append(f"{table}.{column}")
        for table, name, columns in ADDITIVE_INDEXES:
            if table in tables and name not in {ix.get("name") for ix in insp.get_indexes(table)}:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
                applied.append(f"index {name}")
        if engine.dialect.name == "postgresql":
            for table, column, length in WIDENED_COLUMNS:
                if table not in tables:
                    continue
                col = next((c for c in insp.get_columns(table) if c["name"] == column), None)
                current = getattr(col["type"], "length", None) if col else None
                if current is not None and current < length:
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR({length})"))
                    applied.append(f"{table}.{column} VARCHAR({length})")
    return applied


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    _apply_additive_migrations()
