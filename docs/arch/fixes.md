# Fix pass: spend, budget guard, snapshots and correctness

Owner: fix engineer (architecture). Scope: the confirmed review findings SEC-1 … SEC-10, F1 … F5 and the two compat
findings (empty control set, restart-interrupted rows). Everything is backward compatible for the deployed web app:
no endpoint or field was removed or renamed; new fields and error codes are additive. Tests: `api/tests/test_fixes.py`
(44 tests) plus updated assertions in existing files (list at the end).

## 1. What changed, by finding

| Finding | Fix | Main files |
|---|---|---|
| SEC-1 / F3 partial run priced at $0 | Per-dialog estimate from what the worker will judge: a dialog with no completed evaluation on the base version (or a partial run without `base_version_id`) is priced in full; only a run with **no model call at all** is free | `cqa/jobs/estimate.py`, `routers/evaluations.py` |
| SEC-2 unbudgeted spec compilation | Compilation checks the budget and reserves $0.03 per criterion; create/version/import fall back to the deterministic spec (+ warning) when the budget is spent, regenerate answers 429; ≤ 4 compilations in flight; cost booked (`kind: compile`); ≤ 100 criteria, instruction ≤ 4000, N/A condition ≤ 2000, judge_spec JSON ≤ 20 000 characters | `checklist/compiler.py`, `checklist/service.py`, `checklist/validation.py`, `main.py` |
| SEC-3 cache writes not counted | `cost_usd(model, in, out, cache_read, cache_write)`: cache writes (5-minute TTL) at 1.25 × input rate; traces carry `cache_write_tokens` | `judge/providers/base.py`, `anthropic_provider.py`, `config.py` |
| SEC-4 unpriced model = $0 | Only models with a `cost_rates` entry (or a dated `<key>-…` variant) may be called: 422 `unknown_model` on run, resume, trial, Dynamics, PUT /api/settings, and in the worker; `rate_for` of an unknown model is the highest configured rate, never 0 | `config.py`, `judge/runner.py` |
| SEC-5 ensemble size ignored, settings unvalidated | Estimate = calls × members (+ arbiter share); PUT /api/settings validates models, `ensemble_members` (1–5), `judge_effort` (low…max), `max_parallel_criteria` (1–16), provider, local endpoint; bad stored values are clamped at run time (a stored 0 no longer hangs the evaluation) | `routers/settings_router.py`, `judge/runner.py` |
| SEC-6 fixed estimate, unbounded input, no in-run stop | Token-based estimate (transcript length, criteria, members, model rates); body limits (pure ASGI, 413); transcript limits (100 000 chars / 2 000 turns) at evaluation time; a paid job checks the budget before every dialog and stops at the cap; Dynamics body cap 8 MB | `platform/bodylimit.py`, `jobs/runner.py`, `jobs/estimate.py` |
| SEC-7 restore rewinds the budget; token optional | Spend ledger outside the database (a restore cannot rewind it); on the hosted instance snapshot writes and downloads need `CQA_ADMIN_TOKEN` (else 403 `admin_token_not_configured`, opt-out `ALLOW_UNPROTECTED_SNAPSHOTS=1`); ≤ 20 manual snapshots | `jobs/ledger.py`, `routers/admin.py`, `admin/snapshots.py` |
| SEC-8 `local_endpoint` SSRF | Allowed endpoints: `LOCAL_ENDPOINT`, `LOCAL_ENDPOINT_ALLOWLIST`, and off the hosted instance loopback / private / LAN names; metadata and public hosts refused; `LOCAL_API_KEY` only to `LOCAL_ENDPOINT` and allowlisted endpoints; no process-wide settings mutation; the health probe uses the same rules | `judge/local_endpoint.py`, `judge/runner.py`, `routers/system.py`, `routers/settings_router.py` |
| SEC-9 trials race the guard; failed trials unbooked | Trial estimate (judge + compile) reserved under `start_lock` until the trial ends; ≤ 2 trials at once (429 `too_many_trials`); every billed call booked by the provider, failures included | `checklist/service.py`, `jobs/guards.py` |
| SEC-10 billed failures lose their cost | The provider uses `messages.create` with the same `output_config.format` that `messages.parse` sends and validates itself, so refusals, `max_tokens` cut-offs and invalid JSON raise `ProviderError` **with** `usage`/`cost_usd`; traces are caller-owned and kept for failed members, retries and arbiters; error rows store their traces and cost | `anthropic_provider.py`, `providers/base.py`, `judge/runner.py` |
| F1 restore under a running evaluation | Restore runs under `jobs.runner.start_lock`; `force=true` cancels workers and **waits** for their in-flight dialogs (409 `jobs_still_running` after 30 s); restore bumps `cqa.db.restore_generation`; an evaluation re-checks generation and its own row before writing (`EvaluationAborted`, nothing written); job bookkeeping skips writes after a restore; the CLI restore refuses while jobs are queued/running (`--force`) | `routers/admin.py`, `judge/runner.py`, `jobs/runner.py`, `admin/snapshots.py`, `db.py` |
| F2 shared AsyncAnthropic across loops | One client per running event loop (`weakref` registry); job workers and compilations close theirs when their loop ends | `anthropic_provider.py`, `jobs/runner.py`, `checklist/compiler.py` |
| F4 pinned pooled connections | `evaluate_conversation` and the trial load everything, end the read transaction, then call the judges; the SSE poll reads the job in the thread pool; pool `max_overflow` = jobs × dialogs + 10 | `judge/runner.py`, `checklist/service.py`, `routers/evaluations.py`, `db.py` |
| F5 results.json uses another version | `from_db` falls back only to evaluations of the export version; `from_api` selects the latest *completed* evaluation of the version through the history endpoints, so `--api` and `--db` agree | `export/results_json.py` |
| compat F1 empty control set | `conversation_ids: []` with `all: false` → 400 `no_conversations` (was: every conversation); `from_db`/`from_api` with `[]` export nothing | `jobs/runner.py`, `export/results_json.py` |
| compat F2 interrupted rows hide results | `latest_evaluation` ranks rows failed by start-up recovery (`interrupted by restart`) below every other evaluation; the conversations list already prefers the latest completed one | `serializers.py` |

## 2. Spend accounting

### Spend ledger (`cqa/jobs/ledger.py`)

- File: `SPEND_LEDGER_PATH`, default `<DATA_DIR>/spend_ledger.db` (SQLite, table `spend(ts, cost_usd, kind, provider, model,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, ref, ok)`). It is **not** inside the main
  database, so snapshots never contain it and a restore never rewinds it. On Railway it lives on the `/app/data` volume.
- Booked by `AnthropicProvider.complete` for every response that carries usage, successful or not (`ok = 0` for a
  refusal / truncated / invalid answer). `ledger.context(kind=…, ref=…)` labels the work: `judge`
  (`ref: evaluation:<id> job:<id>`), `trial`, `compile`.
- Budget spend = **max(ledger, database)** (database = `Evaluation.cost_usd` + trial costs from the audit trail, as
  before). The first read of a new ledger file copies the last 24 h of database spend into it (`kind: backfill`, once per
  file, recorded in `meta.backfilled_at`), so the first deploy keeps counting earlier spend.
- A broken ledger never blocks work: booking logs a warning, the budget falls back to the database.

### Cost of one call

`cost = (input × rin + output × rout + cache_read × rcache + cache_write × rin × 1.25) / 1e6` (`input` is the
uncached remainder, as the API reports it). For `claude-opus-5-5`: rin 4, rout 20, rcache 0.20 USD / 1M tokens.
Stored traces keep `usage.cache_write_tokens` in `response.usage`.

### Estimates (`cqa/jobs/estimate.py`)

Per dialog, for each judged criterion and each paid member: uncached criterion block (300 + characters/2.2 tokens) +
cached system prompt (3 100 tokens at the cache-read rate) + transcript (characters + 30 per turn + 400) / 1.8 tokens
(+800 for calls) written to the cache at 1.25 × input + 1 000 output tokens (1 600 for calls); +30 % of one call for the
ensemble arbiter; ×1.05 for validation retries. Calibrated on the 656 stored Claude traces of the DEV set (real median
$0.24 per chat, $0.46 per call; the model gives ≈ $0.27 / $0.52). Floors: the historical flat rate scaled to the judged
share ($0.18 chat, $0.35 call, ensemble $0.38 / $0.75 × members/3), and $0.01 when anything is judged by a paid model.
`RunEstimate.free` (no paid member, or zero model calls) is the only case the budget check skips.

## 3. Endpoints whose behaviour changed

### POST /api/evaluations/run (and dry run)

```json
{"conversation_ids": ["a", "b"], "provider": "anthropic", "mode": "partial", "base_version_id": 1, "dry_run": true}
```
→ 200
```json
{"queued": 2, "estimated_cost_usd": 0.18, "rejudge_codes": [], "checklist_version_id": 2,
 "estimate": {"method": "tokens", "paid": true, "members": ["anthropic:claude-opus-5-5"], "dialogs": 2,
              "judged_dialogs": 1, "model_calls": 8, "max_dialog_usd": 0.18},
 "skipped": [{"conversation_id": "x", "reason": "transcript_too_long", "chars": 250000, "turns": 3000}],
 "budget": {"spent_24h": 12.3, "limit": 75.0, "remaining": 62.7, "reserved": 0.0, "unlimited": false, "window_hours": 24},
 "guard": null}
```
(`skipped` only when some dialogs are over the limits; they are not queued.) New errors:

| Status | Code | When |
|---|---|---|
| 400 | `no_conversations` | `conversation_ids: []` with `all: false` (null or `all: true` still mean "all") |
| 422 | `unknown_provider` | provider outside anthropic, ensemble, fake, local, ollama, openai_compatible, vllm (`anthropic:<model>` is accepted as provider + model) |
| 422 | `unknown_model` | a Claude model (given, default setting or ensemble member) without a configured price; `model`, `errors[]` |
| 422 | `transcript_too_long` | every selected dialog is over the limits (`skipped[]`); a dry run answers 200 with this as `guard` |
| 429 | `budget_exceeded` | as before, now also for partial runs that judge something and for paid runs estimated at $0 |

Jobs: `params` gain `paid`; a paid job stops starting dialogs once the 24 h spend reaches the budget: status
`cancelled`, log line `stopped: daily budget exhausted …`, SSE event `job.budget_exhausted {spent_24h, limit}`,
`job.done` with `reason: "budget_exhausted"`, `resumable: true`. `POST /api/jobs/{id}/resume` uses the same estimate and
errors (it can answer 422 `unknown_model` / `transcript_too_long`).

### POST /api/checklists/test-criterion

New errors: 422 `unknown_model`, 422 `transcript_too_long`, 429 `too_many_trials` (`running`, `limit`, `retry_after_s`).
The budget check covers the spec compilation the trial may need (even with provider `local`), and the estimate is
reserved until the trial ends. A 502 `judge_failed` reports the real `cost_usd` of billed calls.

### Checklists

- POST /api/checklists, /import, /{id}/versions: 422 `invalid_checklist` for > 100 criteria and the length limits above;
  with an API key and an exhausted budget missing specs are compiled by the deterministic fallback and `warnings[]`
  says so (`field: criteria`).
- POST …/judge-spec/{code}/regenerate: 429 `budget_exceeded` (guard envelope, `Retry-After`) when the budget is spent.

### PUT /api/settings

422 `invalid_settings` `{errors: [{field, message}]}`, nothing written:
```json
{"error": {"code": "invalid_settings", "message": "Настройки не сохранены: default_model — Модель «claude-fable-5-1» не поддерживается: …",
           "errors": [{"field": "default_model", "message": "…"}]}}
```
A value equal to the stored one is accepted unchanged (the web form sends everything back); `null`/`""` clears.

### Snapshots (/api/admin/snapshots…)

- Hosted instance without `CQA_ADMIN_TOKEN`: create / restore / delete / download → 403 `admin_token_not_configured`
  (listing stays readable and reports `writes_allowed: false`). `ALLOW_UNPROTECTED_SNAPSHOTS=1` restores the old
  behaviour. Off the hosted instance nothing changes.
- Create beyond `SNAPSHOT_MAX_MANUAL` (20) manual snapshots → 409 `too_many_snapshots` (`limit`, `count`); overwriting an
  existing name is allowed. Listing adds `writes_allowed`, `max_manual`.
- Restore with live workers: 409 `jobs_running` (as before); with `force: true` the workers are cancelled and the
  restore waits up to 30 s for their in-flight dialogs, else 409 `jobs_still_running` (`jobs`, `cancelled_jobs`), data
  unchanged. The 24 h spend is unchanged by a restore.
- CLI: `python -m cqa.admin.snapshots restore NAME --yes` refuses while the database lists queued/running jobs
  (`--force` for jobs left by a stopped process).

### Other

- Any POST/PUT/PATCH under /api/ over its body limit → 413 `payload_too_large` (`limit_bytes`): paste 2 MB, upload
  10 MB, Dynamics 8 MB, checklists 6 MB, others 10 MB.
- POST /api/integrations/dynamics/transcripts with `evaluate`: token-based estimate; 422 `unknown_model` (before
  anything is written); 422 `transcript_too_long` (transcript kept, `ingested: true`).
- GET /api/conversations/{id}/evaluation and conversation cards: a row failed by start-up recovery no longer hides the
  previous result.
- GET /api/system/health, GET /api/health: the local provider is the *effective* endpoint (a stored address that is not
  allowed is ignored and never probed).

## 4. Configuration (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `CQA_ADMIN_TOKEN` | — | required for snapshot writes/downloads on the hosted instance (header `X-CQA-Admin-Token`) |
| `ALLOW_UNPROTECTED_SNAPSHOTS` | `0` | `1` = snapshot writes without a token on the hosted instance |
| `CQA_PUBLIC_INSTANCE` | auto | `1`/`0` overrides the hosted-instance detection (any `RAILWAY_*` deployment variable) |
| `SNAPSHOT_MAX_MANUAL` | 20 | manual snapshots kept at most |
| `SPEND_LEDGER_PATH` | `<DATA_DIR>/spend_ledger.db` | spend ledger file |
| `MAX_CONCURRENT_TRIALS` | 2 | single-criterion trials at once |
| `MAX_TRANSCRIPT_CHARS` / `MAX_TRANSCRIPT_TURNS` | 100 000 / 2 000 | dialogs over these are never judged |
| `LOCAL_ENDPOINT_ALLOWLIST` | — | comma-separated `scheme://host[:port][/path]` that PUT /api/settings may select (trusted with `LOCAL_API_KEY`) |

Set `CQA_PUBLIC_INSTANCE=1` on any internet-facing deployment that is not on Railway.

## 5. Permissions

Unchanged: the same permission map (`cqa/security/roles.py`); the new checks are content checks inside the existing
routes. The admin token is an additional lock on top of the `admin` permission.

## 6. Tests

`api/tests/test_fixes.py` (one or more tests per finding; no Claude call, own SQLite file per test, ledger isolated by
`api/tests/conftest.py`):

- SEC-1/F3: `test_partial_run_without_a_base_is_priced_in_full_and_refused_when_the_budget_is_spent`,
  `test_partial_run_prices_only_dialogs_that_have_no_completed_base_evaluation`, `test_resume_of_a_partial_job_uses_the_same_estimate`
- SEC-3: `test_cost_includes_prompt_cache_writes`; SEC-4: `test_an_unknown_model_is_never_priced_at_zero`, `test_runs_with_an_unpriced_model_are_refused`
- SEC-5/8: `test_settings_reject_unpriced_models_oversized_ensembles_and_unsafe_values`, `test_settings_accept_a_resent_stored_value_and_clearing`,
  `test_on_the_hosted_instance_only_configured_local_endpoints_are_accepted`, `test_local_endpoint_rules`,
  `test_parallel_criteria_and_effort_of_bad_stored_values_are_safe`, `test_ensemble_estimate_scales_with_the_number_of_members`,
  `test_members_never_modify_global_settings_and_ignore_a_disallowed_endpoint`, `test_health_probe_never_contacts_a_disallowed_endpoint`
- SEC-6: `test_body_limit_answers_413_before_reading_and_while_streaming`, `test_a_paid_job_stops_starting_dialogs_once_the_budget_is_spent`,
  `test_dialogs_over_the_transcript_limits_are_never_judged`, `test_estimate_grows_with_the_transcript_and_the_number_of_criteria`, `test_checklist_size_limits`
- SEC-7: `test_restoring_a_snapshot_does_not_rewind_the_daily_budget`, `test_ledger_backfills_the_database_once`,
  `test_snapshot_writes_fail_closed_on_the_hosted_instance_without_a_token`, `test_manual_snapshots_are_capped`
- SEC-9: `test_concurrent_trials_cannot_all_pass_one_budget_check`, `test_trials_are_capped_and_failed_trials_are_booked`,
  `test_trial_with_an_unpriced_model_or_too_long_dialog_is_refused`
- SEC-10: `test_a_billed_first_call_survives_a_retry_that_fails`, `test_an_ensemble_member_that_fails_after_billing_keeps_its_cost`,
  `test_a_failed_evaluation_stores_its_billed_traces`
- F1: `test_an_evaluation_never_writes_into_a_restored_database`, `test_an_evaluation_does_not_overwrite_a_row_that_now_belongs_to_another_dialog`,
  `test_restore_waits_for_cancelled_workers_under_the_start_lock`, `test_a_worker_tolerates_a_job_row_that_disappeared`
- F2: `test_each_event_loop_gets_its_own_claude_client`; F4: `test_no_pooled_connection_is_held_while_the_judges_run`, `test_engine_pool_has_room_for_the_configured_concurrency`
- F5 + empty selection: `test_results_export_never_uses_another_versions_evaluation_and_both_sources_agree`, `test_an_explicit_empty_selection_selects_nothing`
- interrupted rows: `test_a_row_interrupted_by_a_restart_does_not_hide_the_last_result`
- SEC-2: `test_compilation_is_budgeted_and_falls_back_when_the_budget_is_spent`, `test_compilation_runs_with_bounded_concurrency`,
  `test_regenerating_a_spec_is_refused_when_the_budget_is_spent`, `test_compilation_cost_is_booked`

Updated existing tests (intended behaviour changes): `test_providers.py` (fake SDK client exposes `messages.create`;
cost includes cache writes; billed refusal / truncation carry usage; new `test_claude_truncated_json_is_a_billed_error`),
`test_dynamics.py` (estimate is the token-based one, `paid` in job params), `test_integration.py` (middleware stack
includes `BodyLimitMiddleware`). Full suite: `cd api && .venv/bin/python -m pytest -q` → green.

## 7. Limitations

- Transcript limits are enforced when a dialog would be judged (run, resume, trial, Dynamics, worker), not at ingest:
  a stored transcript is bounded by the request body limits (paste 2 MB, upload 10 MB) but not rejected on upload.
- The in-run budget stop is checked before each dialog: dialogs already in flight (≤ `MAX_PARALLEL_CONVERSATIONS` per
  job) finish, so a job can pass the cap by at most those dialogs.
- Mixed windows after the first deploy: the ledger is backfilled once from the database; spend made by an older build in
  another process after that moment is counted only through the database side of max().
- A refusal the API does not bill is still booked with the usage the response reports (counted, not under-counted).
- `mode: "partial"` without `base_version_id` is accepted (the web sends it) and priced and run as a full evaluation;
  it is not rejected with 422.
- `cqa.eval.harness` and other offline scripts book their real spend into the same ledger (intended).

## 8. Integration requests

- `scripts/control_run.py` (outside this pass, not edited): (1) right after the ids are resolved, add
  `if not ids: raise SystemExit("0 dialogs resolved for the control set: nothing was evaluated — check --group / --source-url / --source-file")`;
  the server now refuses `conversation_ids: []` (the preflight exits with `dry run failed: HTTP 400 no_conversations`)
  and `from_api(conversation_ids=[])` exports nothing, so the script already stops before evaluating, but the explicit
  guard gives the clearer message. (2) `snapshot_before_control` should send `X-CQA-Admin-Token: $CQA_ADMIN_TOKEN` from
  the environment; without it the pre-control snapshot on the hosted instance is skipped with a warning (the run goes on).
- Web (owned by the user): show `error.message` for the new codes (`invalid_settings`, `unknown_model`,
  `unknown_provider`, `transcript_too_long`, `too_many_trials`, `payload_too_large`, `admin_token_not_configured`,
  `too_many_snapshots`, `jobs_still_running`); optionally display `estimate` and `skipped` from the run answer and the
  `job.budget_exhausted` SSE event. No change is required for the current screens to keep working.
- Deployment: set `CQA_ADMIN_TOKEN` on Railway (docs/HOSTING.md) before relying on snapshots there.
