# Package `platform`: hardening for the public hosted instance and the 1-hour control run

Owner files: `api/cqa/platform/requestlog.py`, `api/cqa/jobs/guards.py`, `api/cqa/jobs/runner.py`, `api/cqa/routers/evaluations.py`, `api/cqa/routers/system.py`, `api/tests/test_platform.py`, this file.
No shared file was edited. `main.py` already wires everything (it lists the `system` router, calls `cqa.jobs.runner:recover_stale_jobs` at start-up, and `cqa.platform.install` already lists `cqa.platform.requestlog`).

What it adds:

| Concern | Where | Summary |
|---|---|---|
| Request id, timing, access log | `cqa/platform/requestlog.py` | Pure ASGI, outermost. `X-Request-ID` on every response, `request.state.request_id`, one `cqa.access` INFO line per request |
| Run guards | `cqa/jobs/guards.py` | Rolling 24 h budget (with reservations for running jobs), concurrent-job limit, dialogs-per-run limit → 429 with precise codes |
| Start-up recovery | `cqa/jobs/runner.py` `recover_stale_jobs()` | Jobs left queued/running → `interrupted` (log + audit), evaluations left running → `failed` |
| Resume | `POST /api/jobs/{id}/resume` | Re-queues only the dialogs that still have no completed evaluation for the job's checklist version |
| Version, deep health, metrics | `cqa/routers/system.py` | `GET /api/version`, `GET /api/system/health`, `GET /api/system/metrics` |

## 1. Request id middleware (`cqa.platform.requestlog`)

- **Pure ASGI** (`scope/receive/send`), never `BaseHTTPMiddleware`. Each response message is forwarded the moment it is sent; only `http.response.start` is rewritten (headers). SSE (`/api/jobs/{id}/events`) and ranged audio (`/api/audio/{id}`) keep streaming (tested at the ASGI level and on a real uvicorn).
- **Request id**: an id already in `scope["state"]["request_id"]`, else a valid incoming `X-Request-ID` (1–64 chars of `A-Za-z0-9._:/+=@-`, first char alphanumeric), else 12 hex chars. Invalid ids (spaces, non-ASCII, too long) are replaced, never echoed. The id is stored in `scope["state"]["request_id"]` (so `request.state.request_id` works) and in a context variable (`requestlog.get_request_id()`, works in async and sync handlers). `cqa.security.middleware` reads the same `scope["state"]["request_id"]` and uses the same character set, so audit events carry the id from the response header.
- **Response header**: `X-Request-ID` on every response, including 403/400 answered by the security middleware, CORS preflights, 404/409/429 envelopes and 500 pages. On cross-origin responses (those carrying `Access-Control-Allow-Origin`) `X-Request-ID` is merged into `Access-Control-Expose-Headers` (existing values such as the export's `X-Results-Valid, …` are kept), so the web app can read it.
- **Access log**: one INFO line on logger `cqa.access` when the response finishes (for SSE: when the stream closes), role from `X-CQA-Role` (sanitised) or `demo`, path without the query string:
  ```
  GET /api/health 200 30.0ms role=demo rid=de59bbc10b5f
  POST /api/evaluations/run 429 1.9ms role=demo rid=1fd12a39f4e3
  POST /api/jobs/j_stale0000001/resume 200 10.6ms role=demo rid=smoke-resume
  GET /api/jobs/j_e35df29cba08/events 200 703.2ms role=demo rid=565b6880e045
  GET /t/boom 500 3.1ms role=demo rid=rid-500 error=RuntimeError     (test route: an unhandled exception)
  ```
- **Placement**: `install(app)` wraps the whole built middleware stack (it overrides `app.build_middleware_stack` on the instance), so the request log runs outside Starlette's error middleware, CORS and the security middleware no matter in which order they are installed. With a plain `add_middleware` the security middleware (installed after it) would sit outside and its 403 answers would carry no id (checked with a negative control). `install` is idempotent and also works on an app that already started.
- **In-process counters** (`stats_snapshot()`, shown in `/api/system/metrics` as `http`): requests, in flight, by status class, SSE streams, and p50/p90/max over the last 1000 non-streaming requests. They reset on restart.

## 2. Run guards (`cqa.jobs.guards`)

Settings (env-overridable, `cqa/config.py`): `DAILY_BUDGET_USD` (75, `0` = unlimited), `MAX_DIALOGS_PER_RUN` (200, `0` = no limit), `MAX_CONCURRENT_JOBS` (2, `0` = no limit).

```python
budget_status(db) -> {"spent_24h": 17.7234, "limit": 75.0, "remaining": 57.2766, "reserved": 0.0, "unlimited": False, "window_hours": 24}
check_run(db, n_dialogs, estimate_usd) -> budget_status  # or raises GuardError(code, message, status=429, extra)
```

- `spent_24h` = sum of `Evaluation.cost_usd` of evaluations **created** in the last 24 h (all statuses: failed evaluations cost money too).
- `reserved` = for every job in `queued|running`: `max(params.estimated_cost_usd − job.cost_usd, 0)`, i.e. what running jobs are still expected to spend (their finished evaluations are already in `spent_24h`). Without it, two jobs started back to back could both pass the budget.
- `remaining` = `max(limit − spent_24h − reserved, 0)`. When unlimited, `limit` and `remaining` are `null` and `unlimited` is `true`.
- Checks, in this order (the first failing one is reported):

| Code | Condition | `extra` fields |
|---|---|---|
| `too_many_dialogs` | `n_dialogs > MAX_DIALOGS_PER_RUN` | `requested`, `limit` |
| `too_many_jobs` | jobs in `queued|running` ≥ `MAX_CONCURRENT_JOBS` | `running`, `limit`, `active_job_ids` |
| `budget_exceeded` | `spent_24h + reserved + estimate > limit` (only when the estimate is > 0: the local model, estimate $0, is never blocked by the budget) | `budget`, `estimate_usd`, `retry_after_s` |

`retry_after_s` is when enough old spend leaves the 24 h window for this run to fit (`null` if the run alone is above the budget or running jobs hold the rest); when it is known, the HTTP answer also carries `Retry-After`. Messages are Russian with the numbers, for example:

```json
{"error": {"code": "budget_exceeded",
  "message": "Превышен суточный бюджет на оценку: за последние 24 ч потрачено $15.00 из $20.00, оценка этого запуска $6.00, доступно $5.00. Бюджет освободится примерно через 1 ч 1 мин.",
  "budget": {"spent_24h": 15.0, "limit": 20.0, "remaining": 5.0, "reserved": 0.0, "unlimited": false, "window_hours": 24},
  "estimate_usd": 6.0, "retry_after_s": 3601}}
{"error": {"code": "too_many_jobs", "message": "Достигнут лимит одновременных задач оценки: сейчас активно 2 из 2. Дождитесь завершения или отмените одну из задач.",
  "running": 2, "limit": 2, "active_job_ids": ["j_1", "j_2"]}}
{"error": {"code": "too_many_dialogs", "message": "Слишком много диалогов в одном запуске: выбрано 201, допускается не больше 200. Разбейте запуск на несколько частей.",
  "requested": 201, "limit": 200}}
```

Within the process, "check the guards, then create the job" runs under `runner.start_lock`, so two simultaneous requests cannot both pass the concurrency limit.

## 3. Evaluations router changes (`cqa/routers/evaluations.py`)

All existing fields and codes are unchanged; error messages are now Russian.

### `POST /api/evaluations/run`
- **Dry run** always answers 200 and adds `budget` and `guard` (`null` when a real run would start now, otherwise the error a real run would get):
  ```json
  {"queued": 3, "estimated_cost_usd": 0.54, "rejudge_codes": null, "checklist_version_id": 1,
   "budget": {"spent_24h": 0.0, "limit": 0.5, "remaining": 0.5, "reserved": 0.0, "unlimited": false, "window_hours": 24},
   "guard": {"code": "budget_exceeded", "message": "Превышен суточный бюджет на оценку: … Этот запуск дороже всего суточного бюджета: уменьшите число диалогов.",
             "budget": {…}, "estimate_usd": 0.54, "retry_after_s": null}}
  ```
- **Real run**: guards first → 429 envelope as in §2 (no job is created). On success the answer adds `budget` taken after queueing (this job's estimate is already in `reserved`):
  ```json
  {"job_id": "j_3f9a1c2b4d5e", "queued": 2, "estimated_cost_usd": 0.36, "rejudge_codes": null, "checklist_version_id": 1,
   "budget": {"spent_24h": 0.0, "limit": 75.0, "remaining": 74.64, "reserved": 0.36, "unlimited": false, "window_hours": 24}}
  ```
- The job stores `params.estimated_cost_usd`.
- Ids that do not exist at all → 400 `no_conversations` ("Ни один из выбранных диалогов не найден.") instead of an empty job.

### `POST /api/jobs/{job_id}/resume` (new)
Re-queues the job's conversations that still have **no completed evaluation for the job's checklist version** (from this or any later job), with the same provider, model, mode, base version and `only_codes`. Guards apply (it spends money like a run). Answers like `/api/evaluations/run`, plus where it came from:

```json
{"job_id": "j_e35df29cba08", "queued": 2, "estimated_cost_usd": 0.0, "rejudge_codes": null, "checklist_version_id": 1,
 "budget": {"spent_24h": 0.0, "limit": 0.5, "remaining": 0.5, "reserved": 0.0, "unlimited": false, "window_hours": 24},
 "resumed_from": "j_stale0000001", "skipped_completed": 1, "skipped_missing": 0}
```

| Status | Code | When |
|---|---|---|
| 404 | `not_found` | unknown job |
| 409 | `job_active` | the job is still queued/running (`job_id`, `status` in the envelope) |
| 409 | `already_resumed` | its resumed copy (`params.resumed_as`) is still queued/running (`job_id` of the copy) |
| 409 | `nothing_to_resume` | every dialog already has a completed evaluation (`completed`, `missing` counts) |
| 409 | `no_checklist` | the job's checklist version no longer exists |
| 429 | guard codes | as in §2 |

The new job has `params.resumed_from`; the old job gets `params.resumed_as` and a log entry `resumed as j_…: N conversation(s) re-queued`.

### Jobs
- New status `interrupted` (set by start-up recovery). `GET /api/jobs` and `GET /api/jobs/{id}` add `resumable` (finished, `done < total`, not resumed yet; the resume endpoint is the authority).
- `GET /api/jobs/{id}/events` now ends with `job.done` also for `interrupted` (before, a client following an interrupted job would wait forever).

## 4. Runner (`cqa/jobs/runner.py`)

- `recover_stale_jobs(session_factory=None) -> {"jobs": [...], "evaluations": [...]}`: own session, idempotent, never raises (any error is logged as a warning).
  - jobs in `queued|running` whose worker is not alive in this process → `interrupted`, `finished_at`, log entry (`warning`): `interrupted by restart (was running): done=1 failed=0 of 3, 1 evaluation(s) in flight marked failed; POST /api/jobs/j_…/resume re-queues the rest`;
  - evaluations in `running` not owned by a live job of this process → `failed`, `error = "interrupted by restart"`, `completed_at`; an evaluation without a job is only touched if it was created before this process started;
  - if `cqa.audit.record` exists: one audit event per interrupted job, `action=job.interrupted`, `entity_type=job`, actor role `system`, summary `Задача j_… прервана перезапуском сервера: выполнено 1 из 3`, details `{previous_status, done, failed, total, cost_usd, evaluations_failed}`. A failing audit hook never undoes the recovery.
- `resume_plan(db, job)`, `resume_conflict(db, job)`, `resume_job(job_id, conversation_ids=None, *, extra_params=None) -> new job id` (raises `ResumeError` with code/status as in the table above; usable from scripts too).
- `start_evaluation_job(..., extra_params=None)`: same semantics and signature as before plus optional extra params (`estimated_cost_usd`, `resumed_from`) that never override the run parameters. The in-memory event stream (`JobState`) is unchanged, so SSE works as before.
- Robustness: a crashed worker (or a thread that cannot start) marks its job `failed` with a log entry and emits `job.done` instead of leaving it `running` (which would block the concurrency guard forever). `live_job_ids()` tells which workers run in this process. The in-memory registry keeps the event logs of the 20 most recently finished jobs (it used to grow without bound).

## 5. System endpoints (`cqa/routers/system.py`)

`GET /api/health` (Railway/Docker health check) is unchanged: `{"ok":true,"db":"sqlite","providers":{"anthropic":false,"local":true},"organizer_api":true}`.

### `GET /api/version`
```json
{"app": "CQA API", "version": "99a1c9e", "source": "git", "started_at": "2026-11-07T13:33:58.020009+00:00", "python": "3.12.16", "deployment_id": null}
```
`version` is resolved once at start-up: `APP_VERSION` (`source: app_version`) → `RAILWAY_GIT_COMMIT_SHA` (first 7, `railway`) → the checkout's HEAD (`git`; read from `.git` directly, worktrees and packed refs included, `git rev-parse --short HEAD` only as a fallback) → package version (`package`, "0.1.0"). `deployment_id` = `RAILWAY_DEPLOYMENT_ID`.

### `GET /api/system/health` (deep)
Always HTTP 200; monitors read `ok` (false = database unreachable). `warnings` are Russian, human-readable.
```json
{"ok": true, "version": "smoke-1832", "uptime_s": 49.4, "started_at": "2026-11-07T13:32:58.343929+00:00",
 "db": {"kind": "sqlite", "ok": true, "latency_ms": 0.2, "size_bytes": 307200, "wal_bytes": 144232, "conversations": 3, "evaluations": 4, "appeals": 0},
 "providers": {"anthropic": false,
               "local": {"configured": true, "endpoint": "http://127.0.0.1:9/v1", "model": "qwen3:8b", "reachable": false, "status_code": null,
                         "latency_ms": 4.3, "models": null, "error": "ConnectError", "checked_at": "2026-11-07T13:33:47.731485+00:00", "cached": false}},
 "jobs": {"running": 0, "queued": 0, "interrupted": 1, "live_workers": 0},
 "disk_free_bytes": 38855811072,
 "budget": {"spent_24h": 0.0, "limit": 0.5, "remaining": 0.5, "reserved": 0.0, "unlimited": false, "window_hours": 24},
 "warnings": [{"code": "anthropic_not_configured", "message": "Ключ Claude API не задан: оценка через Claude недоступна."},
              {"code": "local_unreachable", "message": "Локальная модель по адресу http://127.0.0.1:9/v1 не отвечает."}]}
```
- `db.size_bytes`/`wal_bytes`: SQLite file sizes (Postgres: `pg_database_size`); on failure `db.error` is the exception class only (details go to the server log).
- `providers.local`: endpoint = runtime setting `local_endpoint` (PUT /api/settings) or `LOCAL_ENDPOINT`; probe `GET <endpoint>/models` with a 1 s timeout and `LOCAL_API_KEY` as bearer, cached 30 s per endpoint; `reachable` = HTTP < 400, `models` = up to 20 ids from an OpenAI-style list. `reachable: null` when not configured.
- `jobs.live_workers` = workers alive in this process; `warnings` has `jobs_orphaned` when the database shows more active jobs than that.
- Other warning codes: `db_unavailable`, `disk_low` (< 500 MB free in `DATA_DIR`), `budget_exhausted`, `budget_low` (< 10 % left).

### `GET /api/system/metrics?since_hours=`
Optional `since_hours` (> 0) limits evaluations, appeals and jobs to those created in the window.
```json
{"generated_at": "…", "window": {"since_hours": null, "since": null},
 "evaluations": {"total": 6, "by_status": {"completed": 4, "failed": 1, "running": 1}},
 "latency_ms": {"n": 4, "p50": 2500, "p90": 3700, "max": 4000, "mean": 2500},
 "cost_usd": {"total": 1.05, "per_completed_evaluation": 0.25},
 "tokens": {"input": 400, "output": 40},
 "by_provider": [{"provider": "anthropic", "model": "anthropic:claude-opus-5-5", "evaluations": 4, "completed": 3, "failed": 0, "cost_usd": 0.6,
                  "input_tokens": 300, "output_tokens": 30, "latency_ms": {"p50": 2000, "p90": 2800, "max": 3000}, "cost_per_completed_usd": 0.2}, …],
 "appeals": {"total": 2, "by_status": {"open": 1, "decided": 1}},
 "jobs": {"total": 2, "by_status": {"completed": 1, "interrupted": 1}},
 "last_evaluation_at": "…",
 "http": {"since": "…", "requests": 15, "in_flight": 1, "streams": 2, "by_status_class": {"2xx": 13, "3xx": 0, "4xx": 2, "5xx": 0, "other": 0, "aborted": 0},
          "latency_ms": {"window": 13, "p50": 4.1, "p90": 14.7, "max": 30.0}}}
```
Latency percentiles are over completed evaluations with a recorded latency (linear interpolation, p50 = median). `cost_usd.total` counts every evaluation, `per_completed_evaluation` only completed ones.

## 6. Data model

No schema change, no new table or column.
- `Job.status` gains `interrupted`; `Job.params` gains `estimated_cost_usd` (every new job), `resumed_from` (resumed copy) and `resumed_as` (original job).
- `Evaluation.error = "interrupted by restart"` for evaluations killed by a restart.

## 7. Permissions

- `GET /api/version`, `GET /api/system/health`, `GET /api/system/metrics`: `read` (every role).
- `POST /api/jobs/{job_id}/resume`: `evaluations.run`; already mapped in `cqa/security/roles.py` (action `job.resume`, captures `job_id, queued, estimated_cost_usd, skipped_completed, skipped_missing`). The run route already captures `guard.code` on dry runs.
- Start-up recovery writes its audit events as role `system`.

## 8. Tests (`api/tests/test_platform.py`, 52 tests, no network, no Claude)

Each test uses its own SQLite file in `tmp_path` (worker threads open their own connections); `runner.SessionLocal`, `evaluations.SessionLocal` and the judge (`runner.evaluate_conversation` → offline `FakeJudge`) are monkeypatched; the real `cqa.audit` is isolated. The demo database `api/data/cqa.db` was checked unchanged (mtime and size) after a run.

- Guards: 24 h window, failed evaluations counted, unlimited budget, exact-limit boundary, message numbers and retry hint, run above the whole budget, free (local) runs, reservations of active jobs, too_many_jobs (only queued/running count, 0 disables), too_many_dialogs checked first, Russian plurals.
- Router: 429 envelope + `Retry-After` + no job created; dry run `budget` and `guard`; too_many_jobs and too_many_dialogs over HTTP; successful run reports `budget` with this job reserved (judge held on a gate), `params.estimated_cost_usd`; unknown ids → 400.
- Recovery: jobs → interrupted with log, evaluations → failed, orphan evaluations created after boot untouched, idempotent, live jobs left alone, never raises (broken factory, broken session), one audit event per job through `cqa.audit.record`, failing audit hook, keyword filtering for the hook, main.py hook string resolves.
- Runner: crashed worker → job failed + `job.done`; extra params never override run params; registry pruning.
- Resume: 404, 409 `nothing_to_resume`, 409 `job_active`, 409 `already_resumed`, success re-queues only unfinished dialogs (skips completed and deleted ones), back-references, `resumable` flag, guards apply, `ResumeError` from the function.
- System: version shape and precedence (APP_VERSION → Railway → git → package), git HEAD from loose refs, packed refs, detached HEAD and worktrees without running git; health shape and counts, local probe + 30 s cache + runtime setting override + unreachable warning, broken database → `ok: false` with HTTP 200; metrics values (status counts, percentiles, costs, tokens, per provider/model, appeals, last evaluation), `since_hours` window and validation.
- Middleware: id echoed / generated / invalid ids replaced, `request.state.request_id` and context variable in async and sync handlers, one INFO log line per request with role, ids on 404/500 (with `error=` in the log), runs outside middlewares added later (simulated security 403 and CORS preflight), CORS expose header merged, idempotent install, install on a started app, chunks forwarded without buffering (ASGI-level; a buffering middleware fails this test), SSE job events stream through the middleware, SSE closes for `interrupted`, stats snapshot.

Full suite at hand-off: `449 passed, 6 xfailed, 1 warning` (the warning is the testing package's note about the `fake` provider shim).

Also verified by hand (not in CI): uvicorn on port 8011 with a throwaway database, no Claude key and the local endpoint on a closed port. Start-up recovery interrupted a pre-seeded stale job and wrote the `job.interrupted` audit event through the real `cqa.audit`; the dry run returned `budget`/`guard`; a real Claude run got 429 `budget_exceeded`; resume re-queued 2 of 3 dialogs and a second resume got 409 `already_resumed`; the first SSE events arrived 0.7 s before the stream closed through the full stack (request log, security, CORS); legacy `/api/health` unchanged. The server was stopped afterwards.

## 9. Limitations

- The budget uses `Evaluation.cost_usd`, written when an evaluation finishes: spend of evaluations killed mid-flight (restart, crash) is lost from the accounting. Estimates are per-dialog constants from `estimate_cost` (Claude: $0.18 per chat, $0.35 per call); the guard protects the credit approximately, it is not a hard cap on the provider bill.
- *Superseded by the fix pass (`docs/arch/fixes.md`):* spend now comes from the spend ledger (every billed call, booked when the response arrives, cache writes included) with the database as a floor; estimates are per dialog and token-based (`cqa.jobs.estimate`); a paid job stops starting dialogs once the 24 h spend reaches the budget; trials and spec compilations reserve their estimate. Dialogs already in flight still finish, so a job can pass the cap by at most `MAX_PARALLEL_CONVERSATIONS` dialogs.
- The start lock and the job registry are per process. Railway runs one uvicorn process; with several workers on one database two runs could pass the guards at the same moment, and a starting worker would mark the other worker's jobs interrupted. The same applies to offline scripts (`scripts_run_dev.py`, eval harness) that run jobs on the API's database while the API restarts (their job shows `interrupted` until the script finishes it).
- "Completed" for resume means `Evaluation.status == "completed"`, even when some criteria inside it carry the `error` flag (`scripts/control_run.py` `not_completed` is stricter and re-runs those).
- `resumable` is a cheap heuristic; the resume endpoint decides (409 `nothing_to_resume`).
- Request counters in `/api/system/metrics.http` are per process and reset on restart; SSE streams are counted but kept out of the latency window.
- `/api/version` is resolved once at start-up (it reports the code that is loaded, not a commit made later).

## 10. Integration requests

1. **`scripts/control_run.py` (most important for the control hour; not a platform file).**
   - `run_job`: treat `interrupted` as terminal: `if j["status"] in ("completed", "failed", "cancelled", "interrupted")`. On `interrupted`, call `POST /api/jobs/{job_id}/resume` once or twice and keep polling the returned `job_id`. Today a restart or deploy during the control run makes the script poll until its 1800 s timeout.
   - Before starting: `POST /api/evaluations/run` with `"dry_run": true`. If `guard` is not null, print `guard["message"]` and stop (or raise `DAILY_BUDGET_USD` / wait), so a refused run is found before the critical moment.
   - On a non-200 from run/resume, print `r.json()["error"]["message"]` (Russian, with numbers) instead of the raw body. For `too_many_jobs`, wait 30 s and retry.
2. **Deploy (`docs/HOSTING.md`, Railway).**
   - Before the control hour, check `GET /api/system/health` → `budget.remaining`. At 18:35 the hosted instance had spent $16.22 in 24 h of the default $75; four or five full DEV runs (about $13 each) in the last hours would exhaust it and the control run would get 429. Raise `DAILY_BUDGET_USD` (or set `0`) if needed. Avoid parallel runs during the control hour (`MAX_CONCURRENT_JOBS=2`).
   - Set `APP_VERSION` at deploy time, e.g. `railway variables --set APP_VERSION=$(git rev-parse --short HEAD)` before `railway up`. CLI uploads carry no `RAILWAY_GIT_COMMIT_SHA` and the image has no `.git`, so `/api/version` would otherwise say `0.1.0` (`source: package`).
   - Do not redeploy during the control run. A restart now leaves an `interrupted` job that `resume` continues, but the Claude spend of evaluations in flight is lost.
   - Optional: add `--no-access-log` to the uvicorn start command. `cqa.access` already logs every request with duration, role and request id.
3. **`cqa/main.py` (integration step, optional).** In the `Exception` handler, add `"request_id": getattr(request.state, "request_id", None)` to the 500 envelope. On the public instance, consider replacing `str(exc)` in `message` with a generic Russian message, since the full error is logged anyway. Keep `cqa.platform.requestlog` in `INSTALLERS`; it positions itself outermost by itself, so there is no need to reorder.
4. **`docs/API.md` (integration step).** Add §3 and §5 of this file: the new endpoints, `budget`/`guard` on run and dry run, the 429 codes, job status `interrupted`, the `resumable` field, `job.done` for interrupted jobs on SSE, and `X-Request-ID` on every response.
5. **`cqa/judge/runner.py` `estimate_cost` (judge or testing owner).** Return 0 for provider `fake`. It currently falls back to the Claude price, so offline runs count against the budget guard.
6. **Frontend (`web/`, the user).**
   - `useJobProgress` `TERMINAL`: add `"interrupted"`. SSE now sends `job.done` for it, but the polling fallback would poll forever.
   - Show «Возобновить» when `job.resumable` → `POST /api/jobs/{id}/resume`, then follow the returned `job_id`.
   - Dry run: show `estimated_cost_usd` against `budget.remaining`; when `guard` is not null, show `guard.message` and disable «Запустить».
   - Error toasts: show `error.message`, plus the `X-Request-ID` response header (now exposed via CORS) for support.
   - Optional status page: `/api/system/health` (`warnings[]` are ready-to-show Russian messages) and `/api/system/metrics`.
