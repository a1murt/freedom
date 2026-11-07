"""System endpoints for the hosted instance (WP platform): build version, deep health check and runtime metrics.

GET /api/version, GET /api/system/health, GET /api/system/metrics. The shallow GET /api/health (settings router, used by
the Railway and Docker health checks) is unchanged. All three are plain reads (permission `read`, every role).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..admin.snapshots import DISK_MARGIN_BYTES, disk_status
from ..config import REPO_DIR, settings
from ..db import get_db
from ..jobs import guards
from ..jobs import runner as jobs
from ..models import Appeal, Conversation, Evaluation, Job, Setting

log = logging.getLogger("cqa.system")

router = APIRouter(prefix="/api", tags=["system"])

APP_NAME = "CQA API"
STARTED_AT = datetime.now(timezone.utc)
_T0 = time.monotonic()
PYTHON = ".".join(str(x) for x in sys.version_info[:3])
LOCAL_PROBE_TTL_S = 30.0
LOCAL_PROBE_TIMEOUT_S = 1.0
MIB = 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


# --------------------------------------------------------------------------- version (resolved once at start-up)


def _read_ref(git_dir: Path, common_dir: Path, name: str) -> str | None:
    for base in (git_dir, common_dir):
        ref_file = base / name
        if ref_file.is_file():
            return ref_file.read_text().strip()
        packed = base / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if not line.startswith(("#", "^")) and line.endswith(" " + name):
                    return line.split(" ", 1)[0]
    return None


def _git_head(repo: Path) -> str | None:
    """Short commit of the checkout: read from .git directly (no git binary needed), else `git rev-parse --short HEAD`."""
    try:
        git_dir = repo / ".git"
        if git_dir.is_file():  # worktree or submodule: ".git" holds "gitdir: <path>"
            pointer = git_dir.read_text().strip()
            if pointer.startswith("gitdir:"):
                git_dir = (repo / pointer.split(":", 1)[1].strip()).resolve()
        if (git_dir / "HEAD").is_file():
            common_dir = git_dir
            if (git_dir / "commondir").is_file():
                common_dir = (git_dir / (git_dir / "commondir").read_text().strip()).resolve()
            head = (git_dir / "HEAD").read_text().strip()
            sha = _read_ref(git_dir, common_dir, head.split(":", 1)[1].strip()) if head.startswith("ref:") else head
            if sha and _SHA.fullmatch(sha):
                return sha[:7]
    except OSError:
        pass
    if not (repo / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=2, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _package_version() -> str:
    try:
        from importlib.metadata import version as dist_version

        return dist_version("cqa")
    except Exception:
        return "0.1.0"


def resolve_version() -> tuple[str, str]:
    """(version, source): APP_VERSION → RAILWAY_GIT_COMMIT_SHA → git HEAD of the checkout → package version."""
    if settings.app_version and str(settings.app_version).strip():
        return str(settings.app_version).strip()[:64], "app_version"
    sha = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "").strip()
    if sha:
        return (sha[:7] if _SHA.fullmatch(sha.lower()) else sha[:40]), "railway"
    head = _git_head(REPO_DIR)
    if head:
        return head, "git"
    return _package_version(), "package"


try:
    VERSION, VERSION_SOURCE = resolve_version()
except Exception:  # pragma: no cover - never block the router import
    VERSION, VERSION_SOURCE = _package_version(), "package"


def uptime_s() -> float:
    return round(time.monotonic() - _T0, 1)


@router.get("/version")
def version():
    return {"app": APP_NAME, "version": VERSION, "source": VERSION_SOURCE, "started_at": STARTED_AT.isoformat(), "python": PYTHON,
            "deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID") or None}


# --------------------------------------------------------------------------- deep health


def _db_info(db: Session) -> dict[str, Any]:
    url = db.get_bind().url
    backend = url.get_backend_name()
    kind = "sqlite" if backend == "sqlite" else ("postgres" if backend.startswith("postgres") else backend)
    info: dict[str, Any] = {"kind": kind, "ok": False, "latency_ms": None, "size_bytes": None,
                            "conversations": None, "evaluations": None, "appeals": None}
    t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        info["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        info["conversations"] = int(db.scalar(select(func.count()).select_from(Conversation)) or 0)
        info["evaluations"] = int(db.scalar(select(func.count()).select_from(Evaluation)) or 0)
        info["appeals"] = int(db.scalar(select(func.count()).select_from(Appeal)) or 0)
        info["ok"] = True
    except Exception as e:
        log.warning("health: database check failed: %s", e)
        info["error"] = type(e).__name__
        db.rollback()
    if kind == "sqlite":
        path = url.database
        if path and path != ":memory:" and not path.startswith("file:"):
            p = Path(path)
            try:
                if p.is_file():
                    info["size_bytes"] = p.stat().st_size
                wal = p.with_name(p.name + "-wal")
                info["wal_bytes"] = wal.stat().st_size if wal.is_file() else 0
            except OSError:
                pass
    elif kind == "postgres" and info["ok"]:
        try:
            info["size_bytes"] = int(db.scalar(text("SELECT pg_database_size(current_database())")))
        except Exception:
            db.rollback()
    return info


def _local_endpoint(db: Session) -> str | None:
    """The endpoint the judge would use (cqa.judge.local_endpoint.effective_endpoint): the runtime setting (PUT
    /api/settings) when it is an allowed address, else LOCAL_ENDPOINT. A stored address that is not allowed is never probed."""
    from ..judge.local_endpoint import effective_endpoint

    stored = None
    try:
        row = db.get(Setting, "local_endpoint")
        if row is not None and row.value:
            stored = str(row.value).strip() or None
    except Exception:
        db.rollback()
    return effective_endpoint(stored)


_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_probe_lock = threading.Lock()


def _http_get(url: str, timeout: float, headers: dict[str, str]) -> httpx.Response:
    return httpx.get(url, timeout=timeout, headers=headers, follow_redirects=False)


def probe_local(endpoint: str) -> dict[str, Any]:
    """GET <endpoint>/models with a 1 s timeout; the result is cached for 30 s per endpoint."""
    with _probe_lock:
        hit = _probe_cache.get(endpoint)
        if hit is not None and time.monotonic() - hit[0] < LOCAL_PROBE_TTL_S:
            return {**hit[1], "cached": True}
        result: dict[str, Any] = {"reachable": False, "status_code": None, "latency_ms": None, "models": None, "error": None,
                                  "checked_at": datetime.now(timezone.utc).isoformat()}
        t0 = time.perf_counter()
        try:
            from ..judge.local_endpoint import api_key_for  # LOCAL_API_KEY only for LOCAL_ENDPOINT / the allowlist

            r = _http_get(endpoint.rstrip("/") + "/models", LOCAL_PROBE_TIMEOUT_S, {"Authorization": f"Bearer {api_key_for(endpoint)}"})
            result["status_code"] = r.status_code
            result["reachable"] = r.status_code < 400
            if r.status_code == 200:
                try:
                    data = r.json()
                    items = data.get("data") if isinstance(data, dict) else None
                    if isinstance(items, list):
                        result["models"] = [str(m["id"])[:100] for m in items if isinstance(m, dict) and m.get("id")][:20]
                except ValueError:
                    pass
        except Exception as e:  # timeouts, refused connections, invalid URLs
            result["error"] = type(e).__name__
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        _probe_cache[endpoint] = (time.monotonic(), result)
        return {**result, "cached": False}


def _providers(db: Session) -> dict[str, Any]:
    endpoint = _local_endpoint(db)
    local: dict[str, Any] = {"configured": bool(endpoint), "endpoint": endpoint, "model": settings.local_model, "reachable": None}
    if endpoint:
        local.update(probe_local(endpoint))
    return {"anthropic": bool(settings.anthropic_api_key), "local": local}


def _job_counts(db: Session) -> dict[str, Any]:
    counts: dict[str, int] = {}
    try:
        counts = {str(s): int(n) for s, n in db.execute(select(Job.status, func.count()).group_by(Job.status)).all()}
    except Exception:
        db.rollback()
    return {"running": counts.get("running", 0), "queued": counts.get("queued", 0), "interrupted": counts.get("interrupted", 0),
            "live_workers": len(jobs.live_job_ids())}


def _disk_free(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return None


def _live_db_bytes(db_info: dict) -> int | None:
    """One snapshot of the live database: file + WAL, as cqa.admin.snapshots estimates it. None unless it is a SQLite
    file: Postgres does not live on the data volume and has no snapshots, so only the absolute floor applies."""
    if db_info.get("kind") != "sqlite" or not db_info.get("size_bytes"):
        return None
    return int(db_info["size_bytes"]) + int(db_info.get("wal_bytes") or 0)


def _mb(n: int) -> str:
    """Megabytes with one decimal at most ("26 МБ", "1.5 МБ"), never 0: the snapshot count must add up for a small database too."""
    return f"{max(n / MIB, 0.1):.1f}".removesuffix(".0") + " МБ"


def _disk_warning(disk_free: int, db_bytes: int | None) -> str | None:
    """The ``disk_low`` message by the rule the snapshot guard shares (cqa.admin.snapshots.disk_status), None while there
    is room. It says how many snapshots still fit: a bare "N МБ free" means nothing without the size of the database."""
    disk = disk_status(disk_free, db_bytes)
    if not disk["low"]:
        return None
    head = f"Мало места на диске с данными: свободно {disk_free // MIB} МБ"
    fit, size = disk["snapshots_fit"], disk["snapshot_bytes"]
    if fit is None:  # size of the database unknown: only the absolute floor applied
        return head + "."
    if fit == 0:
        return head + f", а для снимка базы ({_mb(size)}) нужно не меньше {_mb(size + DISK_MARGIN_BYTES)}."
    snapshots = guards.plural_ru(fit, ("снимок", "снимка", "снимков"))
    return head + f", это примерно {fit} {snapshots} базы по {_mb(size)} с запасом {_mb(DISK_MARGIN_BYTES)}."


def _health_warnings(db_info: dict, providers: dict, job_counts: dict, disk_free: int | None, budget: dict | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not db_info["ok"]:
        out.append({"code": "db_unavailable", "message": "База данных недоступна."})
    if disk_free is not None and (disk_low := _disk_warning(disk_free, _live_db_bytes(db_info))):
        out.append({"code": "disk_low", "message": disk_low})
    if not providers["anthropic"]:
        out.append({"code": "anthropic_not_configured", "message": "Ключ Claude API не задан: оценка через Claude недоступна."})
    local = providers["local"]
    if local["configured"] and local.get("reachable") is False:
        out.append({"code": "local_unreachable", "message": f"Локальная модель по адресу {local['endpoint']} не отвечает."})
    if budget and not budget["unlimited"]:
        if budget["remaining"] <= 0:
            out.append({"code": "budget_exhausted", "message": f"Суточный бюджет исчерпан: за 24 ч потрачено ${budget['spent_24h']:.2f} из ${budget['limit']:.2f}."})
        elif budget["remaining"] < 0.1 * budget["limit"]:
            out.append({"code": "budget_low", "message": f"Суточный бюджет почти исчерпан: осталось ${budget['remaining']:.2f} из ${budget['limit']:.2f}."})
    active = job_counts["running"] + job_counts["queued"]
    if active > job_counts["live_workers"]:
        out.append({"code": "jobs_orphaned", "message": f"В базе активных задач: {active}, а в этом процессе выполняется {job_counts['live_workers']}."})
    return out


def _snapshots_summary() -> dict[str, Any] | None:
    """Database snapshots at a glance (cqa.admin.snapshots): is there a baseline, how recent is the latest one."""
    try:
        from ..admin.snapshots import list_snapshots

        items = list_snapshots(settings.data_dir / "snapshots")
    except Exception:  # noqa: BLE001 - optional block: never fail the health check
        return None
    baseline = next((m for m in items if m.get("name") == "baseline"), None)
    latest = items[0] if items else None
    return {"count": len(items), "bytes": sum(int(m.get("size_bytes") or 0) for m in items),
            "baseline_at": baseline.get("created_at") if baseline else None,
            "latest": {"name": latest.get("name"), "created_at": latest.get("created_at"), "kind": latest.get("kind")} if latest else None}


@router.get("/system/health")
def system_health(db: Session = Depends(get_db)):
    """Deep check: database, providers, jobs, disk, budget. Always HTTP 200; monitors read `ok` (false = database down)."""
    db_info = _db_info(db)
    providers = _providers(db)
    job_counts = _job_counts(db)
    disk_free = _disk_free(settings.data_dir)
    budget = None
    if db_info["ok"]:
        try:
            budget = guards.budget_status(db)
        except Exception:
            db.rollback()
    return {"ok": db_info["ok"], "version": VERSION, "uptime_s": uptime_s(), "started_at": STARTED_AT.isoformat(),
            "db": db_info, "providers": providers, "jobs": job_counts, "disk_free_bytes": disk_free, "budget": budget,
            "snapshots": _snapshots_summary(),
            "warnings": _health_warnings(db_info, providers, job_counts, disk_free, budget)}


# --------------------------------------------------------------------------- metrics


def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default); p50 is the median."""
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _ms(x: float | None) -> int | None:
    return round(x) if x is not None else None


def _latency(values: list[int]) -> dict[str, Any]:
    v = sorted(values)
    return {"n": len(v), "p50": _ms(_percentile(v, 0.5)), "p90": _ms(_percentile(v, 0.9)), "max": v[-1] if v else None,
            "mean": _ms(sum(v) / len(v)) if v else None}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


@router.get("/system/metrics")
def system_metrics(since_hours: float | None = Query(None, gt=0, le=24 * 366, description="only evaluations/appeals/jobs created in the last N hours"),
                   db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=since_hours) if since_hours else None

    def recent(stmt, column):
        return stmt.where(column >= since) if since is not None else stmt

    by_status = {str(s): int(n) for s, n in db.execute(recent(select(Evaluation.status, func.count()), Evaluation.created_at)
                                                       .group_by(Evaluation.status)).all()}
    total_cost, tokens_in, tokens_out = db.execute(recent(select(
        func.coalesce(func.sum(Evaluation.cost_usd), 0.0), func.coalesce(func.sum(Evaluation.input_tokens), 0),
        func.coalesce(func.sum(Evaluation.output_tokens), 0)), Evaluation.created_at)).one()

    groups: dict[tuple[str, str | None], dict[str, Any]] = {}
    for provider, model, status, n, cost, tin, tout in db.execute(recent(select(
            Evaluation.provider, Evaluation.model, Evaluation.status, func.count(), func.coalesce(func.sum(Evaluation.cost_usd), 0.0),
            func.coalesce(func.sum(Evaluation.input_tokens), 0), func.coalesce(func.sum(Evaluation.output_tokens), 0)),
            Evaluation.created_at).group_by(Evaluation.provider, Evaluation.model, Evaluation.status)).all():
        g = groups.setdefault((provider, model), {"provider": provider, "model": model, "evaluations": 0, "completed": 0, "failed": 0,
                                                  "cost_usd": 0.0, "completed_cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "_lat": []})
        g["evaluations"] += int(n)
        g["cost_usd"] += float(cost or 0.0)
        g["input_tokens"] += int(tin or 0)
        g["output_tokens"] += int(tout or 0)
        if status == "completed":
            g["completed"] += int(n)
            g["completed_cost_usd"] += float(cost or 0.0)
        elif status == "failed":
            g["failed"] += int(n)

    latencies: list[int] = []
    for provider, model, latency in db.execute(recent(select(Evaluation.provider, Evaluation.model, Evaluation.latency_ms)
                                                      .where(Evaluation.status == "completed"), Evaluation.created_at)).all():
        if latency:
            latencies.append(int(latency))
            if (provider, model) in groups:
                groups[(provider, model)]["_lat"].append(int(latency))

    by_provider = []
    completed_cost = 0.0
    for g in sorted(groups.values(), key=lambda x: (-x["evaluations"], str(x["provider"]), str(x["model"]))):
        lat = _latency(g.pop("_lat"))
        group_completed_cost = g.pop("completed_cost_usd")
        completed_cost += group_completed_cost
        g.update(cost_usd=round(g["cost_usd"], 4), latency_ms={"p50": lat["p50"], "p90": lat["p90"], "max": lat["max"]},
                 cost_per_completed_usd=round(group_completed_cost / g["completed"], 4) if g["completed"] else None)
        by_provider.append(g)

    n_completed = by_status.get("completed", 0)
    appeals ={str(s): int(n) for s, n in db.execute(recent(select(Appeal.status, func.count()), Appeal.created_at)
                                                     .group_by(Appeal.status)).all()}
    job_status = {str(s): int(n) for s, n in db.execute(recent(select(Job.status, func.count()), Job.created_at)
                                                        .group_by(Job.status)).all()}
    last_completed = db.scalar(recent(select(func.max(Evaluation.completed_at)), Evaluation.created_at))
    try:
        from ..platform.requestlog import stats_snapshot

        http = stats_snapshot()
    except Exception:  # pragma: no cover - request log not installed
        http = None
    return {
        "generated_at": now.isoformat(),
        "window": {"since_hours": since_hours, "since": since.isoformat() if since else None},
        "evaluations": {"total": sum(by_status.values()), "by_status": by_status},
        "latency_ms": _latency(latencies),
        "cost_usd": {"total": round(float(total_cost or 0.0), 4),
                     "per_completed_evaluation": round(completed_cost / n_completed, 4) if n_completed else None},
        "tokens": {"input": int(tokens_in or 0), "output": int(tokens_out or 0)},
        "by_provider": by_provider,
        "appeals": {"total": sum(appeals.values()), "by_status": appeals},
        "jobs": {"total": sum(job_status.values()), "by_status": job_status},
        "last_evaluation_at": _iso(last_completed),
        "http": http,
    }


# --------------------------------------------------------------------------- readiness (demo and control hour)


@router.get("/system/readiness")
def get_readiness(group_id: str = Query(default="DEV", max_length=64), expect: int | None = Query(default=None, ge=0, le=100000),
                  control_dialogs: int = Query(default=10, ge=1, le=1000), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Is the instance ready for the control hour and the live demo? Required / recommended / info checks, all reads
    (cqa.platform.readiness): organizer checklist v1, dataset size, coverage, results.json validity, jobs, budget reserve,
    organizer URL, seeded appeals, gross violations, baseline snapshot, corporate style."""
    from ..platform.readiness import readiness

    return readiness(db, group_id=group_id.strip() or "DEV", expect=expect, control_dialogs=control_dialogs)

