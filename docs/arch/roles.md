# Package "roles": roles, permissions and the audit trail

Implements docs/ARCH_CONTRACTS.md §1 (actor context, soft permissions) and §2 (audit trail). There is no login: the UI declares
who is acting in request headers, the API attributes every write to that actor, enforces the permission matrix on writes and
keeps an audit log that anyone can read.

| File | What it holds |
|---|---|
| `api/cqa/security/roles.py` | Source of truth: `ROLES` (labels RU/EN/KK, description, permissions, home route, nav items), `PERMISSIONS`, `NAV_ITEMS`, `ACTIONS` (audit action labels + summary templates), `ROUTE_MAP`, `match_route`, `has_permission`, `current_actor`, `require_permission`, `catalog` |
| `api/cqa/security/middleware.py` | Pure ASGI `SecurityMiddleware` + `install(app)` (called by `cqa.platform.install`) |
| `api/cqa/audit/service.py` | `record()`, `list_events()`, `facets()`, `serialize_event()`, and the `compact()` / `scrub_text()` helpers |
| `api/cqa/audit/__init__.py` | Re-exports `record`, `list_events`, … (`cqa.audit.record` is what other packages call) |
| `api/cqa/routers/roles.py` | `GET /api/roles`, `GET /api/me` |
| `api/cqa/routers/audit.py` | `GET /api/audit`, `GET /api/audit/facets`, `GET /api/audit/export.csv` |
| `api/cqa/routers/appeals.py` | Appeal and decision authorship defaulted from the actor; stricter own-dialog check for operators |
| `api/tests/test_roles_audit.py` | 55 tests (see "Tests") |

## 1. Actor context

| Header | Meaning | Notes |
|---|---|---|
| `X-CQA-Role` | Role chosen in the UI | Trimmed, case-insensitive. Missing or empty → `demo`. Unknown → **400 `unknown_role`** (OPTIONS is never refused). |
| `X-CQA-User` | Person name | URL-encoded UTF-8 (`encodeURIComponent("Алина")` = `%D0%90%D0%BB%D0%B8%D0%BD%D0%B0`). Raw UTF-8 bytes are accepted too (curl, server-to-server). Control characters dropped, whitespace collapsed, at most 128 characters. |
| `X-Request-ID` | Correlation id | The platform request log (outermost layer) validates it or assigns one and puts it in `scope["state"]["request_id"]`; the actor and the audit row use the same id, and every response carries it in `X-Request-ID`. |

Handlers get `request.state.actor = {"role": str, "name": str | None, "request_id": str}`, or call
`cqa.security.roles.current_actor(request)` (which also works in apps built without the middleware, e.g. other packages' test
apps: it resolves the headers itself and raises the same 400 for an unknown role).

For handler-level checks (e.g. a sensitive GET) use the dependency `require_permission("admin")`: 403 `forbidden` envelope.

## 2. Roles and permissions

The matrix is exactly the table in ARCH_CONTRACTS §1 (a test compares them cell by cell).

| Permission | demo | visitor | operator | analyst | reviewer | lead | director | methodologist | admin |
|---|---|---|---|---|---|---|---|---|---|
| `read` (all GET/HEAD/OPTIONS) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `conversations.import` | ✓ |  |  | ✓ |  | ✓ |  | ✓ | ✓ |
| `evaluations.run` | ✓ |  |  | ✓ | ✓ | ✓ |  | ✓ | ✓ |
| `appeals.create` | ✓ |  | ✓ own dialogs | ✓ | ✓ | ✓ |  |  | ✓ |
| `appeals.decide` | ✓ |  |  |  | ✓ | ✓ |  |  | ✓ |
| `checklists.write` | ✓ |  |  |  |  | ✓ |  | ✓ | ✓ |
| `settings.write` | ✓ |  |  |  |  |  |  |  | ✓ |
| `calibration.write` | ✓ |  |  |  | ✓ | ✓ |  | ✓ | ✓ |
| `admin` | ✓ |  |  |  |  |  |  |  | ✓ |
| `integrations.ingest` | ✓ |  |  | ✓ |  | ✓ |  |  | ✓ |

UI hints per role (`home`, ordered `nav`; routes are suggestions for the redesigned web app):

| Role | Label (RU / EN / KK) | home | nav |
|---|---|---|---|
| demo | Демо-режим / Demo mode / Демо режимі | `#/conversations` | conversations, overview, violations, review, operators, analytics, checklists, calibration, audit, settings, admin |
| visitor | Гость / Visitor / Қонақ | `#/` | home, overview, conversations, violations, analytics |
| operator | Оператор / Operator / Оператор | `#/my` | my, checklists |
| analyst | QA-аналитик / QA analyst / QA-талдаушы | `#/conversations` | conversations, violations, review, operators, analytics, checklists |
| reviewer | Ревьюер / Reviewer / Ревьюер | `#/review` | review, conversations, violations, calibration, analytics, checklists |
| lead | Руководитель группы / Team lead / Топ жетекшісі | `#/overview` | overview, conversations, violations, review, operators, analytics, checklists, calibration, audit |
| director | Директор / Director / Директор | `#/overview` | overview, analytics, violations, operators |
| methodologist | Методолог / Methodologist / Әдіскер | `#/checklists` | checklists, calibration, conversations, violations, analytics |
| admin | Администратор / Administrator / Әкімші | `#/settings` | settings, admin, audit, overview, conversations, violations, review, operators, analytics, checklists, calibration |

Nav item routes: home `#/`, my `#/my`, overview `#/overview`, conversations `#/conversations`, violations `#/violations`,
review `#/review`, operators `#/operators`, analytics `#/analytics`, checklists `#/checklists`, calibration `#/calibration`,
audit `#/audit`, settings `#/settings`, admin `#/admin` (labels RU/EN/KK in `GET /api/roles`).

## 3. The middleware (pure ASGI)

Request pipeline of `cqa.main.app`: platform request log (wraps the whole stack) → error handler → CORS → **security** →
exception handlers → routes. The security middleware is innermost, so the request log also logs and tags its 400/403 answers
and CORS adds its headers to them (checked live: a refused cross-origin POST carries `access-control-allow-origin` and
`X-Request-ID`).

For each HTTP request under `/api/`:

1. Resolve the actor (above) and store it in `scope["state"]`; 400 `unknown_role` for an unknown role.
2. **GET / HEAD / OPTIONS**: call the app with the original `receive` and `send` (not wrapped at all; a test asserts object
   identity), so the SSE stream `/api/jobs/{id}/events` and ranged audio `/api/audio/{id}` stream exactly as before.
3. **Writes** (POST/PUT/PATCH/DELETE): `match_route(method, path)` (trailing slash ignored) → rule. No permission → **403
   `forbidden`**, the app is never called; the first 64 KB of the body are read for the audit summary.
4. Allowed writes run with a tee on `receive` (the app reads its own body, message by message; the middleware copies the first
   1 MB, nothing is read ahead or replayed) and a tap on `send` (each message is forwarded immediately; the first 1 MB of the
   response is copied for entity ids).
5. When the response ends with **2xx**, or with **403** (refused by the middleware or by the handler, e.g. `not_your_dialog`),
   one `AuditEvent` is written with its own session, in a worker thread, **right before the last body chunk is forwarded**, so a
   client that refetches the audit log after the response always sees its event. 3xx (nothing written), other 4xx and 5xx are
   not recorded. A failing audit write is logged (`cqa.security`) and never changes the response.

Audit session: `app.state.audit_session_factory` (set by `install(app, session_factory=...)`) → the app's dependency override
of `get_db` (so tests that only override `get_db` never touch `api/data/cqa.db`) → `cqa.db.SessionLocal`.

### Audit details (compact, never secrets)

```json
{"method": "POST", "path": "/api/appeals/1/decision", "path_params": {"appeal_id": "1"},
 "query": {"force": "true"},
 "body": {"action": "change", "new_answer": "no", "reason": "Идентификации не было", "add_to_fewshot": false},
 "response": {"appeal.code": "C3", "appeal.conversation_id": "own-001", "evaluation.final_total_score": 80.0},
 "error": {"code": "not_your_dialog", "message": "…"},  "permission": "checklists.write",  "duration_ms": 6.7}
```

- `body`: JSON compacted to **≤ 2 KB** (UTF-8) with progressively tighter limits; strings cut to 200 characters; `criteria`
  lists reduced to `{code, title, weight, answer_type, is_critical, na_allowed}`; transcripts (`transcript`, `turns`, `text`,
  `messages`, `utterances`, `dialog`) reduced to their size (`{"_omitted": "transcript", "items": 300}`), never content.
  Multipart uploads → `{content_type, bytes, files: ["dialog.json"], fields: ["file"]}`. Bodies over the capture window →
  `{content_type, bytes, truncated: true}`.
- Secrets: values of secret-looking keys (`*password`, `*secret`, `*token`, `*apikey`, `authorization`, `cookie`, `signature`,
  `sig`, `key`, …) become `"***"`; inside any string, `sk-…` API keys, `Bearer …`, URL passwords (`http://user:pass@` →
  `http://***@`) and secret query parameters (`sig=`, `token=`, `key=`, …) are masked. Headers are never stored.
- `response`: only the rule's `capture` fields. `error`: code and message of a 403. `permission`: set when the middleware
  refused the request.

`summary` is a Russian sentence built from the action's template, e.g. «Апелляция #1 (C3, диалог own-001): ответ изменён на
«no»», «Отказано: Импорт выборки — роль «Гость» без права «Импорт диалогов»». Summaries include the conversation id for appeal
events, so a conversation card can show its history with `GET /api/audit?q=<conversation_id>`.

## 4. Route map

Ordered; the first match wins; `{x:path}` matches several segments. Entity = where `entity_id` comes from.

| Method | Path | Permission | Action | Entity |
|---|---|---|---|---|
| POST | `/api/datasets/import` | conversations.import | `dataset.import` | dataset ← response.dataset_id |
| POST | `/api/conversations/upload` | conversations.import | `conversation.upload` | conversation ← response.id, or dataset ← response.dataset_id |
| POST | `/api/conversations/paste` | conversations.import | `conversation.paste` | conversation ← response.id |
| POST | `/api/evaluations/run` | evaluations.run | `evaluation.run` (`evaluation.estimate` when `dry_run`) | job ← response.job_id |
| POST | `/api/jobs/{job_id}/cancel` | evaluations.run | `job.cancel` | job ← path |
| POST | `/api/jobs/{job_id}/resume` | evaluations.run | `job.resume` | job ← path (new job id in details.response) |
| any write | `/api/jobs/{job_id}/…`, `/api/evaluations/{id}/…` | evaluations.run | `job.update`, `evaluation.update` | path |
| POST | `/api/checklists/test-criterion` | checklists.write | `checklist.criterion.test` | conversation ← body.conversation_id |
| POST | `/api/checklists/import` | checklists.write | `checklist.import` | checklist ← response |
| POST | `/api/checklists` | checklists.write | `checklist.create` | checklist ← response.checklist_id |
| POST | `/api/checklists/{checklist_id}/versions` | checklists.write | `checklist.version.create` | checklist_version ← response.id |
| POST | `/api/checklists/{id}/versions/{version_id}/activate` | checklists.write | `checklist.version.activate` | checklist_version ← path |
| POST | `/api/checklists/{id}/versions/{version_id}/judge-spec/{code}/regenerate` | checklists.write | `checklist.spec.regenerate` | checklist_version ← path |
| DELETE / other writes | `/api/checklists/{checklist_id}[/…]` | checklists.write | `checklist.delete` / `checklist.update` | checklist ← path |
| POST | `/api/appeals/preview` | read | not recorded (pure calculation) | — |
| POST | `/api/appeals` | appeals.create | `appeal.create` | appeal ← response.id |
| POST | `/api/appeals/{appeal_id}/decision` | appeals.decide | `appeal.decide` | appeal ← path |
| PUT | `/api/settings` (+ any write under `/api/settings/…`) | settings.write | `settings.update` | settings |
| PUT, POST | `/api/calibration/labels` | calibration.write | `calibration.labels.upsert` | gold_labels |
| POST | `/api/calibration/import` | calibration.write | `calibration.labels.import` | gold_labels |
| DELETE | `/api/calibration/labels[/{label_id}]` | calibration.write | `calibration.labels.delete` | gold_label(s) |
| any write | `/api/calibration/…` | calibration.write | `calibration.update` | — |
| POST | `/api/integrations/dynamics/transcripts` | integrations.ingest | `integration.dynamics.ingest` | dataset / conversation ← response |
| any write | `/api/integrations/{name}[/…]` | admin | `integration.config.update` | integration ← path |
| POST | `/api/admin/snapshots` | admin | `snapshot.create` | snapshot ← response.name |
| POST | `/api/admin/snapshots/{name}/restore` | admin | `snapshot.restore` | snapshot ← path |
| DELETE | `/api/admin/snapshots/{name}` | admin | `snapshot.delete` | snapshot ← path |
| any write | `/api/admin/…`, `/api/system/…` | admin | `admin.write`, `system.write` | — |
| any write | anything else | **admin** (default) | `api.write` | — |

Other packages can declare a rule without editing this file: `roles.register_route(roles.rule("POST", "/api/x/{id}/y",
"evaluations.run", "x.y", "x", "path:id"))` at import time (inserted before the default rule). The test
`test_every_write_endpoint_of_the_app_has_its_own_rule` walks the OpenAPI schema of `cqa.main.app` and fails with the list of
write routes that only match the admin-only default rule.

Service events (no request): `job.completed`, `job.interrupted` (written by `cqa.jobs.runner.recover_stale_jobs`),
`snapshot.download` (admin package). Their actor is `system` (or the actor passed in).

## 5. Endpoints

### GET /api/roles

```json
{ "default_role": "demo",
  "headers": {"role": "X-CQA-Role", "user": "X-CQA-User", "request_id": "X-Request-ID",
              "user_encoding": "URL-encoded UTF-8 (encodeURIComponent); raw UTF-8 is accepted"},
  "roles": [{ "code": "operator", "label": {"ru": "Оператор", "en": "Operator", "kk": "Оператор"},
              "description": {"ru": "Видит свои диалоги и оценки, оспаривает оценки только своих диалогов.", "en": "…", "kk": "…"},
              "permissions": ["read", "appeals.create"], "home": "#/my",
              "nav": [{"key": "my", "route": "#/my", "label": {"ru": "Мои диалоги", "en": "My conversations", "kk": "Менің диалогтарым"}},
                      {"key": "checklists", "route": "#/checklists", "label": {"ru": "Чек-листы", "en": "Checklists", "kk": "Чек-листтер"}}],
              "constraints": {"appeals.create": "own_dialogs"}, "is_default": false }, …],
  "permissions": [{"code": "appeals.decide", "label": {"ru": "Решения по апелляциям", …}, "description": {…}}, …],
  "matrix": {"appeals.create": {"demo": true, "visitor": false, "operator": true, "analyst": true, "reviewer": true,
                                "lead": true, "director": false, "methodologist": false, "admin": true}, …},
  "nav_items": [{"key": "home", "route": "#/", "label": {…}}, …],
  "actions": [{"code": "appeal.decide", "label": {"ru": "Решение по апелляции", "en": "Appeal decided", "kk": "Апелляция бойынша шешім"}}, …],
  "write_routes": [{"methods": ["POST"], "path": "/api/datasets/import", "permission": "conversations.import",
                    "action": "dataset.import", "entity_type": "dataset", "audited": true}, …] }
```

### GET /api/me

`curl -H 'X-CQA-Role: operator' -H 'X-CQA-User: %D0%90%D0%BB%D0%B8%D0%BD%D0%B0' -H 'X-Request-ID: web-7f3a' /api/me`

```json
{ "role": "operator", "name": "Алина", "request_id": "web-7f3a", "display": "Алина (Оператор)",
  "label": {"ru": "Оператор", "en": "Operator", "kk": "Оператор"}, "description": {…},
  "permissions": ["read", "appeals.create"], "home": "#/my", "nav": [{"key": "my", "route": "#/my", "label": {…}}, …],
  "constraints": {"appeals.create": "own_dialogs"}, "is_default": false, "enforced": true }
```

`enforced` is true when the security middleware handled the request (always in `cqa.main.app`).

### GET /api/audit

`?action=&entity_type=&entity_id=&actor_role=&actor_name=&status_code=&q=&since=&until=&limit=100&before_id=`

- `action`: exact, comma list (`appeal.create,appeal.decide`) or prefix with a trailing `*` or `.` (`appeal.*`);
  `entity_type`, `actor_role`: exact or comma list; `actor_name`: exact (Cyrillic fine); `status_code=403` = refused attempts;
  `q`: substring of summary, entity id or actor name; `since` / `until`: ISO 8601 (`2026-11-07T12:00:00Z`).
- Newest first; `limit` clamped to 1..500 (default 100); pass `next_before_id` as `before_id` for the next page
  (`null` = last page). Bad numbers or dates → 422 `{"error": {"code": "validation_error", "message": "…", "errors": [{"field": "limit", "message": "…"}]}}`.

```json
{ "items": [
   { "id": 4, "ts": "2026-11-07T13:41:04.270115+00:00", "actor_role": "reviewer", "actor_role_label": "Ревьюер",
     "actor_name": "Иван", "action": "appeal.decide", "action_label": "Решение по апелляции",
     "entity_type": "appeal", "entity_id": "1",
     "summary": "Апелляция #1 (C3, диалог own-001): ответ изменён на «no»",
     "details": {"method": "POST", "path": "/api/appeals/1/decision", "path_params": {"appeal_id": "1"},
                 "body": {"action": "change", "new_answer": "no", "reason": "Идентификации не было", "add_to_fewshot": false},
                 "response": {"appeal.conversation_id": "own-001", "appeal.code": "C3", "appeal.decision.action": "change",
                              "appeal.decision.new_answer": "no", "appeal.decision.reviewer_role": "reviewer",
                              "evaluation.id": 1, "evaluation.total_score": 100.0, "evaluation.final_total_score": 80.0},
                 "duration_ms": 6.7},
     "request_id": "65b6dc6c8e42", "status_code": 200, "outcome": "ok" },
   { "id": 3, "actor_role": "operator", "actor_name": "Алина", "action": "appeal.create", "entity_type": null, "entity_id": null,
     "summary": "Отказано: Подача апелляции — Оператор может оспаривать только оценки своих диалогов",
     "details": {"method": "POST", "path": "/api/appeals", "body": {"evaluation_criterion_id": 11, "comment": "x"},
                 "error": {"code": "not_your_dialog", "message": "Оператор может оспаривать только оценки своих диалогов"},
                 "duration_ms": 1.0},
     "status_code": 403, "outcome": "denied", … } ],
  "next_before_id": 3 }
```

`outcome`: `ok` | `denied` (403) | `error`. `ts` is UTC with offset.

### GET /api/audit/facets

```json
{ "total": 4,
  "actions": [{"code": "appeal.create", "label": "Подача апелляции", "n": 2}, …],
  "entity_types": [{"code": "appeal", "n": 2}],
  "actor_roles": [{"code": "operator", "label": "Оператор", "n": 3}, …],
  "actor_names": [{"name": "Алина", "n": 3}, …] }
```

### GET /api/audit/export.csv (permission `admin`)

Same filters as `/api/audit`, `limit` up to 20000 (default 5000). UTF-8 with BOM (opens in Excel), columns
`id, ts, actor_role, actor_name, action, action_label, entity_type, entity_id, summary, status_code, outcome, request_id, details`
(details as JSON). Other roles: 403 `forbidden` (`permission: "admin"`); `demo` is allowed.

### Errors

| Status | Code | When |
|---|---|---|
| 400 | `unknown_role` | `X-CQA-Role` is not one of the 9 roles: `{"error": {"code": "unknown_role", "message": "Неизвестная роль «guest» в заголовке X-CQA-Role. Допустимые роли: demo, visitor, …", "role": "guest", "roles": [...]}}` |
| 403 | `forbidden` | Role lacks the route's permission: `{"error": {"code": "forbidden", "message": "Недостаточно прав: роли «Оператор» недоступно действие «Создание чек-листа» (нужно право «Редактирование чек-листов»).", "role": "operator", "permission": "checklists.write", "action": "checklist.create"}}` |
| 403 | `not_your_dialog` | Operator appeals a dialog whose `operator_name` is not theirs (or has no detected operator) |
| 403 | `operator_name_required` | Operator (by header) without `X-CQA-User` tries to appeal |
| 422 | `validation_error` | Bad `/api/audit` query parameters |

## 6. Appeals: authorship from the actor (`api/cqa/routers/appeals.py`)

`POST /api/appeals`:
- `author_role` / `author_name` missing in the body → taken from the actor. `author_label` not sent → `"Алина (Оператор)"`
  (name + role label; just the role label without a name). An explicit `author_label` (the current web app sends one) is kept.
- `X-CQA-Role: operator`: `author_role` is always `operator` and `author_name` is the header name (the body cannot lift the
  check); the dialog's `operator_name` must be the same person (case-insensitive, ё = е), otherwise 403 `not_your_dialog`; a
  dialog without a detected operator is nobody's; no name → 403 `operator_name_required`.
- Without a role header (`demo`, the deployed web app): the previous body-based check is unchanged (`author_role:
  "operator"` + `author_name` ≠ dialog operator → 403 `not_your_dialog`), `author_label` default stays `"QA analyst"`.

`POST /api/appeals/{id}/decision`: `reviewer_role` missing → the actor's role (not for `demo`); `reviewer_label` not sent →
`"Иван (Ревьюер)"`; an explicit label is kept; without headers the default stays `"Reviewer"`.

## 7. Audit service for other packages

```python
from cqa.audit import record
record(db, action="job.interrupted", entity_type="job", entity_id=job.id, summary="Задача … прервана перезапуском",
       details={"done": 3, "total": 10}, actor=None)          # actor None → actor_role "system"; commits by default
record(db, "x.y", actor=request)                              # a Request (or current_actor(request)) attributes it
record(db, "x.y", details={...}, commit=False)                # inside a larger unit of work (flush only)
```

Values are fitted to the columns (`action` 64, `entity_type` 32, `entity_id` 64, `actor_name` 128, `request_id` 64 since the
integration step, so a dashed UUID is stored intact), `summary` ≤ 500 characters, `details` made JSON-safe (datetimes, sets → strings) and capped at 16 KB.
`cqa.audit.compact(value)` / `compact_to_size(value, 2048)` / `scrub_text(text)` are available for services' own details.

Data model: the existing `audit_events` table (`models.AuditEvent`); no schema change. Rows are only appended.

## 8. Tests (`api/tests/test_roles_audit.py`, 55 tests, ~1.3 s, offline)

In-memory SQLite (StaticPool) behind the real routers (conversations, checklists, evaluations, appeals, settings, audio, audit,
roles) with `dependency_overrides[get_db]`; the audit session uses the same DB via `app.state.audit_session_factory`. The
Claude key is removed (fallback judge spec compiler), audio resolution is stubbed, and the middleware's fallback to the real
`SessionLocal` is replaced by a function that fails the test.

- No headers = `demo` with every permission; a demo write is recorded with entity, summary and transcript omitted.
- Visitor: every GET listed is 200, `POST /api/datasets/import` and `PUT /api/settings` are 403 with the exact envelope and the
  handler never runs; the refused attempt is recorded (status 403, permission, body summary).
- Unknown role → 400 on GET and POST, nothing recorded; case-insensitive role; empty header = demo; OPTIONS never refused;
  a UTF-8 role is echoed readably.
- `X-CQA-User` decoding: percent-encoded, raw UTF-8 bytes, UTF-8 mis-decoded as latin-1, latin-1 percent fallback, `100%`,
  whitespace, length cap; request id propagation and UUID fitting.
- Operator: 403 on `POST /api/checklists` (also with a trailing slash); appeals own dialog (200, author fields from headers,
  audit row with the name decoded from `%D0%90…`); 403 `not_your_dialog` on another operator's dialog, on a dialog without an
  operator and when the body claims another role/name; `operator_name_required`; case-insensitive name match.
- Analyst appeal defaults; methodologist cannot appeal; analyst cannot decide; reviewer decides (role/label from headers,
  final total recomputed, audit summary «… ответ изменён на «no»»); explicit labels kept; legacy body behaviour unchanged.
- Preview open to visitors and not recorded; dry run recorded as `evaluation.estimate`, run as `evaluation.run` with the job id;
  `job.cancel` entity from the path (also on a refused attempt).
- Methodologist creates a checklist, lead activates a version, director refused; entity ids from response and path.
- Secrets never stored (URL password, `sk-ant-…`, Bearer token, SAS `sig=`), long strings cut, body summary ≤ 2 KB even for a
  0.6 MB body; a refused 0.9 MB request is summarized from its first 64 KB.
- A 300-turn transcript reaches the app intact; with a tiny capture window the app still gets every byte and the summary keeps
  only the size; multipart upload recorded with its file name.
- Audit session from the `get_db` override when no factory is set; a failing audit write never breaks the request.
- SSE (`/api/jobs/{id}/events`) and audio Range/HEAD unchanged through the middleware; ASGI-level tests: reads get the
  original `receive`/`send` objects and the first SSE chunk leaves before the app finishes; writes forward intermediate chunks
  immediately and record the event before the last chunk.
- `/api/audit` filters (exact, prefix, comma list, entity, role, Cyrillic name, status, q, since/until), pagination,
  clamping, 422 envelope; facets; CSV export admin-only.
- `record()` limits and JSON safety; `compact`/`scrub_text` helpers.
- `/api/roles` equals the contract matrix cell by cell; homes, nav, RU/EN/KK labels everywhere.
- Route map table for every contract endpoint (incl. other packages' resume, test-criterion, calibration, Dynamics, snapshots);
  every write route of `cqa.main.app` has its own rule; installed exactly once in `cqa.main.app` through `cqa.platform`, and a
  refused request through the real app is recorded via the `get_db` override.

Live check (isolated uvicorn on port 8012, temp DB and data dir, API key blanked; stopped afterwards): raw UTF-8 `X-CQA-User`
parsed by uvicorn; SSE first event at 0.01 s and `job.done` only after the job changed in the DB (no buffering); Range → 206
with 100 bytes; visitor import → 403 with CORS and `X-Request-ID` headers; audited paste whose `request_id` equals the
response `X-Request-ID`; preflight allows `x-cqa-role, x-cqa-user, x-request-id`.

## 9. Limitations

- Soft permissions, not authentication: the headers are self-declared, and no header means `demo` with every permission
  (backward compatibility with the deployed web app, per the contract). It provides attribution, guardrails in the UI and an
  audit trail, not protection against a determined user.
- `read` is global: an operator can read every dialog through GET; the "own dialogs" rule applies to appeals.
- GET requests are not recorded (services may record semantic read events, e.g. `snapshot.download`). Redirects, 4xx other
  than 403 and 5xx are not recorded. `POST /api/appeals/preview` is not recorded.
- The audit write runs before the last response chunk: a few ms per write; under a SQLite write lock it waits up to the busy
  timeout (5 s) and then gives up (logged), it never fails the request.
- Bodies or responses over 1 MB keep only their size in the summary, so their entity id may be missing.
- Operator name matching is normalised (case, ё/е, spaces), not fuzzy: "Алина" ≠ "Алина К.".
- A new write endpoint without a rule falls to the default rule (`admin`): `demo` and `admin` can call it, other roles get 403
  until a rule is added (the coverage test reports it).

## 10. Integration requests

1. **docs/API.md**: add a "Roles and audit" section with `GET /api/roles`, `GET /api/me`, `GET /api/audit`,
   `GET /api/audit/facets`, `GET /api/audit/export.csv` (examples in §5), the request headers (§1) and the error codes
   `unknown_role`, `forbidden`, `operator_name_required` (§5); note that `POST /api/appeals` / `.../decision` default
   `author_role`, `author_name`, `author_label`, `reviewer_role`, `reviewer_label` from the headers (§6).
2. **docs/ARCH_CONTRACTS.md §1** (team lead): the sentence "the middleware also fills those from headers when the body omits
   them" is implemented in the appeals router (same effect; the middleware never rewrites bodies). §2: `GET /api/audit` also
   takes `actor_name`, `status_code`, `q`, `since`, `until`; refused attempts (403) are recorded with `status_code = 403`;
   only 2xx and 403 are recorded (not 3xx).
3. **docs/ARCHITECTURE.md**: request pipeline = request log → error handler → CORS → security middleware → routes; the audit
   trail lives in `audit_events` and is written by `cqa.security.middleware` and `cqa.audit.record`.
4. **api/cqa/models.py (optional; applied in the integration step: `String(64)` and the index)**: `AuditEvent.request_id` is `String(32)` while the platform request log accepts ids of up to
   64 characters; `record()` fits them (dashed UUIDs lose their dashes, longer ids are cut). Widening to `String(64)` would keep
   long client ids intact (SQLite does not enforce the length; on Postgres it needs `ALTER COLUMN`). An index on
   `(entity_type, entity_id)` would help if the table grows large.
5. **api/cqa/config.py (optional, not needed now)**: a `default_role` setting (env `DEFAULT_ROLE`, default `demo`) would let the
   public instance treat header-less requests as `visitor` once the web app sends role headers; `roles.normalize_role` would
   read it instead of the `DEFAULT_ROLE` constant.
6. **web/ (frontend owner)**: send `X-CQA-Role`, `X-CQA-User: encodeURIComponent(name)` and optionally `X-Request-ID` on every
   request; read labels, `home`, `nav` and the matrix from `GET /api/roles` and the current permissions from `GET /api/me`;
   hide or disable actions the role lacks (`write_routes` lists the permission per endpoint); show `error.message` of a 403/400;
   stop sending `author_label` / `reviewer_label` when a role is chosen, so the API stores "Имя (Роль)"; an audit page can use
   `/api/audit` + `/api/audit/facets`, and a conversation card can show its history with `/api/audit?q=<conversation_id>`.
   CORS already allows the headers (`allow_headers=["*"]`, verified with a preflight).
