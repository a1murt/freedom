"""SQLite snapshots of the live database (package "admin", see docs/arch/admin.md).

Why: the hosted jury instance must be resettable after hands-on use, and the team backs the database up before the
control run (docs/ORGANIZER_UPDATES.md §3.5). Every function takes explicit file paths, so the same code serves the API
router (cqa/routers/admin.py), the CLI (``python -m cqa.admin.snapshots``) and tests on temporary files.

Layout: ``<snapshots_dir>/<name>.db`` (a standalone SQLite file: rollback journal, no -wal/-shm next to it) and
``<snapshots_dir>/<name>.json`` (metadata: name, created_at, size_bytes, note, actor, counts, kind, integrity, sha256, ...).

- create:  sqlite3 online backup API from the live file (one consistent read; WAL writers are not blocked) into
           ``.<name>.db.part`` -> journal_mode=DELETE -> PRAGMA quick_check -> counts -> atomic rename to ``<name>.db``.
- restore: check the snapshot (header, quick_check, page size) -> automatic ``pre-restore-<UTC timestamp>`` snapshot of
           the current state -> dispose the SQLAlchemy pool -> copy the snapshot INTO the live file with the backup API
           (a single write transaction; the file is never replaced, so its inode and every open handle stay valid) ->
           WAL checkpoint -> dispose again -> read the counts back from the live file and compare them with the snapshot.
- delete:  refuses the protected ``baseline`` unless ``force``.

SQLite only: any other backend raises NotSupported. Operations in one process are serialized by a lock.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("cqa.admin.snapshots")

NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")  # always used with fullmatch: no "$ before a trailing newline" loophole
NAME_RULE = "1–40 символов: строчные латинские буквы, цифры, «-» и «_», первый символ — буква или цифра"
PROTECTED = frozenset({"baseline"})
COUNTED_TABLES = ("conversations", "evaluations", "appeals", "checklist_versions")
KIND_MANUAL = "manual"
KIND_PRE_RESTORE = "pre-restore"
DEFAULT_PREFIX = "snap"
PRE_RESTORE_PREFIX = "pre-restore"
PRE_RESTORE_KEEP = 5  # automatic pre-restore snapshots kept after a restore (oldest are pruned; manual ones never)
LOCK_WAIT_S = 15.0  # how long to wait for the live database's write lock (normal write transactions take milliseconds)
OP_WAIT_S = 120.0  # how long to wait for another snapshot operation in this process
DISK_MARGIN_BYTES = 32 * 1024 * 1024  # never fill the volume that also holds the live database
DISK_LOW_SNAPSHOTS = 4  # disk_status: low while fewer snapshots of the live database fit (every restore also makes one)
DISK_LOW_FLOOR_BYTES = 64 * 1024 * 1024  # disk_status: always low below this (a tiny database, or its size is unknown)
SQLITE_MAGIC = b"SQLite format 3\x00"

_op_lock = threading.RLock()


# ----------------------------------------------------------------------------- errors


class SnapshotError(Exception):
    """Base error. ``status`` and ``code`` map 1:1 to the API error envelope; ``extra`` adds fields to it."""

    status = 500
    code = "snapshot_failed"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class NotSupported(SnapshotError):
    status = 501
    code = "not_supported"


class InvalidName(SnapshotError):
    status = 422
    code = "invalid_name"


class SnapshotNotFound(SnapshotError):
    status = 404
    code = "not_found"


class SnapshotExists(SnapshotError):
    status = 409
    code = "snapshot_exists"


class ProtectedSnapshot(SnapshotError):
    status = 409
    code = "snapshot_protected"


class SnapshotCorrupt(SnapshotError):
    status = 409
    code = "snapshot_corrupt"


class PageSizeMismatch(SnapshotError):
    status = 409
    code = "page_size_mismatch"


class SnapshotBusy(SnapshotError):
    status = 409
    code = "busy"


class DatabaseMissing(SnapshotError):
    status = 503
    code = "database_missing"


class InsufficientDisk(SnapshotError):
    status = 507
    code = "insufficient_storage"


class TooManySnapshots(SnapshotError):
    status = 409
    code = "too_many_snapshots"


# ----------------------------------------------------------------------------- names and paths


def utc_now() -> datetime:
    return datetime.now(UTC)


def validate_name(name: Any) -> str:
    """Return ``name`` if it matches NAME_PATTERN (it becomes a file name, so nothing else is accepted)."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise InvalidName(f"Недопустимое имя снимка: {NAME_RULE}", field="name")
    return name


def default_name(prefix: str = DEFAULT_PREFIX, now: datetime | None = None) -> str:
    """``<prefix>-YYYYmmdd-HHMMSS`` in UTC, e.g. ``snap-20261107-180501``."""
    return f"{prefix}-{(now or utc_now()).astimezone(UTC):%Y%m%d-%H%M%S}"


def resolve_sqlite_path(database_url: str) -> Path:
    """The SQLite file behind a SQLAlchemy URL. NotSupported for any other backend or an in-memory database."""
    from sqlalchemy.engine import make_url

    try:
        url = make_url(database_url)
    except Exception as e:  # never echo the URL: it may carry credentials
        raise NotSupported("Не удалось разобрать DATABASE_URL; снимки доступны только для файловой базы SQLite") from e
    backend = url.get_backend_name()
    if backend != "sqlite":
        raise NotSupported(
            f"Снимки доступны только для SQLite, а сейчас используется {backend}. "
            "Для Postgres используйте резервные копии провайдера (pg_dump, бэкапы Railway или Supabase)",
            backend=backend,
        )
    database = url.database or ""
    if database in ("", ":memory:") or database.startswith("file:"):
        raise NotSupported("Снимки требуют файловую базу SQLite: база в памяти и URI-режим не поддерживаются")
    return Path(database).expanduser().resolve()


def _db_file(snapshots_dir: Path, name: str) -> Path:
    return snapshots_dir / f"{name}.db"


def _meta_file(snapshots_dir: Path, name: str) -> Path:
    return snapshots_dir / f"{name}.json"


def _part_file(snapshots_dir: Path, name: str) -> Path:
    return snapshots_dir / f".{name}.db.part"


def _with_sidecars(path: Path) -> list[Path]:
    return [path, *(path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm"))]


def _unlink_quietly(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:  # pragma: no cover - permissions; leftovers are harmless and ignored by the listing
            log.warning("could not remove %s", p, exc_info=True)


def _remove_snapshot_files(snapshots_dir: Path, name: str) -> None:
    meta = _meta_file(snapshots_dir, name)
    _unlink_quietly([*_with_sidecars(_db_file(snapshots_dir, name)), meta, meta.with_name(f".{meta.name}.part"),
                     *_with_sidecars(_part_file(snapshots_dir, name))])


def _unique_name(snapshots_dir: Path, base: str) -> str:
    if not _db_file(snapshots_dir, base).exists():
        return base
    for i in range(2, 1000):
        candidate = f"{base}-{i}"
        if not _db_file(snapshots_dir, candidate).exists():
            return candidate
    raise SnapshotExists(f"Не удалось подобрать свободное имя для снимка «{base}»", name=base)  # pragma: no cover


def _not_found(name: str) -> SnapshotNotFound:
    hint = ""
    if name == "baseline":
        hint = ' Создайте его после подготовки демо-данных: POST /api/admin/snapshots {"name": "baseline"}'
    return SnapshotNotFound(f"Снимок «{name}» не найден.{hint}", name=name)


# ----------------------------------------------------------------------------- sqlite helpers


def _connect(path: Path, mode: str = "rw", timeout: float = 5.0) -> sqlite3.Connection:
    """Open an EXISTING database file: URI mode=ro|rw never creates an empty database by accident."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=timeout)


def _backup(src: sqlite3.Connection, dst: sqlite3.Connection, wait_s: float) -> None:
    """Online backup in one step: one consistent read of ``src`` and one write transaction on ``dst``.

    CPython retries SQLITE_BUSY/LOCKED forever; the progress callback bounds that wait. With pages=-1 a busy step has
    written nothing, and an aborted backup rolls the destination back, so a timeout never leaves a half-copied file.
    """
    deadline = time.monotonic() + wait_s

    def progress(status: int, _remaining: int, _total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() >= deadline:
            raise SnapshotBusy(f"База данных занята другой записью дольше {wait_s:g} с; повторите через минуту")

    src.backup(dst, pages=-1, progress=progress, sleep=0.05)


def _pragma(conn: sqlite3.Connection, name: str) -> Any:
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def _quick_check(conn: sqlite3.Connection) -> str:
    rows = [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]
    return "ok" if rows == ["ok"] else ("; ".join(rows[:5]) or "unknown")


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {t: (int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) if t in tables else 0) for t in COUNTED_TABLES}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_bytes(db_path: Path) -> int:
    """Database file plus its WAL: an upper bound for the size of a snapshot of it."""
    total = 0
    for p in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} МБ"


def _ensure_disk(directory: Path, needed: int) -> None:
    usage = shutil.disk_usage(directory)
    required = needed + DISK_MARGIN_BYTES
    if usage.free < required:
        raise InsufficientDisk(
            f"Недостаточно места на диске: свободно {_mb(usage.free)}, нужно не меньше {_mb(required)}. Удалите старые снимки",
            free_bytes=usage.free, required_bytes=required,
        )


def disk_status(free_bytes: int, db_bytes: int | None) -> dict[str, Any]:
    """Is the data volume low? Judged by the live database, not a fixed number: the hosted volume is 500 MB in all.

    One rule with the guard above: a snapshot takes ``db_bytes`` (database file + WAL, see _db_bytes) and leaves
    DISK_MARGIN_BYTES free, so ``snapshots_fit`` is how many creates _ensure_disk still allows one after another.
    ``low`` while fewer than DISK_LOW_SNAPSHOTS fit or less than DISK_LOW_FLOOR_BYTES is free; an unknown size (None or
    0: Postgres, no file yet) leaves only the floor.
    """
    if not db_bytes or db_bytes < 0:
        return {"low": free_bytes < DISK_LOW_FLOOR_BYTES, "threshold_bytes": DISK_LOW_FLOOR_BYTES,
                "snapshot_bytes": None, "snapshots_fit": None}
    threshold = max(DISK_LOW_FLOOR_BYTES, DISK_LOW_SNAPSHOTS * db_bytes + DISK_MARGIN_BYTES)
    return {"low": free_bytes < threshold, "threshold_bytes": threshold, "snapshot_bytes": db_bytes,
            "snapshots_fit": max(0, (free_bytes - DISK_MARGIN_BYTES) // db_bytes)}


def _dispose(callback: Callable[[], Any] | None) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception:  # the backup API keeps open handles valid, so a failed dispose is not fatal
        log.warning("engine dispose callback failed", exc_info=True)


@contextmanager
def _exclusive(wait_s: float = OP_WAIT_S) -> Iterator[None]:
    if not _op_lock.acquire(timeout=wait_s):
        raise SnapshotBusy("Другая операция со снимками ещё выполняется; повторите через минуту")
    try:
        yield
    finally:
        _op_lock.release()


# ----------------------------------------------------------------------------- metadata


def _public(meta: dict[str, Any]) -> dict[str, Any]:
    return {**meta, "protected": meta["name"] in PROTECTED}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_meta(snapshots_dir: Path, name: str) -> dict[str, Any]:
    """Metadata of an existing snapshot: the .db file is the truth for size, the .json for everything else."""
    db_file = _db_file(snapshots_dir, name)
    st = db_file.stat()
    meta: dict[str, Any] = {
        "name": name, "created_at": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(), "size_bytes": st.st_size,
        "note": None, "actor": None, "counts": None, "kind": "unknown",
    }
    try:
        stored = json.loads(_meta_file(snapshots_dir, name).read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            meta.update(stored)
        else:
            meta["metadata_error"] = "метаданные снимка не являются JSON-объектом"
    except FileNotFoundError:
        meta["metadata_missing"] = True
    except (OSError, ValueError) as e:
        meta["metadata_error"] = f"метаданные снимка не читаются: {e}"
    meta["name"] = name
    meta["size_bytes"] = st.st_size
    if not isinstance(meta.get("counts"), dict):
        try:
            with closing(_connect(db_file, "ro")) as conn:
                meta["counts"] = _counts(conn)
        except sqlite3.Error as e:
            meta["counts"] = None
            meta["integrity"] = f"файл не читается: {e}"
    return _public(meta)


def _sort_key(meta: dict[str, Any]) -> tuple[datetime, str]:
    try:
        ts = datetime.fromisoformat(str(meta.get("created_at")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except ValueError:
        ts = datetime.min.replace(tzinfo=UTC)
    return ts, str(meta["name"])


# ----------------------------------------------------------------------------- public API


def list_snapshots(snapshots_dir: Path | str) -> list[dict[str, Any]]:
    """Metadata of every snapshot, newest first. Temporary, foreign and invalidly named files are ignored."""
    snapshots_dir = Path(snapshots_dir)
    if not snapshots_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in snapshots_dir.glob("*.db"):
        if not (path.is_file() and _NAME_RE.fullmatch(path.stem)):
            continue
        try:
            items.append(_read_meta(snapshots_dir, path.stem))
        except FileNotFoundError:  # deleted while listing
            continue
    items.sort(key=_sort_key, reverse=True)
    return items


def get_snapshot(snapshots_dir: Path | str, name: str) -> dict[str, Any]:
    validate_name(name)
    snapshots_dir = Path(snapshots_dir)
    if not _db_file(snapshots_dir, name).is_file():
        raise _not_found(name)
    return _read_meta(snapshots_dir, name)


def snapshot_file(snapshots_dir: Path | str, name: str) -> Path:
    """Path of an existing snapshot's database file (for downloads)."""
    validate_name(name)
    path = _db_file(Path(snapshots_dir), name)
    if not path.is_file():
        raise _not_found(name)
    return path


def live_info(db_path: Path | str) -> dict[str, Any] | None:
    """Size, journal mode and counts of the live database (None when the file does not exist yet)."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return None
    try:
        with closing(_connect(db_path, "rw")) as conn:
            return {"size_bytes": _db_bytes(db_path), "journal_mode": _pragma(conn, "journal_mode"), "counts": _counts(conn)}
    except sqlite3.Error as e:
        return {"size_bytes": _db_bytes(db_path), "journal_mode": None, "counts": None, "error": str(e)}


def create_snapshot(
    db_path: Path | str,
    snapshots_dir: Path | str,
    name: str | None = None,
    note: str | None = None,
    actor: dict[str, Any] | None = None,
    *,
    kind: str = KIND_MANUAL,
    overwrite: bool = False,
    force: bool = False,
    wait_s: float = LOCK_WAIT_S,
    max_manual: int | None = None,
) -> dict[str, Any]:
    """Snapshot the live database into ``<snapshots_dir>/<name>.db`` + ``<name>.json`` and return the metadata.

    ``name`` defaults to ``snap-<UTC timestamp>`` (a ``-2``, ``-3`` suffix avoids collisions within one second). An existing
    name raises SnapshotExists unless ``overwrite``; overwriting a protected snapshot also needs ``force``. With
    ``max_manual`` a new manual snapshot beyond that many raises TooManySnapshots (overwriting an existing name is allowed).
    """
    if name is not None:
        validate_name(name)
    with _exclusive():
        if max_manual and kind == KIND_MANUAL and not (name is not None and _db_file(Path(snapshots_dir), name).exists()):
            manual = [m["name"] for m in list_snapshots(snapshots_dir) if m.get("kind") in (KIND_MANUAL, "unknown")]
            if len(manual) >= max_manual:
                raise TooManySnapshots(f"Снимков уже {len(manual)} (не больше {max_manual}): удалите старые снимки или "
                                       "перезапишите существующий (overwrite=true)", limit=max_manual, count=len(manual))
        return _create(Path(db_path), Path(snapshots_dir), name=name, note=note, actor=actor, kind=kind,
                       overwrite=overwrite, force=force, wait_s=wait_s)


def _create(
    db_path: Path,
    snapshots_dir: Path,
    *,
    name: str | None,
    note: str | None,
    actor: dict[str, Any] | None,
    kind: str,
    overwrite: bool = False,
    force: bool = False,
    prefix: str = DEFAULT_PREFIX,
    wait_s: float = LOCK_WAIT_S,
) -> dict[str, Any]:
    if not db_path.is_file():
        raise DatabaseMissing(f"Файл базы данных {db_path.name} не найден")
    try:
        snapshots_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SnapshotError(f"Папка снимков недоступна: {e}") from e
    taken_at = utc_now()  # the backup reads one consistent state right after this instant
    if name is None:
        name = _unique_name(snapshots_dir, default_name(prefix, taken_at))
    db_file = _db_file(snapshots_dir, name)
    if db_file.exists():
        if not overwrite:
            raise SnapshotExists(f"Снимок «{name}» уже существует: выберите другое имя или передайте overwrite=true", name=name)
        if name in PROTECTED and not force:
            raise ProtectedSnapshot(
                f"Снимок «{name}» защищён: чтобы перезаписать его, передайте overwrite=true и force=true", name=name)
    _ensure_disk(snapshots_dir, _db_bytes(db_path))

    part = _part_file(snapshots_dir, name)
    _unlink_quietly(_with_sidecars(part))
    started = time.monotonic()
    try:
        with closing(_connect(db_path, "rw", timeout=min(wait_s, 5.0))) as src, closing(sqlite3.connect(part)) as dst:
            _backup(src, dst, wait_s)
            mode = _pragma(dst, "journal_mode=DELETE")  # the copy inherits WAL from the source; make it one standalone file
            if str(mode).lower() != "delete":  # pragma: no cover - a private connection always gets exclusive access
                log.warning("snapshot %s: journal mode stayed %s", name, mode)
            integrity = _quick_check(dst)
            counts = _counts(dst)
    except SnapshotError:
        _unlink_quietly(_with_sidecars(part))
        raise
    except sqlite3.Error as e:
        _unlink_quietly(_with_sidecars(part))
        raise SnapshotError(f"Не удалось создать снимок «{name}»: {e}", name=name) from e
    if integrity != "ok":
        _unlink_quietly(_with_sidecars(part))
        raise SnapshotCorrupt(f"Копия базы не прошла проверку целостности ({integrity}); снимок не сохранён", name=name)
    try:
        os.replace(part, db_file)
        meta: dict[str, Any] = {
            "name": name,
            "created_at": taken_at.isoformat(),
            "size_bytes": db_file.stat().st_size,
            "note": note,
            "actor": actor,
            "counts": counts,
            "kind": kind,
            "integrity": integrity,
            "sha256": _sha256(db_file),
            "sqlite_version": sqlite3.sqlite_version,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        _write_json(_meta_file(snapshots_dir, name), meta)
    except OSError as e:  # e.g. the volume filled up between the disk check and the write
        _unlink_quietly(_with_sidecars(part))
        raise SnapshotError(f"Не удалось сохранить снимок «{name}»: {e}", name=name) from e
    log.info("snapshot %s created (%s bytes, counts=%s)", name, meta["size_bytes"], counts)
    return _public(meta)


def _inspect(snap_file: Path, name: str) -> tuple[dict[str, int], int]:
    """Counts and page size of a snapshot after a header check and PRAGMA quick_check (SnapshotCorrupt otherwise)."""
    try:
        with snap_file.open("rb") as f:
            header = f.read(len(SQLITE_MAGIC))
    except OSError as e:
        raise SnapshotCorrupt(f"Снимок «{name}» не читается: {e}", name=name) from e
    if header != SQLITE_MAGIC:
        raise SnapshotCorrupt(f"Файл снимка «{name}» не является базой SQLite", name=name)
    try:
        with closing(_connect(snap_file, "ro")) as conn:
            integrity = _quick_check(conn)
            if integrity != "ok":
                raise SnapshotCorrupt(f"Снимок «{name}» повреждён ({integrity}); текущие данные не изменены", name=name)
            return _counts(conn), int(_pragma(conn, "page_size"))
    except sqlite3.Error as e:
        raise SnapshotCorrupt(f"Снимок «{name}» повреждён: {e}; текущие данные не изменены", name=name) from e


def restore_snapshot(
    db_path: Path | str,
    snapshots_dir: Path | str,
    name: str,
    dispose_engine_callback: Callable[[], Any] | None = None,
    *,
    actor: dict[str, Any] | None = None,
    keep_pre_restore: int | None = PRE_RESTORE_KEEP,
    wait_s: float = LOCK_WAIT_S,
) -> dict[str, Any]:
    """Replace the live database's content with snapshot ``name``, in place.

    Order: validate the snapshot -> automatic ``pre-restore-<UTC timestamp>`` snapshot of the current state -> dispose the
    pool (``dispose_engine_callback``, e.g. ``engine.dispose``) -> backup API from the snapshot INTO the live file ->
    WAL checkpoint -> dispose again -> counts read back from the live file. Returns the counts, the counts expected from
    the snapshot, ``verified`` (equal counts and quick_check ok), the pre-restore snapshot and the pruned automatic ones.
    """
    validate_name(name)
    db_path, snapshots_dir = Path(db_path), Path(snapshots_dir)
    with _exclusive():
        snap_file = _db_file(snapshots_dir, name)
        if not snap_file.is_file():
            raise _not_found(name)
        if not db_path.is_file():
            raise DatabaseMissing(f"Файл базы данных {db_path.name} не найден")
        expected, snap_page_size = _inspect(snap_file, name)
        try:
            with closing(_connect(db_path, "rw", timeout=min(wait_s, 5.0))) as conn:
                live_page_size = int(_pragma(conn, "page_size"))
                live_mode = str(_pragma(conn, "journal_mode")).lower()
        except sqlite3.Error as e:
            raise SnapshotError(f"Текущая база данных не открывается: {e}") from e
        if live_mode == "wal" and live_page_size != snap_page_size:  # SQLite cannot change the page size of a WAL database
            raise PageSizeMismatch(
                f"Размер страницы снимка «{name}» ({snap_page_size}) не совпадает с текущей базой ({live_page_size}); "
                "такой снимок нельзя восстановить на месте", name=name)
        _ensure_disk(snapshots_dir, _db_bytes(db_path) + snap_file.stat().st_size)  # pre-restore copy + WAL growth

        pre = _create(db_path, snapshots_dir, name=None, prefix=PRE_RESTORE_PREFIX, kind=KIND_PRE_RESTORE, actor=actor,
                      note=f"Автоматически перед восстановлением снимка «{name}»", wait_s=wait_s)
        started = time.monotonic()
        _dispose(dispose_engine_callback)
        try:
            with closing(_connect(snap_file, "ro")) as src, closing(_connect(db_path, "rw", timeout=min(wait_s, 5.0))) as dst:
                _backup(src, dst, wait_s)
                if live_mode == "wal":
                    try:  # fold the rewritten pages back into the main file and shrink the WAL; best effort
                        busy, wal_frames, done = dst.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                        if busy:
                            log.info("restore %s: WAL checkpoint partial (%s/%s frames)", name, done, wal_frames)
                    except sqlite3.Error:
                        log.warning("restore %s: WAL checkpoint failed", name, exc_info=True)
        except SnapshotError as e:
            e.extra.setdefault("pre_restore", pre["name"])
            raise
        except sqlite3.Error as e:
            raise SnapshotError(
                f"Не удалось восстановить снимок «{name}»: {e}. Текущие данные не изменены; их копия — снимок «{pre['name']}»",
                name=name, pre_restore=pre["name"]) from e
        finally:
            _dispose(dispose_engine_callback)
        duration_ms = int((time.monotonic() - started) * 1000)

        try:  # read back from the live file (the data is restored at this point; a failed check is reported, not raised)
            with closing(_connect(db_path, "rw")) as conn:
                counts = _counts(conn)
                integrity = _quick_check(conn)
        except sqlite3.Error as e:
            counts, integrity = {}, f"не удалось проверить: {e}"
        pruned = _prune_pre_restore(snapshots_dir, keep_pre_restore, keep_names={pre["name"], name})
        verified = counts == expected and integrity == "ok"
        if not verified:
            log.warning("restore %s: verification failed (counts=%s expected=%s integrity=%s)", name, counts, expected, integrity)
        log.info("snapshot %s restored in %s ms (pre-restore %s, pruned %s)", name, duration_ms, pre["name"], pruned)
        return {
            "name": name,
            "restored_at": utc_now().isoformat(),
            "snapshot": _read_meta(snapshots_dir, name),
            "pre_restore": pre,
            "counts": counts,
            "expected_counts": expected,
            "verified": verified,
            "integrity": integrity,
            "pruned": pruned,
            "duration_ms": duration_ms,
        }


def active_job_ids(db_path: Path | str) -> list[str]:
    """Jobs the live database lists as queued/running (read-only; [] when the table does not exist)."""
    try:
        with closing(_connect(Path(db_path), "ro")) as conn:
            return [str(r[0]) for r in conn.execute("SELECT id FROM jobs WHERE status IN ('queued', 'running') ORDER BY id")]
    except sqlite3.Error:
        return []


def _prune_pre_restore(snapshots_dir: Path, keep: int | None, keep_names: set[str]) -> list[str]:
    """Delete the oldest automatic pre-restore snapshots beyond ``keep`` (manual snapshots are never touched)."""
    if keep is None:
        return []
    autos = [m for m in list_snapshots(snapshots_dir) if m.get("kind") == KIND_PRE_RESTORE]  # newest first
    victims = [m["name"] for m in autos[max(1, keep):] if m["name"] not in keep_names]
    for victim in victims:
        _remove_snapshot_files(snapshots_dir, victim)
    return victims


def delete_snapshot(snapshots_dir: Path | str, name: str, force: bool = False) -> dict[str, Any]:
    """Delete snapshot ``name`` (database + metadata) and return its metadata. ``baseline`` needs ``force``."""
    validate_name(name)
    snapshots_dir = Path(snapshots_dir)
    with _exclusive():
        if not _db_file(snapshots_dir, name).is_file():
            raise _not_found(name)
        if name in PROTECTED and not force:
            raise ProtectedSnapshot(
                f"Снимок «{name}» защищён от удаления: передайте force=true, если его действительно нужно удалить", name=name)
        meta = _read_meta(snapshots_dir, name)
        _remove_snapshot_files(snapshots_dir, name)
    log.info("snapshot %s deleted", name)
    return meta


# ----------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """``python -m cqa.admin.snapshots list|create|restore|delete`` (from api/; --db/--dir default to the app settings)."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m cqa.admin.snapshots",
                                     description="Снимки базы SQLite: список, создание, восстановление, удаление")
    parser.add_argument("--db", type=Path, help="файл базы (по умолчанию из DATABASE_URL)")
    parser.add_argument("--dir", type=Path, help="папка снимков (по умолчанию <DATA_DIR>/snapshots)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="снимки, новые первыми")
    p_create = sub.add_parser("create", help="создать снимок")
    p_create.add_argument("--name")
    p_create.add_argument("--note")
    p_create.add_argument("--overwrite", action="store_true")
    p_create.add_argument("--force", action="store_true", help="разрешить перезапись защищённого снимка")
    p_restore = sub.add_parser("restore", help="восстановить снимок (текущее состояние сохраняется как pre-restore-…)")
    p_restore.add_argument("name")
    p_restore.add_argument("--yes", action="store_true", help="подтвердить замену текущих данных")
    p_restore.add_argument("--force", action="store_true", help="восстановить, даже если в базе есть задачи в статусе queued/running")
    p_delete = sub.add_parser("delete", help="удалить снимок")
    p_delete.add_argument("name")
    p_delete.add_argument("--force", action="store_true", help="разрешить удаление защищённого снимка")
    args = parser.parse_args(argv)

    configured = args.db is None
    try:
        if args.db is None or args.dir is None:
            from ..config import settings

            db_path = args.db or resolve_sqlite_path(settings.database_url)
            snapshots_dir = args.dir or Path(settings.data_dir) / "snapshots"
        else:
            db_path, snapshots_dir = args.db, args.dir
        actor = {"role": "cli", "name": os.environ.get("USER") or None}
        if args.cmd == "list":
            out: Any = list_snapshots(snapshots_dir)
        elif args.cmd == "create":
            from ..config import settings as app_settings

            out = create_snapshot(db_path, snapshots_dir, args.name, args.note, actor, overwrite=args.overwrite, force=args.force,
                                  max_manual=app_settings.snapshot_max_manual or None)
        elif args.cmd == "restore":
            if not args.yes:
                print(f"Восстановление заменит текущие данные содержимым снимка «{args.name}». Повторите с --yes.",
                      file=sys.stderr)
                return 2
            active = active_job_ids(db_path)
            if active and not args.force:
                print(f"В базе есть незавершённые задачи оценки ({', '.join(active)}): если API запущен, остановите его или "
                      "дождитесь окончания задач (их поздние записи попали бы в восстановленные данные). Повторите с --force, "
                      "если задачи остались от остановленного процесса.", file=sys.stderr)
                return 2
            out = restore_snapshot(db_path, snapshots_dir, args.name, None, actor=actor)
            if configured:  # same idempotent schema sync the API runs after a restore (cqa.db.init_db)
                from ..db import init_db

                init_db()
                out["schema_sync"] = "ok"
        else:
            out = delete_snapshot(snapshots_dir, args.name, force=args.force)
    except SnapshotError as e:
        error = {"error": {"code": e.code, "message": e.message, **e.extra}}
        print(json.dumps(error, ensure_ascii=False, default=str), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
