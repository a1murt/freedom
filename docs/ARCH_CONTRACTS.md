# Architecture contracts (shared by all backend work packages)

Owner: team lead (architecture). Frontend design is owned by the user in `web/`; backend packages never edit `web/`. Every package below owns a disjoint set of files; shared files (`cqa/main.py`, `cqa/models.py`, `cqa/config.py`, `cqa/platform/__init__.py`, `cqa/export/results_json.py`, `docs/API.md`, `docs/ARCHITECTURE.md`) are edited only by the integration step.

## 1. Actor context (no login, attribution + soft permissions)

Requests may carry:

| Header | Meaning | Example |
|---|---|---|
| `X-CQA-Role` | Role chosen in the UI | `operator`, `analyst`, `reviewer`, `lead`, `director`, `methodologist`, `admin`, `visitor` |
| `X-CQA-User` | Person name, **URL-encoded UTF-8** | `%D0%90%D0%BB%D0%B8%D0%BD%D0%B0` (Алина) |
| `X-Request-ID` | Optional client correlation id | any short string |

Rules:
- No `X-CQA-Role` header → role `demo` with **every permission** (backward compatibility: the current web app sends no headers and must keep working unchanged).
- Unknown role → 400 `{"error": {"code": "unknown_role", ...}}`.
- Missing permission → 403 `{"error": {"code": "forbidden", "message": "...", "role": "...", "permission": "..."}}`.
- The actor is available to handlers via `request.state.actor` = `{"role": str, "name": str | None, "request_id": str}` and via `cqa.security.roles.current_actor(request)`.
- `operator` may create appeals only on dialogs whose `operator_name` (or `agent_name`, the source-system identity) equals `X-CQA-User` (enforced in `POST /api/appeals`; the appeals router fills `author_role` / `author_name` / `author_label` and `reviewer_role` / `reviewer_label` from the headers when the body omits them; the middleware never rewrites bodies).

### Roles and permissions (source of truth: `cqa/security/roles.py`)

| Permission | demo | visitor | operator | analyst | reviewer | lead | director | methodologist | admin |
|---|---|---|---|---|---|---|---|---|---|
| `read` (all GET) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `conversations.import` (import, upload, paste) | ✓ |  |  | ✓ |  | ✓ |  | ✓ | ✓ |
| `evaluations.run` | ✓ |  |  | ✓ | ✓ | ✓ |  | ✓ | ✓ |
| `appeals.create` | ✓ |  | ✓ (own dialogs) | ✓ | ✓ | ✓ |  |  | ✓ |
| `appeals.decide` | ✓ |  |  |  | ✓ | ✓ |  |  | ✓ |
| `checklists.write` (create version, activate, regenerate spec, test criterion) | ✓ |  |  |  |  | ✓ |  | ✓ | ✓ |
| `settings.write` | ✓ |  |  |  |  |  |  |  | ✓ |
| `calibration.write` (gold labels) | ✓ |  |  |  | ✓ | ✓ |  | ✓ | ✓ |
| `admin` (snapshots, restore, audit export, integrations config) | ✓ |  |  |  |  |  |  |  | ✓ |
| `integrations.ingest` (Dynamics 365 transcript push) | ✓ |  |  | ✓ |  | ✓ |  |  | ✓ |

`GET /api/roles` returns this matrix plus, per role, a suggested home route and nav items for the UI (`home`, `nav`), and labels in RU/EN/KK.

## 2. Audit trail

Table `audit_events` (model `AuditEvent` in `cqa/models.py`): `ts, actor_role, actor_name, action, entity_type, entity_id, summary, details (JSON), request_id, status_code`.

- Every successful **write** request (POST/PUT/PATCH/DELETE under `/api/`, status 2xx) and every refused one (403) is recorded by the security middleware (3xx, other 4xx and 5xx are not; `POST /api/appeals/preview` is not) with an `action` from the route map (e.g. `appeal.create`, `appeal.decide`, `checklist.create`, `checklist.version.create`, `checklist.version.activate`, `checklist.spec.regenerate`, `evaluation.run`, `dataset.import`, `conversation.upload`, `conversation.paste`, `settings.update`, `job.cancel`, `snapshot.create`, `snapshot.restore`, `calibration.labels.upsert`, `integration.dynamics.ingest`), `entity_id` from path params or the response, and a compact `details` (never full transcripts or secrets; bodies truncated to 2 KB).
- Services may add semantic events with `cqa.audit.record(db, action=..., entity_type=..., entity_id=..., summary=..., details=..., actor=...)` (e.g. `job.completed`, `job.interrupted`).
- `GET /api/audit?action=&entity_type=&entity_id=&actor_role=&actor_name=&status_code=&q=&since=&until=&limit=100&before_id=` → newest first. `request_id` (64 characters) equals the `X-Request-ID` response header.

## 3. Platform guards (public hosted instance)

Settings in `cqa/config.py` (env-overridable): `DAILY_BUDGET_USD` (75), `MAX_DIALOGS_PER_RUN` (200), `MAX_CONCURRENT_JOBS` (2), `MAX_CONCURRENT_TRIALS` (2), `MAX_TRANSCRIPT_CHARS` (100 000), `MAX_TRANSCRIPT_TURNS` (2 000), `CORS_ORIGINS` (JSON list), `APP_VERSION`, `SPEND_LEDGER_PATH`, `CQA_PUBLIC_INSTANCE`.

Spend (fix pass, `docs/arch/fixes.md`): every billed Claude call is booked in the spend ledger (`cqa.jobs.ledger`, a file outside the snapshotted database), successful or not, with cache writes at 1.25 × input; the 24 h spend = max(ledger, database). Only models with a configured price may be called (422 `unknown_model`). Estimates are per dialog and token-based (`cqa.jobs.estimate`); a partial run prices dialogs without a completed base evaluation in full.

`POST /api/evaluations/run` (non-dry) is rejected with **429** and a precise code when:
- `budget_exceeded`: 24 h spend + what running jobs, trials and compilations have reserved + this run's estimate > budget (message shows spent/budget/estimate); only runs that make no paid model call (local, fake, a weight-only partial run that copies every row) are never blocked; a paid job also stops starting dialogs once the 24 h spend reaches the budget;
- `too_many_jobs`: running jobs ≥ limit;
- `too_many_dialogs`: queued dialogs > limit.
Dry runs always work and return `budget: {spent_24h, limit, remaining}`.

Start-up recovery (`cqa.jobs.runner.recover_stale_jobs`, called from `main.py`): jobs left `queued/running` by a previous process become `interrupted` (log entry + audit event); evaluations left `running` become `failed` with `error = "interrupted by restart"`. `POST /api/jobs/{id}/resume` re-queues the job's conversations that have no completed evaluation for its checklist version made since the job started.

## 4. Error envelope

Always `{"error": {"code": "<snake_case>", "message": "<human, RU>", ...extra}}` with the HTTP status. Validation errors use 422 with `errors: [{"field": "criteria[2].weight", "message": "..."}]`: package routers with their own codes, and a global handler in `main.py` (`validation_error`) for every other route (FastAPI's `detail` list is kept next to it for old clients). Unknown routes answer 404 `not_found`, wrong methods 405 `method_not_allowed`, crashes 500 `internal` with `request_id`.

## 5. New endpoints by package (details in each package's `docs/arch/<package>.md`; the integration step merges them into `docs/API.md`)

| Package | Endpoints |
|---|---|
| platform | `GET /api/system/health` (deep: db, providers, disk, jobs, version, uptime), `GET /api/version`, `GET /api/system/metrics`, `POST /api/jobs/{id}/resume` |
| roles | `GET /api/roles`, `GET /api/audit`, `GET /api/me` (echo of the resolved actor and permissions) |
| checklists | 422 validation on create/version, `activate` flag on version create, `weights` summary in version JSON, `POST /api/checklists/test-criterion` (judge one criterion on one dialog, no persistence), `GET /api/checklists/{id}/versions/{vid}/export` (portable JSON) |
| overview | `GET /api/overview` (executive KPIs, trends by day, by language/channel, top failing criteria, violations, appeals funnel, agreement, cost), `GET /api/operators/{name}` (operator scorecard and coaching points) |
| admin | `GET/POST /api/admin/snapshots`, `POST /api/admin/snapshots/{name}/restore`, `GET /api/admin/snapshots/{name}/download` |
| integrations | `POST /api/integrations/dynamics/transcripts`, `GET /api/integrations/dynamics/manifest` |
| calibration | `GET/PUT /api/calibration/labels`, `POST /api/calibration/import`, `GET /api/calibration/export`, `GET /api/calibration/agreement`, `GET /api/calibration/stability` |
| testing | fake provider `fake` (deterministic, offline), CI workflow |
| integration | `answers=ai\|final` on the results export, `agent_name` on conversations, `snapshots` in `/api/system/health`, `default_checklist_id` setting (see `docs/arch/integration.md`) |

## 6. Testing contract

- Every package adds tests under `api/tests/` in its own file(s); `cd api && .venv/bin/python -m pytest -q` must stay green.
- Tests never call the Claude API; use the `fake` provider or monkeypatch.
- The integration step may run a small live verification against the local API with the real key (≤ $5).
