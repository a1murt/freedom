# CQA architecture v2: the system as built

Status: 2026-11-07, after the architecture phase (eight backend packages plus the integration step). [ARCHITECTURE.md](ARCHITECTURE.md) is the original design spec and stays the reference for the judge design, the checklist schema and the scoring rules. This document describes what runs today. Endpoint details: [API.md](API.md) (section "Architecture phase additions" for everything new). Package notes: `docs/arch/*.md`. Contracts: [ARCH_CONTRACTS.md](ARCH_CONTRACTS.md).

Team: «Я Тебя Могну». Frontend (web/) is owned and being redesigned by the team lead; this document lists what the backend offers it in §15.

---

## 1. Component diagram

```
  Browser                          Dynamics 365 Contact Center               Laptop (control hour)
  CQA Web (React + Vite,           web resource (single-file build,          scripts/control_run.py
  hash router, RU/EN/KK)           ?embed=1&conversation=<id>)               cqa.export.results_json CLI
        │  role picker → X-CQA-Role / X-CQA-User      │ Power Automate: Notes trigger →         │
        │                                             │ POST /api/integrations/dynamics/...     │
        └──────────────────── REST + SSE (JSON), X-Request-ID ───────────────────────────────────┘
                                             │
┌────────────────────────────────── CQA API · FastAPI · one uvicorn process ───────────────────────────────────┐
│ request pipeline (§3): requestlog → ServerError(500) → CORS → security (actor, permissions, audit) → handlers │
│                                                                                                               │
│ routers   conversations · checklists · evaluations/jobs · appeals · settings · audio · analytics · export    │
│           insights (overview, operator scorecards, violations) · system · admin · integrations · calibration │
│           audit · roles                                                                                       │
│ services  ingest (adapter, normalize) · checklist (validation, compiler, versions) · judge (runner, packer,  │
│           validate, ensemble, providers) · scoring.engine · review · jobs (runner, guards) · analytics        │
│           (service, overview, coaching, violations) · calibration · admin.snapshots · audit ·                 │
│           integrations.dynamics · export (results_json, csv, pdf) · audio (resolver, features, peaks)         │
└──────┬─────────────────────┬────────────────────────┬────────────────────┬─────────────────┬─────────────────┘
       │                     │                        │                    │                 │
 SQLite (WAL) or        Claude API               OpenAI-compatible     fake judge        Organizer API
 Postgres               Opus 5.5 (judge)         local model           (offline:         (Power Automate)
 DATA_DIR/cqa.db        Sonnet 5.5 (compiler,    (Ollama, vLLM)        tests, CI,        + Dynamics MP3
 DATA_DIR/snapshots/    ensemble member)                               keyless demos)    (resolved, cached)
 DATA_DIR/audio/
```

Hosted: API on Railway (Docker, SQLite on a volume), web on Vercel. Local: `docker compose up` (API 8000, web 5173) or `uvicorn --reload` + `pnpm dev`.

## 2. Code map

| Area | Modules | Package note |
|---|---|---|
| App, settings, DB | `cqa/main.py`, `cqa/config.py`, `cqa/db.py` (engine, `init_db`, additive migrations), `cqa/models.py`, `cqa/serializers.py` | this file |
| Platform | `cqa/platform/requestlog.py`, `cqa/jobs/guards.py`, `cqa/jobs/runner.py`, `cqa/routers/system.py`, `cqa/routers/evaluations.py` | [platform](arch/platform.md) |
| Roles and audit | `cqa/security/{roles,middleware}.py`, `cqa/audit/service.py`, `cqa/routers/{roles,audit,appeals}.py` | [roles](arch/roles.md) |
| Checklists | `cqa/checklist/{validation,service,compiler}.py`, `cqa/routers/checklists.py` | [checklists](arch/checklists.md) |
| Judge | `cqa/judge/{runner,packer,validate,ensemble,contract,pii,reference}.py`, `cqa/judge/providers/{anthropic_provider,openai_compatible,fake,base}.py`, prompts in `cqa/judge/prompts/v1/` (ML teammate) | [ARCHITECTURE §7](ARCHITECTURE.md), [testing](arch/testing.md) |
| Scoring and review | `cqa/scoring/engine.py`, `cqa/review/service.py` | [ARCHITECTURE §5.4, §10](ARCHITECTURE.md) |
| Analytics | `cqa/analytics/{service,overview,coaching,violations}.py`, `cqa/routers/{analytics,insights}.py` | [overview](arch/overview.md) |
| Admin | `cqa/admin/snapshots.py`, `cqa/routers/admin.py` | [admin](arch/admin.md) |
| Integrations | `cqa/integrations/dynamics.py`, `cqa/routers/integrations.py` | [integrations](arch/integrations.md), [INTEGRATION_D365](INTEGRATION_D365.md) |
| Calibration | `cqa/calibration/service.py`, `cqa/routers/calibration.py`, `cqa/eval/harness.py` (CLI) | [calibration](arch/calibration.md) |
| Export | `cqa/export/{results_json,csv_export,pdf_export}.py`, `cqa/routers/export.py` | [ORGANIZER_UPDATES §1](ORGANIZER_UPDATES.md) |
| Ingest, audio | `cqa/ingest/{adapter,normalize,service}.py`, `cqa/audio/*`, `cqa/routers/{conversations,audio}.py` | [DATASET_NOTES](DATASET_NOTES.md) |
| Control hour | `scripts/control_run.py` | §9–§10 |

## 3. Request pipeline

Order in `cqa.main.app` (outermost first). Every layer is pure ASGI (`scope/receive/send`), never `BaseHTTPMiddleware`, so the SSE stream `GET /api/jobs/{id}/events` and ranged audio `GET /api/audio/{id}` stream byte for byte.

```
client ─▶ RequestLogMiddleware      request id (valid X-Request-ID or 12 hex), scope.state.request_id, context var;
          (cqa.platform.requestlog)  X-Request-ID on every response (+ Access-Control-Expose-Headers); one access line
                                     per request on logger cqa.access; in-process counters for /api/system/metrics
       ─▶ ServerErrorMiddleware     unhandled exceptions → 500 envelope {code: internal, request_id, type, detail}
                                     (CORS headers added by the handler itself)
       ─▶ (FastAPI telemetry layer)
       ─▶ CORSMiddleware            CORS_ORIGINS (default ["*"]), any header (X-CQA-*, X-Request-ID)
       ─▶ SecurityMiddleware        actor from X-CQA-Role / X-CQA-User → scope.state.actor (400 unknown_role);
          (cqa.security.middleware)  GET/HEAD/OPTIONS pass untouched; writes: ROUTE_MAP rule → 403 forbidden or run with
                                     a body tee + response tap; one audit_events row right before the last body chunk
                                     (2xx and 403 only)
       ─▶ ExceptionMiddleware       HTTPException → envelope; RequestValidationError → 422 validation_error {errors[]}
       ─▶ AsyncExitStack ─▶ routers (handlers read request.state.actor / current_actor(request))
```

Placement: `cqa.platform.install(app)` runs the installers listed in `cqa/platform/__init__.py` (`requestlog`, then `security.middleware`). The request log wraps the built stack by overriding `app.build_middleware_stack`, so it is outermost no matter what is added later; the security middleware is appended innermost; `main.py` then adds CORS, which Starlette puts outside the security middleware. Result: 400/403 answers of the security middleware carry CORS headers and a request id, and are logged. Covered by `test_middleware_stack_order_of_the_real_app` and the ASGI-level streaming tests.

Start-up (lifespan): `init_db()` (create tables, additive columns, indexes, Postgres widening) → `ensure_seed()` (organizer checklist v1, judge specs) → `cqa.jobs.runner:recover_stale_jobs` (§8).

## 4. Data model

SQLAlchemy 2 models in `cqa/models.py`; schema changes are additive only (`cqa.db.ADDITIVE_COLUMNS`, `ADDITIVE_INDEXES`, `WIDENED_COLUMNS`, applied by `init_db` at every start and after a snapshot restore).

| Table | Key columns | Notes |
|---|---|---|
| `datasets` | id, group_id, source (`organizer_api`, `dynamics365`, upload), imported_at, record_count | one row per import; `D365` collects Dynamics pushes |
| `conversations` | id (source id), dataset_id, channel (`agent chat` / `voice call`), language (RU/KZ/EN), audio_url, operator_name, **agent_name**, turn_count, first_response_seconds, terminal_event, meta JSON | `operator_name` is detected from the greeting and fed to the judge; `agent_name` is the source-system identity (D365) and never fed to the judge |
| `turns` | conversation_id, idx, timestamp_raw, offset_seconds, speaker (client/operator/bot/system), text, is_duplicate, segment (pre/operator/internal/post) | unique (conversation_id, idx) |
| `checklists` | id, name, description | |
| `checklist_versions` | checklist_id, version_no, is_active, change_note, scoring_rules JSON (incl. `export_ids`), sections JSON, created_from_version_id | one active version per checklist |
| `criteria` | checklist_version_id, code, order, section, title, instruction, answer_type (binary/scale), options JSON, max_points, weight, na_allowed, na_condition, is_critical, critical_value, channel_overrides JSON, judge_spec JSON | |
| `evaluations` | conversation_id, checklist_version_id, status (queued/running/completed/failed), provider, mode (single/ensemble/partial), model, prompt_version, total_score, final_total_score, critical_error, sections JSON, cost_usd, tokens, latency_ms, job_id, error | immutable AI verdict; reviews write `final_*` |
| `evaluation_criteria` | evaluation_id, criterion_id, code, applicable, na_reason, answer, score, max_points, weight, weight_applied, earned, confidence, explanation, evidence JSON, checked_span JSON, flags JSON, claims JSON, votes JSON, review_state, final_answer, final_score, final_earned | flags include `copied_from_previous_version`, `error`, `fake_judge` |
| `appeals` | evaluation_criterion_id, comment, author_label, author_role, author_name, status (open/decided) | |
| `decisions` | appeal_id (unique), action (confirm/change), new_answer, new_score, reason, reviewer_label, reviewer_role | |
| `llm_traces` | evaluation_id, evaluation_criterion_id, criterion_code, kind (judge/retry/arbiter/compiler), provider, model, prompt_version, request JSON, response JSON, tokens, cache_read_tokens, cost_usd, latency_ms | proof of work per call |
| `audio_assets` | conversation_id, local_path, duration_s, fetched_at, features JSON | MP3 cache in `DATA_DIR/audio` |
| `jobs` | id (`j_…`), type, status (queued/running/completed/failed/cancelled/**interrupted**), total, done, failed, cost_usd, params JSON (run parameters, `estimated_cost_usd`, `resumed_from`, `resumed_as`, `source`), log JSON | |
| `fewshot_examples` | criterion_code, language, channel, transcript_excerpt, verdict JSON, source (review/gold) | written by reviewer decisions |
| `settings` | key, value JSON | runtime overrides (provider, model, local endpoint, team name, `default_checklist_id`, …) |
| `audit_events` | ts, actor_role, actor_name, action, entity_type, entity_id, summary, details JSON (≤ 16 KB), request_id (64), status_code | append-only; index (entity_type, entity_id) |
| `gold_labels` | conversation_id, criterion_code (C1…, `total`, `_note`), value JSON, labeler (`team` = reconciled), note, split (train/validation) | unique (conversation_id, criterion_code, labeler) |

Invariants: an evaluation is never overwritten (re-evaluation = new row; the card shows the latest, with history); totals are computed only by `cqa.scoring.engine` (N/A weight redistribution, half-up two decimals); `final_total_score` is recomputed from final answers after every decision and when a partial re-evaluation carries decisions.

Files next to the database: `DATA_DIR/snapshots/<name>.db|.json` (admin snapshots), `DATA_DIR/audio/` (MP3 cache), `DATA_DIR/logs/`.

## 5. Evaluation pipeline

```
POST /api/evaluations/run ─▶ estimate + guards (under jobs.start_lock) ─▶ Job row (queued, params) ─▶ worker thread
   (own asyncio loop, ≤ 3 dialogs in parallel) ─▶ evaluate_conversation per dialog:
      runtime settings (env + settings table) ─▶ conversation_pack (turns, PII masked unless local, structural facts,
      acoustic summary) ─▶ provider members ─▶ partial: base rows of the base version's latest completed evaluation
      ─▶ per criterion (≤ 8 in parallel): build_bundle (transcript first, compiled judge_spec second, few-shots,
         reference facts) ─▶ provider.complete (structured output) ─▶ validate_verdict (evidence against the
         transcript, hard N/A rules) ─▶ one retry with the issues ─▶ ensemble vote ─▶ arbiter on disagreement
      ─▶ rows + llm_traces ─▶ compute_score (weights normalised to 100, N/A redistributed, critical flagged apart)
      ─▶ carried decisions → review.recompute_final ─▶ commit
   ─▶ job progress row + in-memory events ─▶ SSE: evaluation.started, criterion.done, evaluation.done, job.progress,
      job.status, exactly one job.done
```

Providers (`cqa.judge.providers.get_provider`, routed by `cqa.judge.runner._members`):

| provider | model | Use |
|---|---|---|
| `anthropic` | `claude-opus-5-5` (default), any `claude-*` via `model` | primary judge, adaptive thinking, effort high, structured outputs |
| `ensemble` | Opus, Opus, Sonnet (+ the local model when configured) + arbiter | control-run option, ≈ 2.5× cost |
| `local` (`ollama`, `vllm`, `openai_compatible`) | `LOCAL_MODEL` (default `qwen3:8b`) at `LOCAL_ENDPOINT` | local / data-sovereign option, cost 0, transcripts not PII-masked |
| `fake` | `fake-judge` | offline deterministic double (keyword rules over the real prompt, valid `Verdict`); tests, CI, keyless demos and free rehearsals; cost 0; never for organizer results |

Partial re-evaluation (the jury's "replace one criterion and apply"): the diff between versions gives `rejudge_codes` (added criteria and criteria whose judge-relevant fields changed); only those are judged, other rows are copied with `copied_from_previous_version` (flag once), rows that failed in the base are judged again, reviewer decisions (`confirmed`/`changed`) on copied criteria are carried, totals and sections are recomputed with the new weights. A weight-only change makes no model call (estimate $0). Checked live: C9 added → only C9 traced, 45.00 → 51.11.

Single-criterion trial: `POST /api/checklists/test-criterion` runs the same path (`conversation_pack`, `_criterion_pack`, `judge_criterion`) on one dialog for a criterion that is not saved yet, compiles a judge_spec if needed, persists nothing; the returned spec is saved with the version so the tested procedure is the one used.

Appeals: `POST /api/appeals` → `review.create_appeal` (row `under_review`) → `POST /api/appeals/{id}/decision` → `review.decide` (final answer, `recompute_final`, optional few-shot example).

## 6. Roles and permissions

No login: the UI declares the actor in headers (`X-CQA-Role`, URL-encoded `X-CQA-User`); no header = `demo` with every permission, so the deployed web app keeps working unchanged. The source of truth is `cqa/security/roles.py` (`ROLES`, `PERMISSIONS`, `ROUTE_MAP`); the matrix is in API.md and in `GET /api/roles`.

- Reads (GET) are open to every role, including `visitor`; writes need the rule's permission (403 `forbidden`, audited).
- `operator` appeals only dialogs whose `operator_name` or `agent_name` is the header name (403 `not_your_dialog`, `operator_name_required`).
- Handler-level checks for sensitive reads: snapshot download and audit CSV need `admin`.
- Optional shared secrets for a public instance: `CQA_ADMIN_TOKEN` (snapshot writes and downloads), `INTEGRATIONS_API_KEY` (Dynamics push).
- `GET /api/roles` also gives the UI per role: labels RU/EN/KK, `home`, ordered `nav` (director/lead → `#/overview`, operator → `#/my`, reviewer → `#/review`, methodologist → `#/checklists`, admin → `#/settings`), and `write_routes` to hide actions.

This is attribution plus guardrails, not authentication: the headers are self-declared.

## 7. Audit trail

- Writer 1, the security middleware: every write under `/api/` that ends 2xx, and every 403. Row: actor, action from the route map (refined: `evaluation.estimate`, `calibration.labels.preview`, `integration.dynamics.ingest_evaluate`), entity from path/response/body, Russian summary, compact details (path params, query, body summary ≤ 2 KB, selected response fields, error code/message), request id, status.
- Writer 2, services via `cqa.audit.record(db, action=…)`: `job.interrupted` (role `system`, start-up recovery), `snapshot.download`.
- Privacy: transcripts, `turns`, `messages`, Dynamics `documentbody` and `participants` are reduced to their size; secret-looking keys, `sk-…` keys, bearer tokens, URL passwords and signed-URL parameters are masked; headers are never stored.
- Read: `GET /api/audit` (filters, prefix search, `q`), `/api/audit/facets`, `/api/audit/export.csv`.
- A failing audit write is logged and never changes the response. A snapshot restore rewinds the trail with the database (the restore itself is recorded in the restored database).

## 8. Security and cost guards

| Guard | Where | Behaviour |
|---|---|---|
| Daily budget | `cqa.jobs.guards.check_run` / `check_budget` | 24 h spend = `Evaluation.cost_usd` of evaluations created in the window (all statuses) + single-criterion trial costs from the audit trail; plus reservations of queued/running jobs (`estimated_cost_usd − cost so far`); + this estimate > `DAILY_BUDGET_USD` (75, 0 = unlimited) → 429 `budget_exceeded` with `retry_after_s`. $0 runs (local, fake) are never blocked. Applies to runs, resumes, Dynamics `evaluate`, trials |
| Concurrency | same | `MAX_CONCURRENT_JOBS` (2) queued/running jobs → 429 `too_many_jobs`; "check, then create the job" runs under `jobs.start_lock` |
| Run size | same | `MAX_DIALOGS_PER_RUN` (200) → 429 `too_many_dialogs` |
| Dry run first | `POST /api/evaluations/run {dry_run: true}` | always answers with `estimated_cost_usd`, `budget` and `guard` (what a real run would get); `scripts/control_run.py` stops on a guard before spending |
| Restart recovery | `recover_stale_jobs` at start-up | queued/running jobs not alive in this process → `interrupted` (log + audit); running evaluations → `failed` ("interrupted by restart"); idempotent, never raises |
| Resume | `POST /api/jobs/{id}/resume` | re-queues the dialogs without a completed evaluation made since the job started; 409 when active, already resumed or nothing left; guards apply |
| Crashed worker | `jobs.runner._worker` | job → `failed` + `job.done` instead of a job stuck in `running` (which would block the concurrency guard) |
| Snapshots | `cqa.admin.snapshots` | restore needs `confirm: <name>`, takes a pre-restore snapshot first, refuses corrupt files and live jobs (409), copies in place (same inode), 15 s lock bound, protected `baseline`, name pattern (no path traversal), disk check (507) |
| Ingest | Dynamics router | 413 above 8 MB, conflict with non-D365 dialogs (409 unless `overwrite`), dedupe of unchanged transcripts, optional integration key |
| PII | judge runner | transcripts are PII-masked for cloud providers (not for `local`) |
| Error hygiene | `main.py` 500 handler | generic Russian message + request id; the exception text is scrubbed of keys and tokens |

## 9. Results submission flow (organizer JSON 1.0)

```
dialogs in the DB ─▶ latest completed evaluation per dialog for the organizer version (v1) ─▶ build_record:
  criterion ids from the version's export ids (organizer ids for v1), Да/Нет → true/false, N/A → null, scale → raw step,
  1-based evidence_turns of the original transcript, final_score recomputed (N/A redistributed, half-up),
  critical_flag = value of "critical"; a dialog that could not be evaluated → status "error", criteria [], score null
─▶ validate_document (8 ids exactly once, value domains, evidence bounds, counts, duplicates, team_name, model block)
─▶ GET /api/export/results.json (file, X-Results-Valid/Errors/Warnings) · GET /api/export/results/validate (report)
   · python -m cqa.export.results_json --api <url> | --db  --group <id> --out … --expect N [--answers final]
```

- `team_name` = `TEAM_NAME` / settings («Я Тебя Могну»); `model` lists every model used, `mode` `external_api` / `local` / `hybrid`.
- `answers=ai` (default) exports the judge's answers; `answers=final` (opt-in) exports reviewer decisions with the reviewer's reason in the rationale. The team decides which one to submit.
- `results/dev/results.json`: 65 DEV records; `results/prod/results.json`: exactly 10 control chats, written by the control run.

## 10. Control-hour runbook

Before (H+22):

1. `GET <API>/api/system/health`: `ok`, `providers.anthropic: true`, `budget.remaining` ≥ 3× the control estimate (10 chats ≈ $1.80 single Opus, ≈ $4 ensemble). If not, raise `DAILY_BUDGET_USD` on Railway (or `0`) and redeploy now, not later.
2. `GET <API>/api/version`: the build you expect (set `APP_VERSION` at deploy).
3. Snapshot: the control script takes `pre-control-<UTC>` itself; a `baseline` of the populated instance should already exist (`POST /api/admin/snapshots {"name": "baseline"}`).
4. Rehearse locally, for free, against a scratch copy of the database (never the hosted instance: fake verdicts would become the latest evaluations):
   `DATABASE_URL=sqlite:////tmp/cqa-rehearsal/cqa.db DATA_DIR=/tmp/cqa-rehearsal uvicorn cqa.main:app --port 8019` and
   `api/.venv/bin/python scripts/control_run.py --api http://127.0.0.1:8019 --rehearse --provider fake`.
   A paid rehearsal on the hosted API: `--rehearse` (10 DEV chats, no git, ≈ $2).
5. Freeze the judge (provider, model, prompt version) per `docs/HANDOFF_ML.md`; avoid other runs during the hour (`MAX_CONCURRENT_JOBS=2`).

During (H+23):

6. `api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> [--provider ensemble]` (or `--source-url` / `--source-file`). It: checks health and the organizer version → snapshot `pre-control-…` → imports → dry run (stops with the guard's message and prints the `--skip-import --ids …` line to continue) → runs (waits on 429 `too_many_jobs`) → follows the job, keeps polling through an API restart, resumes `interrupted` jobs → re-runs dialogs that did not complete → exports `results/prod/results.json` → validates (exactly 10) → `git add/commit/push`.
7. If anything stops it: fix the cause, then `--skip-import --ids <ids>`; the partial file is always written.
8. Do not redeploy during the run. If Railway restarts anyway, the job becomes `interrupted` and the script resumes it (checked live: API killed mid-job → resumed → VALID).
9. The organizer's criterion replacement afterwards: Checklist Studio (or the API) → test the new criterion on one dialog (`test-criterion`) → new version with the tested spec → partial re-evaluation of the control dialogs (`mode: partial`, `base_version_id` = organizer version). results.json stays on the organizer version unless the new criterion is mapped with `scoring_rules.export_ids`.
10. H+23:50: confirm the pushed file opens on GitHub; stop coding.

## 11. Deployment

| Target | How | Data |
|---|---|---|
| Railway (API) | `api/Dockerfile` (python:3.12-slim, ffmpeg, uv), `api/railway.json` (start `uvicorn cqa.main:app --host 0.0.0.0 --port $PORT`, health check `/api/health`, restart on failure); one process | volume at `/app/data`: `cqa.db`, `snapshots/`, `audio/` |
| Vercel (web) | `cd web && vercel --prod --yes -e VITE_API_URL=https://<railway-domain>` | static |
| Local Docker | `docker compose up --build` (API 8000, web 5173, nginx proxies `/api`) | volume `cqa-data` |
| Local dev | `cd api && uv run uvicorn cqa.main:app --reload` + `cd web && pnpm dev` | `api/data/cqa.db`; every code save restarts the API and interrupts its running jobs (they become `interrupted`, resumable) |

Environment (all optional except the key; `.env` in the repo root or `api/`): `ANTHROPIC_API_KEY`, `ORGANIZER_API_URL`, `DATABASE_URL` (default `sqlite:///api/data/cqa.db`; Railway `sqlite:////app/data/cqa.db`), `DATA_DIR`, `DEFAULT_PROVIDER`, `DEFAULT_MODEL`, `SECONDARY_MODEL`, `LOCAL_ENDPOINT`, `LOCAL_MODEL`, `LOCAL_API_KEY`, `PII_MASKING`, `EXPLANATION_LANGUAGE`, `CORS_ORIGINS` (JSON list; a restricted instance must include the web origin and `https://freedomholdingcorporation.crm11.dynamics.com`), `DAILY_BUDGET_USD`, `MAX_DIALOGS_PER_RUN`, `MAX_CONCURRENT_JOBS`, `APP_VERSION`, `TEAM_NAME`, `DEFAULT_CHECKLIST_ID`, `WEB_BASE_URL`, `PUBLIC_API_URL`, `INTEGRATIONS_API_KEY`, `CQA_ADMIN_TOKEN`, `SNAPSHOT_KEEP_PRE_RESTORE`.

Deploy notes: set `APP_VERSION=$(git rev-parse --short HEAD)` before `railway up` (CLI uploads carry no git SHA, so `/api/version` would say `0.1.0`); after the first deploy of this build `init_db` adds `conversations.agent_name` and the audit index by itself; optional `--no-access-log` in the start command (`cqa.access` already logs every request with duration, role and request id). Postgres works for everything except snapshots (501, use provider backups).

## 12. Observability

- **Request id**: `X-Request-ID` on every response, in the access log, in the audit row and in the 500 envelope; clients can send their own.
- **Access log** (`cqa.access`): `POST /api/evaluations/run 429 1.9ms role=analyst rid=1fd12a39f4e3`, plus `error=<Exception>` for crashes.
- **`GET /api/system/health`**: database (latency, size, WAL, counts), providers (Claude key, local endpoint probe cached 30 s), jobs (running, queued, interrupted, live workers), disk, budget, snapshots, Russian `warnings[]` ready to show (`budget_low`, `jobs_orphaned`, `local_unreachable`, …).
- **`GET /api/system/metrics?since_hours=`**: evaluations by status, latency percentiles, cost per completed evaluation, tokens, per provider/model, appeals, jobs, in-process HTTP counters.
- **`GET /api/version`**: the build that is loaded.
- **Per verdict**: `GET /api/evaluations/{id}/trace` (model, prompt version hash, request, response, tokens, cost, latency of every call).
- **Per job**: `GET /api/jobs/{id}` log entries and the SSE stream.
- **Audit**: who did what, filterable.

## 13. Testing and CI

`cd api && .venv/bin/python -m pytest -q` (or `uv run --extra dev pytest -q`): **539 tests, about 8 s, offline**. No test calls Claude (keys cleared, providers stubbed or the `fake` judge) or opens `api/data/cqa.db` (in-memory SQLite with `StaticPool` + `dependency_overrides[get_db]`, temp files, or a subprocess with its own `DATABASE_URL`).

| File | Tests | Covers |
|---|---|---|
| `test_checklist_governance.py` | 88 | validation (48 invalid cases), weights, export ids, spec reuse, trial, partial re-evaluation incl. the four runner fixes |
| `test_admin.py` | 73 | snapshot create/restore/list/delete/download, CLI, guards, hooks |
| `test_dynamics.py` | 58 | transcript parsing (18 documentbody variants), ids, language, channel, endpoint, guards, permissions, manifest |
| `test_roles_audit.py` | 55 | actor resolution, permissions, appeals authorship, audit capture and privacy, streaming untouched, route-map coverage of every write route |
| `test_platform.py` | 53 | guards, recovery, resume, version/health/metrics, request log, SSE through the middleware |
| `test_providers.py` | 48 | fake judge, OpenAI-compatible adapter, Claude adapter error mapping |
| `test_overview.py` | 33 | executive overview and scorecards |
| `test_calibration.py` | 25 | gold labels, agreement (parity with the harness), stability |
| `test_integration.py` | 24 | middleware order, error envelopes, fake routing and $0 estimates, trial spend in the budget, default checklist, results export ids and `answers=final`, audit privacy and route refinements, D365 identity in appeals, ISO chat offsets, admin settings, `control_run.py` resume/poll/busy/guard |
| `test_e2e_flow.py` | 12 | the whole process in a subprocess with the fake judge: import → evaluate (job + SSE) → card → operator appeal → reviewer decision → violations/analytics/CSV/PDF → checklist v2 → partial → results.json → audit |
| others | 47 | scoring engine, normalizer, audio, analytics/export, results.json, violations |

CI: `.github/workflows/ci.yml` (`api`: ffmpeg, uv, pytest, no secrets; `web`: typecheck + build, non-blocking during the redesign).

Live verification (integration step, real Claude on a copy of the local database, ≈ $0.8): see [arch/integration.md §4](arch/integration.md).

## 14. Dynamics 365 integration

```
D365 Omnichannel conversation ends ─▶ transcript note (annotation, objecttypecode msdyn_transcript, base64 documentbody)
─▶ Power Automate "When a row is added" on Notes ─▶ HTTP POST /api/integrations/dynamics/transcripts
   (X-CQA-Role: analyst, optional X-CQA-Integration-Key, {"annotation": {...}, "evaluate": true})
─▶ decode (base64/gzip/UTF-16 variants) ─▶ map speakers (customer/agent/bot/system, private notes → [internal])
─▶ language (KZ letter share) and channel detection ─▶ upsert into dataset D365 (normalizer: segments, duplicates,
   ISO offsets, first response, greeting-detected operator_name; agent_name = D365 display name)
─▶ with evaluate: dedupe by content hash ─▶ guards ─▶ job ─▶ card_url / embed_url in the answer
─▶ the agent opens the CQA tab on the Conversation form: web resource = single-file build of the web app in embed mode
```

`GET /api/integrations/dynamics/manifest` gives the admin everything to paste (build command, `CQA_CONFIG` snippet, loader, form tab steps, flow body, a working example payload). Admin guide: [INTEGRATION_D365.md](INTEGRATION_D365.md). Checked live with the manifest's synthetic payload (7 turns, offsets from ISO timestamps, fake evaluation for $0, unchanged re-push skipped, audit per conversation without the transcript).

## 15. Frontend integration checklist (owner: the team lead, web/)

Everything below is optional for backward compatibility (no headers = demo) but needed to show the new features:

1. Send `X-CQA-Role`, `X-CQA-User: encodeURIComponent(name)` and optionally `X-Request-ID` on every request; build the role picker, home route and navigation from `GET /api/roles`; current permissions from `GET /api/me`; hide writes the role lacks (`write_routes`). Stop sending `author_label` / `reviewer_label` once a role is chosen.
2. Jobs: treat `interrupted` as terminal in the polling fallback; show «Возобновить» when `job.resumable` (`POST /api/jobs/{id}/resume`, then follow the returned `job_id`).
3. Dry runs: show `estimated_cost_usd` against `budget.remaining`; when `guard` is set, show `guard.message` and disable «Запустить».
4. Error toasts: `error.message` (Russian), plus the `X-Request-ID` header; 422 `errors[]` next to the fields.
5. Checklist Studio: `weights.total` / `normalized` and "normalise to 100", `errors[]` per row, `warnings` after save, «Сохранить как черновик» = `activate: false`, «Проверить на диалоге» = `test-criterion` (then save the returned `judge_spec`), export/import via `…/export?download=true` and `POST /api/checklists/import` (keep `judge_spec`), the gross-error toggle sets weight 0.
6. Overview page (`GET /api/overview`) as the director/lead home; operator portal `#/my` with `GET /api/operators/{X-CQA-User}`; violations page (`GET /api/violations`).
7. Audit page (`/api/audit`, `/api/audit/facets`, CSV for admin); a card's history via `?q=<conversation_id>`.
8. Admin page: snapshots list/create/restore (typed confirm, show `message`, offer undo with `safety_snapshot`), download link, `X-CQA-Admin-Token` prompt when `token_required`; status from `/api/system/health` `warnings[]`.
9. Calibration page: labeling grid (`GET/PUT /api/calibration/labels`), import with dry-run preview, export, agreement table with confusion matrices, disagreements linking to the card, stability.
10. Dynamics: `web/src/lib/config.ts` could read the D365 web-resource parameters `id` and `data` so the single-file build works without the snippet; optional Settings → Integrations panel from the manifest.
11. Results: "Export results.json" may offer `answers=final` once the team decides.

## 16. Known limitations

- Soft permissions, not authentication; no header means `demo` with every permission.
- One API process per database: the start lock, the job registry, recovery and `jobs_running` are per process (Railway runs one).
- Budget accounting misses the spend of evaluations killed mid-flight and of trial spec compiles; estimates are fixed per-dialog constants ($0.18 per chat, $0.35 per call for Opus).
- A restore rewinds the whole database, audit trail included; snapshots hold the database only (audio is re-fetched); SQLite only.
- Open appeals stay on the evaluation they were filed on; a later partial re-evaluation does not see them until decided (decided ones are carried).
- `read` is global: any role can read every dialog and scorecard.
- The `fake` judge is a plumbing double (keyword rules), never a quality judge.
- Overview and analytics aggregate in memory (fine for thousands of evaluations; no cache).
- Dynamics voice transcripts stored as files are not parsed; not validated against a real D365 org.
- The hosted instance runs the previous build until the next deploy.
