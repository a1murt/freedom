# Integration step: wiring the eight packages together

Owner: integration engineer (architecture phase). Inputs: the eight package reports and `docs/arch/{platform,roles,checklists,overview,admin,integrations,calibration,testing}.md`. The system as built is described in [ARCHITECTURE_V2.md](../ARCHITECTURE_V2.md); the API additions are in [API.md, "Architecture phase additions"](../API.md#architecture-phase-additions).

Rules kept: nothing under `web/` was edited, no git command was run, nothing was deployed, the hosted API received no request at all, judge prompts and `api/seeds/judge_specs_v1.json` are untouched, tests never call Claude and never open `api/data/cqa.db`.

## 1. Result

| | Before | After |
|---|---|---|
| Full suite (`cd api && .venv/bin/python -m pytest -q`) | 508 passed, 6 xfailed, 1 warning | 539 passed, 0 xfailed, 0 warnings (also with `-W error::UserWarning`) |
| Strict xfails documenting runner bugs | 4 (checklists) + 2 (providers) | 0: all six fixed and running as normal tests |
| `fake` provider over the API | routed to Claude (test shim + warning) | routed natively, estimated at $0 |
| OpenAPI generation | duplicate operation id warning (audio GET/HEAD) | clean |

New test file: `api/tests/test_integration.py` (24 tests). Extended: `test_platform.py` (+1 resume regression), `test_dynamics.py` (`agent_name`, `fake` accepted), `test_e2e_flow.py` (reviewer decision carried into the partial re-evaluation, final total 71.43), `test_roles_audit.py` (request id column is now 64 characters).

## 2. Cross-package wiring (verified, not changed)

- **Middleware order** in `cqa.main.app`: `RequestLogMiddleware` (installed by `cqa.platform`, wraps the whole stack) → Starlette `ServerErrorMiddleware` → `CORSMiddleware` → `SecurityMiddleware` → `ExceptionMiddleware` → routes. All pure ASGI; FastAPI's own telemetry and exit-stack layers sit in between. Tested by walking the built stack (`test_middleware_stack_order_of_the_real_app`).
- **Actor and request id** in handlers: `request.state.actor` / `cqa.security.roles.current_actor(request)` and `request.state.request_id`; the audit row's `request_id` equals the `X-Request-ID` response header.
- **Audit on every write route**: the roles package's coverage test walks the real app's OpenAPI schema; every POST/PUT/DELETE has its own rule.
- **Guards** sit in every paid path: `POST /api/evaluations/run`, `POST /api/jobs/{id}/resume`, `POST /api/checklists/test-criterion` (now the shared `guards.check_budget`), Dynamics ingest with `evaluate`.
- **Start-up**: `init_db` (+ additive migrations) → `ensure_seed` → `cqa.jobs.runner:recover_stale_jobs`.
- **Routers**: all 15 modules in `main.py`'s list load (71 operations).

## 3. Fixes and integration requests applied

### Runner (checklists requests 1–4, testing requests 1–2)

| # | File | Change | Test (formerly) |
|---|---|---|---|
| 1 | `cqa/jobs/runner.py` `_run` | `only_codes=[]` stays `[]`: a weight-only partial re-evaluation re-judges nothing ($0, as the dry run says) | `test_job_runner_weight_only_change_needs_no_llm_calls` (strict xfail) |
| 2 | `cqa/judge/runner.py` | base rows flagged `error` (or applicable without an answer) are judged again instead of being copied as failures | `test_partial_rejudges_criteria_that_failed_in_the_base_evaluation` (strict xfail) |
| 3 | same | `copied_from_previous_version` appears once on chained partials | `test_chained_partials_keep_a_single_copied_flag` (strict xfail) |
| 4 | same | reviewer decisions (`confirmed` / `changed`) on copied criteria are carried (`review_state`, `final_answer`, `final_score`) and `review.recompute_final` runs before commit; open appeals (`under_review`) are not carried | `test_partial_keeps_reviewer_decisions_on_copied_criteria` (strict xfail); e2e final total 71.43 |
| 5 | same | `_members("fake")` → the offline judge; `estimate_cost` is 0 for `fake` and every local alias | e2e runs without a shim (`-W error::UserWarning`) |
| 6 | same | `conversation_pack(db, conv, provider, rs)` shared by `evaluate_conversation` and the single-criterion trial (checklists request 8) | checklist governance trial tests |

### Providers (testing request 4)

`cqa/judge/providers/openai_compatible.py`: invalid JSON → `ProviderError("Local model returned invalid JSON …")`; transport errors → `ProviderError("Local provider connection error …")` (the runner retries it); an unexpected response body → `ProviderError`. The two `xfail(strict=False)` tests are now normal tests.

### Shared files

| File | Change | Requested by |
|---|---|---|
| `cqa/main.py` | Global handlers: Starlette `HTTPException` (unknown route 404 `not_found`, 405 `method_not_allowed` with `Allow`, all in the envelope); `RequestValidationError` → 422 `validation_error` with `errors: [{field, message}]` (Russian messages for common types, field paths like `criteria[2].weight`, malformed JSON → field `body`), FastAPI's `detail` list kept next to it for old clients; 500 → generic Russian message with `request_id`, `type`, a secret-scrubbed `detail`, and CORS headers (the 500 is built outside `CORSMiddleware`, so browsers could not read it before) | checklists 6, overview 2, admin 2, platform 3 |
| `cqa/models.py` + `cqa/db.py` | `Conversation.agent_name` (source-system identity, e.g. the D365 agent; never shown to the judge); `AuditEvent.request_id` widened to 64 characters (= the request log's limit, so UUID ids are stored intact); index `ix_audit_events_entity (entity_type, entity_id)`; `init_db` adds the column and the index to existing databases and widens the column on Postgres | integrations 4, roles 4 |
| `cqa/config.py` | `default_checklist_id`, `web_base_url`, `public_api_url`, `integrations_api_key`, `cqa_admin_token`, `snapshot_keep_pre_restore` (values from `.env` now reach the routers) | checklists 7, integrations 5, admin 3 |
| `cqa/export/results_json.py` | criterion ids follow the version's export ids (`export_mapping`; organizer ids for the organizer checklist, `ExportError` when a version cannot fill all eight ids); `fake` counts as `model.mode = local`; opt-in `answers=final` (reviewer-final answers; the default stays the judge's answers); Russian error messages | checklists 5, testing 3 and 6 |

### Other packages' files

| File | Change | Requested by |
|---|---|---|
| `cqa/audit/service.py` | `documentbody` and `participants` are never stored in audit details (also when the value is an object); request id limit 64 | integrations 1 (privacy, high) |
| `cqa/security/roles.py` + `middleware.py` | Dynamics push audited per conversation (`conversation=response:conversation_id` first) with `turns, language, channel, job_id, evaluation.status`; `evaluate: true` → action `integration.dynamics.ingest_evaluate`; calibration captures widened; dry runs (body `dry_run` or the import's `?dry_run=true`) → `calibration.labels.preview`; restore captures `verified`, `counts`, `schema_sync`; refine hooks may take `(body, query)`; summaries for the new actions | integrations 2, calibration 2, admin 4 |
| `cqa/jobs/guards.py` | single-criterion trial spend (audit `checklist.criterion.test`, `details.response.cost_usd`, status 200) counts in `spent_24h`; `check_budget()` extracted and used by the trial | checklists 9 |
| `cqa/jobs/runner.py` `resume_plan` | only evaluations created since the job count as done (found by the live check, see §4) | integration step |
| `cqa/checklist/service.py` | `active_version()`: the `default_checklist_id` setting (DB, then env), else the active version of the lowest checklist id, else the newest version; the trial uses `guards.check_budget` and `judge_runner.conversation_pack` | checklists 7, 8, 9 |
| `cqa/routers/integrations.py` | `fake` accepted; `agent_name` stored and returned | integrations 4, 8 |
| `cqa/routers/appeals.py` | an operator may appeal a dialog whose `operator_name` **or** `agent_name` is theirs; Russian messages for 404/409/400 | integrations 4 |
| `cqa/routers/admin.py` | admin token and pre-restore retention also from settings | admin 3 |
| `cqa/routers/system.py` | `/api/system/health` adds `snapshots: {count, bytes, baseline_at, latest}` | admin 7 |
| `cqa/routers/evaluations.py` | exactly one `job.done` per SSE stream (the worker's own event made the closing one a duplicate; seen live) | integration step |
| `cqa/routers/export.py` | `answers` query parameter; team name from settings | testing 6 |
| `cqa/routers/settings_router.py` | `default_checklist_id` editable; team name from settings | checklists 7 |
| `cqa/routers/audio.py` | HEAD registered separately (no duplicate OpenAPI operation id) | integration step |
| `cqa/ingest/normalize.py` | chats whose timestamps are not `MM:SS` get offsets from ISO timestamps (D365); organizer chats are unchanged | integrations 3 |
| `scripts/control_run.py` | follows `interrupted` jobs with `POST /api/jobs/{id}/resume` (up to 3 times), keeps polling while the API restarts, dry-run preflight that stops with the guard's message (and the `--skip-import --ids` line to continue), waits on 429 `too_many_jobs`, prints `error.message`, snapshot `pre-control-<UTC>` before the import (`--no-snapshot`), `--provider fake` for a free rehearsal (never pushed, refused against the hosted URL), `--out` outside the repo no longer crashes | platform 1, admin 5 |

### Not applied (and why)

| Request | Decision |
|---|---|
| roles 5: `DEFAULT_ROLE` setting | Not now: the deployed web app sends no headers, so anything but `demo` would make it read-only. Add it once the web app sends `X-CQA-Role`. |
| overview 4: operators may read only their own scorecard | Not in the contract (`read` is global for every role). Left as a policy option. |
| calibration 4: "AI vs gold" KPI in the overview | Optional; needs gold labels first. `cqa.calibration.agreement(db)["overall"]` is ready for it. |
| calibration 5: harness skips `_` keys and failed rows | `cqa/eval/harness.py` is the ML teammate's working file; left to them. |
| results.json from reviewer-final answers by default | Policy: the default stays the judge's answers (what the system produced); `answers=final` / `--answers final` exists for the team to decide. |
| platform 2, admin 6: deploy notes, runbook links | Written into `docs/HOSTING.md`, `docs/DEMO.md` and ARCHITECTURE_V2 §10–§11; nothing deployed. |
| every `web/` request (roles 6, platform 6, checklists 11, admin 8, integrations 7, calibration 3) | Listed for the frontend owner in ARCHITECTURE_V2 §15. |

## 4. Live check (real Claude key, local copy of the database)

Setup: `api/data/cqa.db` copied with the SQLite backup API (read-only source, WAL content included, the original's size and mtime unchanged) to `/tmp/cqa-live/cqa.db`; `uvicorn cqa.main:app --port 8019` with `DATABASE_URL=sqlite:////tmp/cqa-live/cqa.db DATA_DIR=/tmp/cqa-live`. The server was stopped and `/tmp/cqa-live` deleted afterwards; ports 8000 and 5173 were not touched.

| Step | Result |
|---|---|
| `GET /api/version`, `/api/system/health` | `99a1c9e` (source git); health ok, Claude configured, budget $17.72 of $75 spent in 24 h, no warnings, `snapshots.count = 0` |
| `GET /api/roles`, `GET /api/me` (operator, `X-CQA-User: %D0%90…`) | 9 roles, 10 permissions, 36 write routes; me = `Алина (Оператор)`, permissions `read, appeals.create`, `enforced: true`, request id echoed |
| `POST /api/checklists/test-criterion` (new C9 «Ясность следующего шага», p1-ru-001, Claude) | 200 in 27 s (spec compiled by Sonnet, judged by Opus in 10 s): N/A with three evidence turns, $0.0285, nothing persisted |
| New version with C9 (judge_spec from the trial), activate | version 3 active, weights 110 (warning), diff `added: [C9]`, `rejudge_codes: [C9]` |
| Partial evaluation p1-ru-001 + p1-ru-029, base = organizer version | dry run $0.04, guard none; job done 2/2 in 12 s for $0.056; traces contain **only C9**; C1–C8 copied with the flag; totals recomputed and equal to an independent recomputation (100.00 → 100.00 with C9 N/A; 45.00 → 51.11 with C9 = Да); `weight_applied` sums to 100 |
| Appeals | Алина on her dialog → 200 (`Алина (Оператор)`); Алина on Алексей's → 403 `not_your_dialog`; Алексей on his → 200; visitor deciding → 403 `forbidden`; reviewer Иван confirms (100.00) and changes C6 4 → 7 (final 51.11 → 56.11) |
| Audit trail | 10 events with the actors, Russian summaries and the client request ids (`live-trial-1` …); refused attempts recorded with status 403; `q=p1-ru-029` finds the appeal events |
| `GET /api/overview`, `/api/operators/{name}`, `/api/violations` | overview in 56 ms: coverage 65/66, mean 80.77, median 95.0, 8 gross violations (1 insult, 4 paid bypass, 3 disclosure), appeals 4 decided / 3 changed; scorecards Алина 100.00, Алексей 56.11 with coaching points C2, C5; unknown name → 404 `operator_not_found`; violations 8 |
| Budget guard (`DAILY_BUDGET_USD=0.01`, restart) | real run → 429 `budget_exceeded` (Russian message with the numbers), no job created; dry run reports the same guard; trial → 429; `fake` dry run → guard none, $0; health warning `budget_exhausted` |
| Snapshots | create `live-check` 201 (27.2 MB, integrity ok); visitor download 403; admin `Range: bytes=0-99` → 206 with the SQLite header; wrong confirm → 400 `confirm_mismatch`; restore → 200 `verified`, 54 ms, safety snapshot `pre-restore-20261107-142312`, the settings change made after the snapshot is gone; audit `snapshot.restore` captured `verified` and `counts`; health shows 2 snapshots |
| `GET /api/export/results/validate?group_id=DEV&expect=65` | valid: 65 ok, 8 critical, mean 81.02, team «Я Тебя Могну»; `answers=final` valid (mean 80.60); version 3 export valid; bad `answers` → 400 |
| Dynamics 365 ingest (manifest's synthetic payload) | 200: 7 turns, RU, agent chat, operator «Алина», `agent_name` «Алина», first response 22 s, offsets from the ISO timestamps (0, 4, 30, 52, 80, 95, 120 s); operator → 403; `evaluate: true, provider: fake` → job completed for $0; unchanged re-push → `skipped_unchanged`; audit per conversation, `documentbody` not stored |
| Redeploy during a control run (`scripts/control_run.py --skip-import --no-git`, API killed mid-job, restarted) | 1st run: recovery marked the job `interrupted` (audit `job.interrupted`, role system), the script polled through the outage, but resume answered `nothing_to_resume` because the dialog had an older DEV evaluation → **bug fixed** (`resume_plan` counts only evaluations created since the job; regression test) and `--out` outside the repo crashed the script → **fixed**. 2nd run with the fixes: interrupted → resumed as a new job → completed → results file VALID in 36 s; a second resume → 409 `nothing_to_resume` |
| SSE through the full stack (fake judge, 3 dialogs) | first event at once, 24 `criterion.done`, 3 `evaluation.done`, but **two** `job.done` → fixed to exactly one; re-checked live (16 `criterion.done`, 1 `job.done`) |
| Error envelopes | unknown route → 404 `not_found` (Russian), bad body → 422 `validation_error` with `errors[]`, unknown group → 400 `export_failed`, all with `X-Request-ID` |

Claude spend: $0.78 recorded (evaluations $0.755, trial $0.029), plus the unrecorded Sonnet compile of the C9 spec and the two evaluations killed in flight; well under $1.50 in total, against the $5 limit.

## 5. Known limitations left

- Soft permissions, not authentication (headers are self-declared; no header = `demo` with every permission).
- Budget accounting misses the Claude spend of evaluations killed mid-flight and the spec compile of a trial.
- A restore rewinds the audit trail with the rest of the database (the history after the snapshot survives only in the pre-restore snapshot).
- Open appeals (`under_review`) stay on the base evaluation during a partial re-evaluation; deciding them later updates the old evaluation only.
- Guards, the start lock and recovery assume one API process per database (Railway runs one).
- The access log is duplicated by uvicorn's own access log unless the start command adds `--no-access-log`.
- The hosted instance still runs the previous build (no `/api/version` there); nothing was deployed.

## 6. Integration requests (for the team)

1. **Deploy** (team lead): `railway variables --set APP_VERSION=$(git rev-parse --short HEAD)`, check `GET /api/system/health` → `budget.remaining` before the control hour (raise `DAILY_BUDGET_USD` if four or five full DEV runs are planned), create the `baseline` snapshot once the hosted instance is populated, do not redeploy during the control run. Details: ARCHITECTURE_V2 §10–§11, `docs/HOSTING.md`.
2. **Frontend** (user): the list in ARCHITECTURE_V2 §15 (role headers, `interrupted` as a terminal job status and the «Возобновить» button, guard messages on dry runs, `error.message` + `X-Request-ID` in toasts, Studio draft/test/export, admin and calibration pages).
3. **ML teammate**: `cqa/eval/harness.py` could skip `_`-prefixed keys and failed rows (calibration request 5); three unused imports in `harness.py`, `ensemble.py`, `validate.py` (ruff F401).
