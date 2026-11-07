"""Admin API (package "admin", see docs/arch/admin.md): SQLite snapshots of the live database.

GET    /api/admin/snapshots                     snapshots (newest first) + live database counts and free disk space
POST   /api/admin/snapshots                     {name?, note?, overwrite?, force?} -> 201 snapshot metadata
GET    /api/admin/snapshots/{name}              one snapshot's metadata
POST   /api/admin/snapshots/{name}/restore      {confirm: "<name>", force?} -> "pre-restore-..." snapshot, restore in place
GET    /api/admin/snapshots/{name}/download     the snapshot file (application/vnd.sqlite3)
DELETE /api/admin/snapshots/{name}?force=true   delete ("baseline" only with force)

The "admin" permission on writes is enforced by the security middleware (cqa.security, package roles); the download is a
GET (the middleware lets every role read), so it checks "admin" itself with cqa.security.roles.require_permission (a full
database dump is an export, not a read). Handlers read request.state.actor for the metadata. Optional extra lock for the
public instance: when the env var CQA_ADMIN_TOKEN is set, create/restore/delete/download also require the header
X-CQA-Admin-Token with that value (listing stays readable). On the public hosted instance (settings.is_public_instance)
without a token, create/restore/delete/download are refused (403 admin_token_not_configured) unless
ALLOW_UNPROTECTED_SNAPSHOTS=1: a restore also rewinds every row, and an open admin endpoint must not be the default.

Restore (F1): the whole sequence runs under cqa.jobs.runner.start_lock (no job can start meanwhile): live job workers
-> 409 jobs_running, or with force=true they are cancelled and the restore waits until they have finished their
in-flight dialogs (409 jobs_still_running after RESTORE_JOB_WAIT_S) -> cqa.db.bump_restore_generation() -> pre-restore
snapshot -> copy -> schema sync -> recovery. A worker never writes into the restored database (its evaluation checks the
generation and its own row before writing). The daily budget is not rewound: spend lives in the spend ledger, a file
the snapshot does not contain. Manual snapshots are capped at settings.snapshot_max_manual (409 too_many_snapshots).
Non-SQLite databases -> 501 not_supported. Errors use the envelope {"error": {"code", "message", ...}}; JSON bodies are
parsed here (not by FastAPI) so even malformed bodies get that envelope.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ..admin import snapshots as snap
from ..config import settings

log = logging.getLogger("cqa.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

ADMIN_TOKEN_ENV = "CQA_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-CQA-Admin-Token"
NOTE_MAX_LEN = 500
PRE_RESTORE_KEEP = snap.PRE_RESTORE_KEEP
SNAPSHOT_MEDIA_TYPE = "application/vnd.sqlite3"
RESTORE_JOB_WAIT_S = 30.0  # force=true: how long a restore waits for cancelled workers to finish their in-flight dialogs


class _JobsRunning(Exception):
    def __init__(self, jobs: list[str]):
        super().__init__("jobs running")
        self.jobs = jobs


class _JobsStillRunning(Exception):
    def __init__(self, jobs: list[str], cancelled: list[str]):
        super().__init__("jobs still running")
        self.jobs = jobs
        self.cancelled = cancelled


def _admin_dependency() -> Any:
    """cqa.security.roles.require_permission("admin"): 403 `forbidden` for roles without it (no X-CQA-Role = demo = allowed)."""
    try:
        from ..security.roles import require_permission
    except Exception:  # package roles missing or broken: its middleware is then absent too and every request is "demo"
        log.warning("cqa.security.roles unavailable: snapshot downloads are not permission-checked", exc_info=True)
        return lambda: None
    return require_permission("admin")


_require_admin = _admin_dependency()
AdminActor = Annotated[Any, Depends(_require_admin)]


# ----------------------------------------------------------------------------- hooks (resolved at call time; tests patch them)


def _live_db_path() -> Path:
    """The live SQLite file behind settings.database_url (snap.NotSupported for Postgres or an in-memory database)."""
    return snap.resolve_sqlite_path(settings.database_url)


def _snapshots_dir() -> Path:
    return Path(settings.data_dir) / "snapshots"


def _engine_serves_live_db() -> bool:
    """True when the app engine (cqa.db.engine) points at the same file as settings.database_url."""
    from .. import db

    try:
        return snap.resolve_sqlite_path(db.engine.url.render_as_string(hide_password=False)) == _live_db_path()
    except snap.SnapshotError:
        return False


def _dispose_engine() -> None:
    """Close the pooled connections of the app engine (called before and after the in-place restore)."""
    from .. import db

    db.engine.dispose()


def _sync_schema() -> str:
    """After a restore: create the tables/columns an older snapshot lacks (cqa.db.init_db is idempotent, as at start-up)."""
    if not _engine_serves_live_db():
        return "skipped"
    from .. import db

    db.init_db()
    return "ok"


def _safe_sync_schema() -> str:
    try:
        return _sync_schema()
    except Exception as e:  # the data is already restored: report instead of failing the request
        log.exception("schema sync after restore failed")
        return f"failed: {e}"


def _active_jobs() -> list[str]:
    """Evaluation jobs whose worker thread is alive in this process (their writes would race a restore)."""
    try:
        from ..jobs import runner
    except ImportError:  # pragma: no cover - the runner always imports inside the app
        return []
    registry = getattr(runner, "_registry", None) or {}
    alive = {t.name for t in threading.enumerate() if t.is_alive()}
    return sorted(job_id for job_id in list(registry) if job_id in alive)


def _cancel_jobs(job_ids: list[str]) -> list[str]:
    """Ask running jobs to stop starting dialogs (dialogs already in flight finish; the restore waits for them)."""
    try:
        from ..jobs import runner
    except ImportError:  # pragma: no cover
        return []
    return [job_id for job_id in job_ids if runner.cancel_job(job_id)]


def _wait_for_jobs(job_ids: list[str], timeout: float) -> list[str]:
    """Wait until the workers of `job_ids` have exited; returns the ones still alive after `timeout` seconds."""
    try:
        from ..jobs import runner
    except ImportError:  # pragma: no cover
        return []
    deadline = time.monotonic() + max(timeout, 0.0)
    still: list[str] = []
    for job_id in job_ids:
        state = runner.get_state(job_id)
        if state is None:  # not a worker of this process
            continue
        if not state.finished.wait(max(deadline - time.monotonic(), 0.0)):
            still.append(job_id)
    return still


def _start_lock() -> Any:
    """cqa.jobs.runner.start_lock: held for the whole restore, so no evaluation job (or resume) starts meanwhile."""
    try:
        from ..jobs import runner
    except ImportError:  # pragma: no cover
        return threading.RLock()
    return runner.start_lock


def _bump_generation() -> None:
    from .. import db

    db.bump_restore_generation()


def _restore_locked(db_path: Path, sdir: Path, name: str, force: bool, actor: dict[str, Any] | None,
                    keep: int) -> tuple[dict[str, Any], str, Any, list[str]]:
    """Check the workers, cancel and wait for them (force), then snapshot, copy, sync the schema and recover jobs, all
    under the job start lock (runs in the thread pool)."""
    with _start_lock():
        running = _active_jobs()
        if running and not force:
            raise _JobsRunning(running)
        cancelled = _cancel_jobs(running) if running else []
        if running:
            still = _wait_for_jobs(running, RESTORE_JOB_WAIT_S)
            if still:
                raise _JobsStillRunning(still, cancelled)
        _bump_generation()
        result = snap.restore_snapshot(db_path, sdir, name, _dispose_engine, actor=actor, keep_pre_restore=keep)
        schema_sync = _safe_sync_schema()
        recovered = _safe_recover_stale_jobs()
    return result, schema_sync, recovered, cancelled


def _recover_stale_jobs() -> dict[str, list] | str:
    """After a restore: jobs/evaluations the snapshot captured in flight have no worker here -> interrupted / failed.

    Uses cqa.jobs.runner.recover_stale_jobs (idempotent, never raises, leaves jobs alive in this process alone) with the
    app session factory, and only when that factory is bound to the live database.
    """
    from .. import db

    if not _engine_serves_live_db() or getattr(db.SessionLocal, "kw", {}).get("bind") is not db.engine:
        return "skipped"
    try:
        from ..jobs import runner
    except ImportError:  # pragma: no cover
        return "skipped"
    recover = getattr(runner, "recover_stale_jobs", None)
    if recover is None:  # pragma: no cover - older runner
        return "skipped"
    return recover(session_factory=db.SessionLocal)


def _safe_recover_stale_jobs() -> dict[str, list] | str:
    try:
        return _recover_stale_jobs()
    except Exception as e:  # the data is already restored: report instead of failing the request
        log.exception("job recovery after restore failed")
        return f"failed: {e}"


def _audit(action: str, entity_id: str, summary: str, actor: dict[str, Any] | None) -> None:
    """Semantic audit event through cqa.audit.record (package roles) when that module exists; never fails the request.

    Only GET downloads use it: writes (snapshot.create / snapshot.restore / snapshot.delete) are recorded by the
    security middleware from its route map.
    """
    try:
        from .. import audit  # type: ignore[attr-defined]
    except ImportError:  # package roles not installed yet
        return
    except Exception:  # a broken optional module must not break downloads
        log.warning("cqa.audit failed to import", exc_info=True)
        return
    record = getattr(audit, "record", None)
    if record is None or not _engine_serves_live_db():
        return
    from .. import db

    session = db.SessionLocal()
    try:
        record(session, action=action, entity_type="snapshot", entity_id=entity_id, summary=summary, details={}, actor=actor)
        session.commit()
    except Exception:
        session.rollback()
        log.warning("audit %s %s failed", action, entity_id, exc_info=True)
    finally:
        session.close()


# ----------------------------------------------------------------------------- helpers


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}})


def _snapshot_error(e: snap.SnapshotError) -> JSONResponse:
    extra = dict(e.extra)
    field = extra.pop("field", None)
    if field:
        extra["errors"] = [{"field": field, "message": e.message}]
    return _error(e.status, e.code, e.message, **extra)


def _expected_token() -> str:
    """CQA_ADMIN_TOKEN from the environment, else from settings (values in the .env file reach settings only)."""
    return (os.environ.get(ADMIN_TOKEN_ENV, "") or getattr(settings, "cqa_admin_token", None) or "").strip()


def _pre_restore_keep() -> int:
    value = getattr(settings, "snapshot_keep_pre_restore", None)
    return int(value) if isinstance(value, int) and value > 0 else PRE_RESTORE_KEEP


def _unprotected_allowed() -> bool:
    """Snapshot writes without CQA_ADMIN_TOKEN: always off the hosted instance; on it only with ALLOW_UNPROTECTED_SNAPSHOTS."""
    return (not settings.is_public_instance()) or bool(getattr(settings, "allow_unprotected_snapshots", False))


def _token_error(request: Request) -> JSONResponse | None:
    expected = _expected_token()
    if not expected:
        if _unprotected_allowed():
            return None
        return _error(403, "admin_token_not_configured",
                      "На публичном сервере операции со снимками доступны только с токеном администратора: задайте переменную "
                      f"окружения {ADMIN_TOKEN_ENV} и передавайте её в заголовке {ADMIN_TOKEN_HEADER}")
    given = request.headers.get(ADMIN_TOKEN_HEADER, "").strip()
    if given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
        return None
    return _error(403, "admin_token_required",
                  f"Операция со снимками требует токен администратора в заголовке {ADMIN_TOKEN_HEADER}")


def _actor(request: Request) -> dict[str, Any] | None:
    """{role, name[, request_id]} from request.state.actor (set by the security middleware); None without it."""
    raw = getattr(request.state, "actor", None)
    if raw is None:
        return None

    def get(key: str) -> Any:
        return raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)

    actor: dict[str, Any] = {"role": get("role"), "name": get("name")}
    if get("request_id"):
        actor["request_id"] = get("request_id")
    return {k: (str(v) if v is not None else None) for k, v in actor.items()}


async def _json_object(request: Request) -> dict[str, Any] | JSONResponse:
    """The request body as a JSON object; an empty body is {}."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return _error(422, "invalid_json", "Тело запроса должно быть корректным JSON",
                      errors=[{"field": "body", "message": "некорректный JSON"}])
    if data is None:
        return {}
    if not isinstance(data, dict):
        return _error(422, "validation_error", "Тело запроса должно быть JSON-объектом",
                      errors=[{"field": "body", "message": "ожидается объект"}])
    return data


def _optional_str(body: dict[str, Any], field: str, errors: list[dict[str, str]], max_len: int | None = None) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append({"field": field, "message": "ожидается строка"})
        return None
    value = value.strip()
    if max_len is not None and len(value) > max_len:
        errors.append({"field": field, "message": f"не длиннее {max_len} символов"})
        return None
    return value or None


def _optional_bool(body: dict[str, Any], field: str, errors: list[dict[str, str]]) -> bool:
    value = body.get(field, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        errors.append({"field": field, "message": "ожидается true или false"})
        return False
    return value


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _with_links(meta: dict[str, Any]) -> dict[str, Any]:
    return {**meta, "download_url": f"/api/admin/snapshots/{meta['name']}/download"}


def _disk(path: Path) -> dict[str, int] | None:
    probe = path if path.exists() else path.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return None
    return {"free_bytes": usage.free, "total_bytes": usage.total}


# ----------------------------------------------------------------------------- endpoints


@router.get("/snapshots")
def list_snapshots():
    """Snapshots newest first, plus the live database's counts and the free space on the data volume."""
    try:
        db_path = _live_db_path()
    except snap.SnapshotError as e:
        return _snapshot_error(e)
    sdir = _snapshots_dir()
    items = [_with_links(m) for m in snap.list_snapshots(sdir)]
    return {
        "items": items,
        "total": len(items),
        "live": snap.live_info(db_path),
        "disk": _disk(sdir),
        "protected": sorted(snap.PROTECTED),
        "name_pattern": snap.NAME_PATTERN,
        "pre_restore_keep": _pre_restore_keep(),
        "token_required": bool(_expected_token()),
        "writes_allowed": bool(_expected_token()) or _unprotected_allowed(),
        "max_manual": int(getattr(settings, "snapshot_max_manual", 0) or 0) or None,
    }


@router.post("/snapshots", status_code=201)
async def create_snapshot(request: Request):
    """Snapshot the live database (sqlite3 online backup; readers and writers are not blocked)."""
    if (denied := _token_error(request)) is not None:
        return denied
    body = await _json_object(request)
    if isinstance(body, JSONResponse):
        return body
    errors: list[dict[str, str]] = []
    name = _optional_str(body, "name", errors)
    note = _optional_str(body, "note", errors, max_len=NOTE_MAX_LEN)
    overwrite = _optional_bool(body, "overwrite", errors)
    force = _optional_bool(body, "force", errors)
    if errors:
        return _error(422, "validation_error", "Некорректные параметры снимка", errors=errors)
    try:
        db_path = _live_db_path()
        running = _active_jobs()
        meta = await run_in_threadpool(snap.create_snapshot, db_path, _snapshots_dir(), name, note, _actor(request),
                                       overwrite=overwrite, force=force,
                                       max_manual=int(getattr(settings, "snapshot_max_manual", 0) or 0) or None)
    except snap.SnapshotError as e:
        return _snapshot_error(e)
    content = _with_links(meta)
    if running:
        warning = (f"Во время снимка шли задачи оценки ({', '.join(running)}): их незавершённые оценки попали в снимок, "
                   "после восстановления они будут помечены прерванными. Лучше снимать базу без задач")
        content["warnings"] = [warning]
    return JSONResponse(status_code=201, content=content)


@router.get("/snapshots/{name}")
def get_snapshot(name: str):
    try:
        _live_db_path()
        return _with_links(snap.get_snapshot(_snapshots_dir(), name))
    except snap.SnapshotError as e:
        return _snapshot_error(e)


@router.post("/snapshots/{name}/restore")
async def restore_snapshot(name: str, request: Request):
    """Restore snapshot ``name`` in place after an automatic pre-restore snapshot. Body: {"confirm": "<name>", "force": false}."""
    if (denied := _token_error(request)) is not None:
        return denied
    body = await _json_object(request)
    if isinstance(body, JSONResponse):
        return body
    confirm = body.get("confirm")
    if not isinstance(confirm, str) or confirm.strip() != name:
        return _error(400, "confirm_mismatch",
                      f"Восстановление заменит текущие данные. Для подтверждения передайте confirm = «{name}»", expected=name)
    errors: list[dict[str, str]] = []
    force = _optional_bool(body, "force", errors)
    if errors:
        return _error(422, "validation_error", "Некорректные параметры восстановления", errors=errors)
    try:
        db_path = _live_db_path()
        sdir = _snapshots_dir()
        snap.get_snapshot(sdir, name)  # invalid name / unknown snapshot are reported before anything is touched
    except snap.SnapshotError as e:
        return _snapshot_error(e)

    try:
        result, schema_sync, recovered_jobs, cancelled = await run_in_threadpool(
            _restore_locked, db_path, sdir, name, force, _actor(request), _pre_restore_keep())
    except _JobsRunning as e:
        return _error(409, "jobs_running",
                      f"Идут задачи оценки ({', '.join(e.jobs)}): дождитесь их завершения или отмените их. "
                      "С force=true задачи будут отменены, и снимок восстановится, когда они допишут начатые диалоги", jobs=e.jobs)
    except _JobsStillRunning as e:
        return _error(409, "jobs_still_running",
                      f"Задачи {', '.join(e.jobs)} отменены, но ещё дописывают начатые диалоги (ждали {RESTORE_JOB_WAIT_S:g} с). "
                      "Повторите восстановление через минуту: данные не изменены", jobs=e.jobs, cancelled_jobs=e.cancelled)
    except snap.SnapshotError as e:
        return _snapshot_error(e)
    counts = result["counts"]
    result.update({
        "snapshot": _with_links(result["snapshot"]),
        "pre_restore": _with_links(result["pre_restore"]),
        "safety_snapshot": result["pre_restore"]["name"],  # flat copy: the audit route map captures this field
        "schema_sync": schema_sync,
        "recovered_jobs": recovered_jobs,
        "cancelled_jobs": cancelled,
        "message": (f"Восстановлен снимок «{name}»: диалогов {counts['conversations']}, оценок {counts['evaluations']}, "
                    f"апелляций {counts['appeals']}. Прежнее состояние сохранено как «{result['pre_restore']['name']}»"),
    })
    return result


@router.get("/snapshots/{name}/download")
def download_snapshot(name: str, request: Request, _admin: AdminActor):
    """The snapshot as a standalone SQLite file (open it with sqlite3 or any SQLite browser)."""
    if (denied := _token_error(request)) is not None:
        return denied
    try:
        _live_db_path()
        sdir = _snapshots_dir()
        path = snap.snapshot_file(sdir, name)
        meta = snap.get_snapshot(sdir, name)
    except snap.SnapshotError as e:
        return _snapshot_error(e)
    _audit("snapshot.download", name, f"Скачан снимок базы «{name}»", _actor(request))
    headers = {"Cache-Control": "no-store", "Access-Control-Expose-Headers": "Content-Disposition, X-Snapshot-SHA256"}
    if meta.get("sha256"):
        headers["X-Snapshot-SHA256"] = str(meta["sha256"])
    return FileResponse(path, media_type=SNAPSHOT_MEDIA_TYPE, filename=f"cqa-{name}.db", headers=headers)


@router.delete("/snapshots/{name}")
def delete_snapshot(name: str, request: Request, force: str | None = None):
    """Delete a snapshot; the protected "baseline" needs ?force=true."""
    if (denied := _token_error(request)) is not None:
        return denied
    try:
        _live_db_path()
        meta = snap.delete_snapshot(_snapshots_dir(), name, force=_truthy(force))
    except snap.SnapshotError as e:
        return _snapshot_error(e)
    return {"deleted": name, "snapshot": meta}
