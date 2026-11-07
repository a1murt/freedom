# Package "admin": database snapshots (reset the jury instance, back up before the control run)

Why: the jury may click through the hosted instance themselves (docs/ORGANIZER_UPDATES.md §3.5: "nothing that can break the shared instance irreversibly"), and the team wants a backup right before the control run. A snapshot is a full, consistent copy of the SQLite database. A restore brings the live database back to it **in place**, after automatically saving the current state so that every restore can be undone.

| File | Role |
|---|---|
| `api/cqa/admin/snapshots.py` | Library and CLI on explicit file paths: `create_snapshot`, `list_snapshots`, `get_snapshot`, `restore_snapshot`, `delete_snapshot`, `snapshot_file`, `live_info`, `resolve_sqlite_path`, `validate_name`, `default_name`, errors (`SnapshotError` and subclasses with `status`/`code`) |
| `api/cqa/admin/__init__.py` | Lazy re-export (PEP 562), so `python -m cqa.admin.snapshots` does not import the module twice |
| `api/cqa/routers/admin.py` | `/api/admin/snapshots` endpoints (auto-loaded by the `"admin"` entry already in `main.py`'s router list) |
| `api/tests/test_admin.py` | 73 tests, temp files only |

## How it works

**Create** (`create_snapshot(db_path, snapshots_dir, name=None, note=None, actor=None, *, overwrite=False, force=False)`):
1. Uses the sqlite3 online backup API, reading from the live file into `<dir>/.<name>.db.part`. This is one consistent read that includes WAL content not yet checkpointed. In WAL mode readers and writers are not blocked; on the hosted instance (26 MB) it takes about 0.1 s.
2. Runs `PRAGMA journal_mode=DELETE` on the copy. A backup of a WAL database is itself flagged WAL; this turns the snapshot into a single standalone file with no `-wal`/`-shm` next to it, so it can be downloaded and opened anywhere.
3. Runs `PRAGMA quick_check` and counts rows, then renames the copy atomically to `<name>.db` and writes the metadata atomically to `<name>.json`. A failure at any step leaves no partial files.

**Restore** (`restore_snapshot(db_path, snapshots_dir, name, dispose_engine_callback, *, actor=None, keep_pre_restore=5)`):
1. Checks the snapshot before touching anything: the SQLite header, `PRAGMA quick_check`, and that the page size matches the live WAL database.
2. Automatic snapshot `pre-restore-<UTC YYYYmmdd-HHMMSS>` of the current state (kind `pre-restore`).
3. Disposes the SQLAlchemy pool (`engine.dispose`).
4. Copies the snapshot **into** the live file with the backup API. This is a single write transaction, and the file is never replaced: its inode stays the same, connections already open (other requests, job threads) stay valid and see the restored data on their next statement, and WAL readers mid-transaction finish on their old view.
5. Runs `PRAGMA wal_checkpoint(TRUNCATE)` and disposes the pool again.
6. Reads the counts back from the live file and compares them with the snapshot (`verified`). Prunes old automatic snapshots: the 5 newest `pre-restore` snapshots are kept, and manual snapshots are never touched.

The router then:
- runs `cqa.db.init_db()`, which is idempotent as at start-up, so a snapshot from an older build gets the tables and additive columns the current code expects (`schema_sync`);
- runs `cqa.jobs.runner.recover_stale_jobs(session_factory=SessionLocal)`, so job and evaluation rows the snapshot captured in flight become `interrupted` / `failed` instead of looking stuck (`recovered_jobs`).

The backup call is bounded: CPython retries `SQLITE_BUSY` forever, so a progress callback aborts after 15 s (`busy`). An aborted backup has written nothing. All snapshot operations in the process are serialized by a lock.

Measured on a 52 MB WAL database (twice the hosted size), with 2 reader threads and 1 writer thread running during the restore:
- snapshot: 0.12 s;
- restore: 0.33 s, of which the copy itself took 114 ms;
- 9,823 reads and 178 writes, 0 errors.

The restore was verified, and writes made after it landed in the restored data.

## Endpoints

All errors use the envelope `{"error": {"code": "...", "message": "... (RU)", ...extra}}`. JSON bodies are parsed by the router itself, so even a malformed body gets the envelope (FastAPI's default `{"detail": [...]}` never appears on these routes). Names, counts, messages and error bodies in the examples come from a run of the full app (real security middleware, request log and audit) on a scratch database; timings, hashes, timestamps and disk sizes are illustrative.

### `GET /api/admin/snapshots`

```json
{
  "items": [
    {"name": "baseline", "created_at": "2026-11-07T13:35:58.120391+00:00", "size_bytes": 307200, "note": "демо-данные",
     "actor": {"role": "admin", "name": "Алина", "request_id": "0e249f0e4754"},
     "counts": {"conversations": 1, "evaluations": 0, "appeals": 0, "checklist_versions": 1},
     "kind": "manual", "integrity": "ok", "sha256": "5d0c…", "sqlite_version": "3.53.1", "duration_ms": 4,
     "protected": true, "download_url": "/api/admin/snapshots/baseline/download"}
  ],
  "total": 1,
  "live": {"size_bytes": 401992, "journal_mode": "wal",
           "counts": {"conversations": 1, "evaluations": 0, "appeals": 0, "checklist_versions": 1}},
  "disk": {"free_bytes": 48318382080, "total_bytes": 494384795648},
  "protected": ["baseline"],
  "name_pattern": "^[a-z0-9][a-z0-9_-]{0,39}$",
  "pre_restore_keep": 5,
  "token_required": false
}
```

Newest first. `live` describes the current database (`null` before the file exists). `disk` is the free space on the volume that also holds the live database. Snapshots without readable metadata are still listed:
- missing `.json`: `"metadata_missing": true`, `kind: "unknown"`, counts recounted from the file;
- broken `.json`: `"metadata_error": "..."`.

Temporary and foreign files are ignored.

### `POST /api/admin/snapshots` → 201

Body (all optional): `{"name": "baseline", "note": "демо-данные", "overwrite": false, "force": false}`. An empty body is allowed and gives the default name `snap-<UTC YYYYmmdd-HHMMSS>`, with `-2`, `-3`… if two snapshots fall in the same second. The name and `created_at` come from the same instant: the moment the backup reads the database.

Response: the snapshot metadata (as in the listing) plus `download_url`. When evaluation jobs are running in this process, the response also carries `"warnings": ["Во время снимка шли задачи оценки (j_…): …"]`: their unfinished evaluations are in the snapshot and will be marked interrupted after a restore.

Errors:

| Status | code | When |
|---|---|---|
| 422 | `invalid_name` | the name does not match `^[a-z0-9][a-z0-9_-]{0,39}$`; `errors: [{"field": "name", "message": "Недопустимое имя снимка: 1–40 символов: строчные латинские буквы, цифры, «-» и «_», первый символ — буква или цифра"}]` |
| 422 | `validation_error` | a wrong type (`name: 5`, `overwrite: "yes"`), `note` longer than 500 characters, or a body that is not an object; `errors: [{"field", "message"}]` |
| 422 | `invalid_json` | the body is not valid JSON |
| 409 | `snapshot_exists` | the name is taken and `overwrite` is not set |
| 409 | `snapshot_protected` | overwriting `baseline` needs `overwrite: true` and `force: true` |
| 409 | `busy` | another snapshot operation is still running, or the database stayed locked for more than 15 s |
| 507 | `insufficient_storage` | free space is below the database size + 32 MB (`free_bytes`, `required_bytes`) |
| 503 | `database_missing` | the live database file does not exist |
| 500 | `snapshot_failed` | an unexpected SQLite or filesystem error (the message names it; no partial files remain) |
| 501 | `not_supported` | `DATABASE_URL` is not a SQLite file (Postgres, `sqlite://` in memory); the message never echoes the URL |

### `GET /api/admin/snapshots/{name}`

One snapshot's metadata plus `download_url`. 404 `not_found`, 422 `invalid_name`.

### `POST /api/admin/snapshots/{name}/restore`

Body: `{"confirm": "baseline", "force": false}`. `confirm` must equal the name.

Response (from the full-app run; `snapshot` and `pre_restore` shortened):

```json
{
  "name": "baseline",
  "restored_at": "2026-11-07T13:35:58.402114+00:00",
  "snapshot": {"name": "baseline", "kind": "manual", "counts": {"…": "…"}, "download_url": "/api/admin/snapshots/baseline/download"},
  "pre_restore": {"name": "pre-restore-20261107-133558", "kind": "pre-restore",
                  "note": "Автоматически перед восстановлением снимка «baseline»",
                  "actor": {"role": "admin", "name": "Алина", "request_id": "…"},
                  "counts": {"conversations": 2, "evaluations": 0, "appeals": 0, "checklist_versions": 1},
                  "download_url": "/api/admin/snapshots/pre-restore-20261107-133558/download"},
  "safety_snapshot": "pre-restore-20261107-133558",
  "counts": {"conversations": 1, "evaluations": 0, "appeals": 0, "checklist_versions": 1},
  "expected_counts": {"conversations": 1, "evaluations": 0, "appeals": 0, "checklist_versions": 1},
  "verified": true,
  "integrity": "ok",
  "pruned": [],
  "duration_ms": 9,
  "schema_sync": "ok",
  "recovered_jobs": {"jobs": [], "evaluations": []},
  "cancelled_jobs": [],
  "message": "Восстановлен снимок «baseline»: диалогов 1, оценок 0, апелляций 0. Прежнее состояние сохранено как «pre-restore-20261107-133558»"
}
```

Response fields:
- `counts` are read back from the live file after the copy; `expected_counts` are counted in the snapshot file.
- `verified` is true when they are equal and `quick_check` returns `ok`.
- `safety_snapshot` is a flat copy of `pre_restore.name`; the audit route map captures that field.
- **Undo** is a restore of `safety_snapshot`.
- `schema_sync` / `recovered_jobs` are `"ok"` or the recovery result, `"skipped"` when the app engine is not this database, or `"failed: …"` (the data is restored either way).

Errors:

| Status | code | When |
|---|---|---|
| 400 | `confirm_mismatch` | `confirm` is missing or differs from the name (`expected`); nothing is created or changed |
| 404 | `not_found` | unknown snapshot (for `baseline` the message explains how to create it) |
| 409 | `jobs_running` | evaluation jobs are running in this process (`jobs: [...]`). `force: true` cancels them and restores at once; dialogs already in flight finish and may fail against the restored data |
| 409 | `snapshot_corrupt` | the file is not SQLite or fails `quick_check`; nothing was touched |
| 409 | `page_size_mismatch` | snapshot and live WAL database have different page sizes (cannot happen for snapshots made by this code) |
| 409 | `busy` | lock timeout; nothing was copied, and `pre_restore` names the safety snapshot already taken |
| 422 | `invalid_name` / `validation_error` / `invalid_json` | as above (`force: "yes"` → `validation_error`) |
| 507 / 503 / 500 / 501 | as above | |

### `GET /api/admin/snapshots/{name}/download`

The snapshot as a standalone SQLite file:
- headers `Content-Type: application/vnd.sqlite3`, `Content-Disposition: attachment; filename="cqa-<name>.db"`, `Cache-Control: no-store`, and `X-Snapshot-SHA256` (also exposed for CORS);
- HTTP Range works through the pure-ASGI middlewares (206 checked in the full-app run);
- 404 / 422 as above.

This is a dump of the whole database, so the endpoint requires the **`admin`** permission (see below), and every download is written to the audit trail as `snapshot.download`.

### `DELETE /api/admin/snapshots/{name}?force=true`

→ `{"deleted": "tmp-1", "snapshot": {…metadata…}}`. Removes the `.db` and `.json` files and any leftovers. Errors: 404 `not_found`; 409 `snapshot_protected` for `baseline` without `force=true` (`force` also accepts `1`/`yes`/`on`).

## Data model notes

No table and no model change. Files live in `settings.data_dir / "snapshots"`:
- locally `api/data/snapshots/`;
- on Railway `/app/data/snapshots/`, on the persistent volume next to `cqa.db`.

`api/data/` is git-, docker- and railway-ignored, so snapshots never leak into a commit or an upload.

`<name>.json`:

| Field | Meaning |
|---|---|
| `name`, `created_at` (ISO UTC, the instant of the read), `size_bytes` | as requested |
| `note`, `actor` | free text (≤ 500); `{role, name, request_id}` from `request.state.actor`, `null` without the security middleware, `{"role": "cli", "name": $USER}` from the CLI |
| `counts` | `{conversations, evaluations, appeals, checklist_versions}` counted in the snapshot file |
| `kind` | `manual` or `pre-restore`; only `pre-restore` snapshots are pruned |
| `integrity`, `sha256`, `sqlite_version`, `duration_ms` | `quick_check` result, file checksum (also sent on download), SQLite library version, backup time |

API responses add `protected` (name is `baseline`) and `download_url`.

## CLI (on the host, e.g. a Railway shell; from `api/`)

```bash
python -m cqa.admin.snapshots list
python -m cqa.admin.snapshots create --name pre-control --note "перед контрольным прогоном"
python -m cqa.admin.snapshots restore baseline --yes
python -m cqa.admin.snapshots delete old-test [--force]
```

`--db` and `--dir` default to `DATABASE_URL` and `<DATA_DIR>/snapshots`. JSON goes to stdout and errors to stderr as the same envelope. Exit codes: 0 ok, 1 error, 2 restore without `--yes`.

A CLI restore works while the API is running, because the copy goes into the live file. With the default `--db` it also runs `init_db()`. The API's pool is not disposed from another process, but its open handles stay valid.

## Runbook for the hosted jury instance

1. After the instance is populated (DEV imported and evaluated, no job running): `POST /api/admin/snapshots {"name": "baseline", "note": "демо-данные для жюри"}`.
2. After the jury's hands-on session: `POST /api/admin/snapshots/baseline/restore {"confirm": "baseline"}`. Show the `message`; the jury's changes stay available as `safety_snapshot`.
3. Before the control run: `POST /api/admin/snapshots {"name": "pre-control"}`, or the CLI on the host.
4. If a restore was a mistake: restore the `safety_snapshot` it returned.

Optional lock for the public instance: set `CQA_ADMIN_TOKEN` on Railway. Create, restore, delete and download then also need `X-CQA-Admin-Token: <token>` (403 `admin_token_required`, constant-time comparison); the listing reports `token_required: true` so a UI can ask for it. Without the variable nothing changes. Never put the token in the web bundle.

## Permissions, audit, guards

**Writes** (POST create, POST restore, DELETE) need the permission `admin`, enforced by the security middleware from its route map (`cqa/security/roles.py`):
- `snapshot.create` (entity `response:name`; captures `name`, `size_bytes`, `created_at`);
- `snapshot.restore` (entity `path:name`; captures `name` and `safety_snapshot`);
- `snapshot.delete`.

**Reads.** The middleware lets every role read. For the **download** the router checks `admin` itself with `cqa.security.roles.require_permission("admin")`, which returns 403 `forbidden` with `role`/`permission`; checked in the full-app run, e.g. visitor → 403. The listing and the metadata endpoint stay `read`.

**Demo mode.** A request without `X-CQA-Role` is `demo` with every permission (contract §1), so the current web app keeps working unchanged.

**Actor.** The handlers only read `request.state.actor`, set by the middleware, and store it in the metadata.

**Audit.**
- The download is audited by the router through `cqa.audit.record` (`snapshot.download`, entity `snapshot/<name>`), because the middleware does not audit GETs.
- The restore's own audit event is written after the response, so it lands in the *restored* database: the restored trail shows who reset the instance, and to what.

**Guards:**

| Guard | Response |
|---|---|
| Live evaluation jobs during a restore | 409 `jobs_running` unless `force` |
| Lock timeout (15 s) | 409 `busy` |
| Free space below the needed size + 32 MB (the volume also holds the live database) | 507 |
| Corrupt snapshot | 409, before any change |
| Protected `baseline` | 409 unless `force` |
| Names | `^[a-z0-9][a-z0-9_-]{0,39}$` (`fullmatch`, so no trailing-newline loophole); a name always becomes a file name inside the snapshots folder, so there is no path traversal |

**Test-safety guards.**
- The hooks that touch the app engine (`_sync_schema`, `_recover_stale_jobs`, `_audit`) act only when `cqa.db.engine` points at the same file as `settings.database_url`.
- The job-recovery hook also requires the session factory to be bound to that engine.

A mismatched configuration (as in a test) can therefore never write to another database.

## Tests (`api/tests/test_admin.py`, 73 tests, ~1.7 s, temp files only)

Never touched by the tests:
- `api/data/cqa.db`: every database lives under `tmp_path`; the API tests patch `settings.database_url`/`data_dir` and swap `cqa.db.engine`/`SessionLocal` for a temp WAL engine, and they assert the resolved paths;
- the network and the Claude API.

The suite also passes in reversed order.

- **Names and URLs.** Valid and invalid names (uppercase, leading `-`/`_`, 41 characters, `/`, `..`, `.`, space, Cyrillic, trailing `\n`, NUL, non-strings); UTC default names (converted from UTC+5); `resolve_sqlite_path` for absolute, relative, `+pysqlite` and `+aiosqlite` URLs, and NotSupported for in-memory, Postgres (the password is not echoed) and garbage.
- **Create.**
  - Output: a standalone rollback-journal file (header bytes) with nothing next to it; metadata fields, counts, sha256, actor and note; uncheckpointed WAL frames are included.
  - Names: collision suffixes; `snapshot_exists`, overwrite, and the protected baseline needing `force`.
  - Failures: a missing live file is never created; the disk guard; a filesystem error on rename becomes `snapshot_failed` and leaves no files.
- **Restore.**
  - Data and handles: data reverted (a new dialog, a deleted appeal and a changed score all come back); same inode; a raw sqlite3 handle and a SQLAlchemy session opened before the restore keep working and see the restored data; dispose is called twice.
  - The pre-restore snapshot holds the mutated state, and restoring it undoes the restore; tables created after the snapshot disappear.
  - Refusals that change nothing: unknown snapshot (no pre-restore created), corrupt snapshot (garbage or truncated), page-size mismatch; a locked database gives a clean `busy` with nothing half-copied, and the restore succeeds after the lock is released.
  - Pruning keeps the 2 newest automatic snapshots (with `keep_pre_restore=2`) and never touches manual ones.
- **List, get, delete.** Newest first; files with lost or broken metadata are still listed, stray files ignored; protected baseline; `live_info`.
- **CLI.** create, list, restore (needs `--yes`), delete, and the error envelope on stderr.
- **API.**
  - Plumbing: router registered in `cqa.main.app` (checked via OpenAPI, no lifespan and no requests).
  - Create and list: list/create with the actor from a pure-ASGI stand-in for the security middleware; 7 validation cases plus malformed JSON, all in the envelope; duplicate and overwrite.
  - Restore: the full flow (confirm mismatch in 4 variants changes nothing; the restore returns verified counts, the message and `safety_snapshot`; the listing shows the pre-restore snapshot); unknown and invalid names; `jobs_running` and `force` (`force` cancels the jobs).
  - Restore of an older-schema snapshot (dropped table and column): the real `init_db` adds them back.
  - Restore of a snapshot taken mid-run: the real `recover_stale_jobs` marks the job `interrupted` and the evaluation `failed`, and the create response carried the warning.
  - Download: bytes, headers and sha256, and the file opens with the expected counts; the audit hook is called; visitor, operator and analyst get 403 `forbidden`, admin and demo pass.
  - Delete with protection; 501 on all 6 endpoints for Postgres and in-memory URLs; the optional admin token on every protected endpoint.
- **Hooks.**
  - Live job-thread detection and cancel.
  - The hooks refuse a mismatched engine, even with a fake `cqa.audit` installed, and the job-recovery hook refuses an unbound session factory.
  - The audit hook calls `record` with the right arguments and survives an exception; with the real `cqa.audit` it writes one `snapshot.download` row into the temp database (skipped if the module is absent).

## Limitations

- **SQLite only.** Postgres returns 501; use the provider's backups (pg_dump, Railway or Supabase snapshots).
- **A restore rewinds the whole database**, including `audit_events`, `settings`, `jobs` and `fewshot_examples`:
  - the history recorded after the snapshot survives only in the automatic pre-restore snapshot (download it to keep it);
  - the restore itself is audited in the restored database.
  - Keeping the audit trail across restores would need a separate audit store; see Integration requests.
- **A tiny gap.** Writes that land between the automatic pre-restore snapshot and the copy (milliseconds) are in neither copy.
- **One process only.** `jobs_running` sees only jobs in this process. Several API processes on one file (not our deployment) could write during a restore; SQLite stays consistent, but their writes may be lost.
- **Snapshots hold the database only.** Cached MP3 files in `data/audio` are re-fetched on demand.
- **Disk use.** Each snapshot is a full copy (~26 MB on the hosted instance). Five automatic snapshots are kept; manual ones are never pruned.
- **In-memory state is not reset:** the job event buffers in `cqa.jobs.runner` and the request counters. No database-derived cache was found; the only cache is the claims-ledger file.
- **No upload.** Restoring an arbitrary uploaded SQLite file on a public instance is a risk we do not take; snapshots are made only from the live database.
- **The token is a shared secret** in an environment variable, not a user system.

## Fix pass (docs/arch/fixes.md)

- A restore no longer resets the daily budget: spend lives in the spend ledger (`<DATA_DIR>/spend_ledger.db`), which is not part of any snapshot.
- On the hosted instance (Railway, or `CQA_PUBLIC_INSTANCE=1`) create / restore / delete / download need `CQA_ADMIN_TOKEN` (403 `admin_token_not_configured` without it; `ALLOW_UNPROTECTED_SNAPSHOTS=1` opts out). Locally nothing changes.
- At most `SNAPSHOT_MAX_MANUAL` (20) manual snapshots (409 `too_many_snapshots`; overwriting an existing name is allowed).
- Restore runs under `cqa.jobs.runner.start_lock`; with `force: true` it cancels running jobs and waits up to 30 s for their in-flight dialogs (else 409 `jobs_still_running`, data unchanged), then bumps `cqa.db.restore_generation`, so an evaluation that started before never writes into the restored database. The CLI restore refuses while jobs are queued/running (`--force`).

## Integration requests (shared files this package may not edit)

1. `docs/API.md`: add a section "Admin: database snapshots" with the six endpoints, bodies, errors and the examples above (all additive). Also: `GET /api/admin/snapshots/{name}/download` requires `admin`; `CQA_ADMIN_TOKEN` / `X-CQA-Admin-Token` are optional.
2. `cqa/main.py`: nothing required; the router is already in the list. Optional: a global `RequestValidationError` → envelope handler, for the other routers; this router parses its bodies itself.
3. `cqa/config.py` (optional): `admin_token: str | None = None` (env `CQA_ADMIN_TOKEN`) and `snapshot_keep_pre_restore: int = 5`. The router reads `os.environ["CQA_ADMIN_TOKEN"]` directly today, as `export.py` does for `TEAM_NAME`.
4. Roles package (`cqa/security/roles.py`), optional:
   - add `verified` and `counts` to the `snapshot.restore` capture (`safety_snapshot` and `name` are already captured);
   - if you prefer the download permission in the middleware (a GET rule for `/api/admin/snapshots/{name}/download` → `admin`, audited as `snapshot.download`), tell me, and I remove the handler-level `require_permission` and `_audit` call to avoid double events.
   - Keeping `audit_events` across restores would need audit rows in a separate SQLite file (`ATTACH`), or a carry-over step owned by the audit service.
5. `scripts/control_run.py` (owner of the script): before importing the control set, `POST {API}/api/admin/snapshots {"name": "pre-control", "note": "перед контрольным прогоном"}` and print the name; on failure warn and continue (a missing backup must not block the run). Use a new name or `"overwrite": true` on a second run.
6. `docs/HOSTING.md` / `docs/DEMO.md`: link the runbook above (baseline after populating, restore after the hands-on, pre-control before the control run).
7. Platform package, optional: `GET /api/system/health` could show `snapshots: {count, baseline_at, latest_at, bytes}` from `cqa.admin.snapshots.list_snapshots(settings.data_dir / "snapshots")`, so the team sees at a glance that a baseline exists.
8. Web (owned by the user; for the redesign): an Admin page with the following.
   - **List:** with live counts and free disk.
   - **Create:** name and note; show `warnings`.
   - **Restore:** a confirm dialog where the name is typed, sent as `confirm`; show `message`, and offer "Отменить восстановление" = restore of `safety_snapshot`.
   - **Download link:** `download_url`, prefixed with the API base.
   - **Delete:** `baseline` only with an explicit force.
   - **Token:** if `token_required`, ask for it and send it as `X-CQA-Admin-Token`.
