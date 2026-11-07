# Frontend integration guide (for the redesign)

**For:** the frontend owner (`web/`). **From:** the backend/architecture owner.
**Goal:** every backend feature the redesign can use, which screen calls what, and the rules that keep the numbers on screen identical to `results.json`.

- **Where it runs.** Every endpoint below works on the local API (`cd api && .venv/bin/uvicorn cqa.main:app --port 8000`, `pnpm dev` proxies `/api`). It reaches the hosted API (`https://api-production-58ab4.up.railway.app`) with the next deploy; `GET /api/version` tells you which build answers.
- **Full payloads.** Exhaustive request and response examples live in `docs/API.md` and `docs/arch/<package>.md`. This file is the map.
- **Old app still works.** Nothing here breaks the current app: a request without role headers acts as the `demo` role with every permission.

---

## 1. Plumbing to do first

### 1.1 Send who is acting (role headers)

Add three headers in `request()` in `web/src/api/client.ts`. That one place covers every `get/post/put/postForm` call.

```ts
// web/src/api/client.ts, inside request(), right before `res = await f(url, init)`
const actor = loadActor(); // { role: "operator", name: "Алина" } from your store or localStorage
const headers = new Headers(init.headers);
if (actor?.role) headers.set("X-CQA-Role", actor.role);
if (actor?.name) headers.set("X-CQA-User", encodeURIComponent(actor.name)); // UTF-8 names must be URL-encoded
headers.set("X-Request-ID", `web-${crypto.randomUUID().slice(0, 12)}`);
init.headers = headers;
```

- `EventSource` (job progress) and `<audio src>` cannot send headers. Both are GET requests, and every role may read, so they need none.
- Every response carries `X-Request-ID`. Show it in error toasts ("код запроса …") so a failure can be found in the audit log and server log.
- Once a role is chosen, stop sending `author_label` / `reviewer_label`. The API then stores "Имя (Роль)" from the headers.

### 1.2 Errors

The body is always `{"error": {"code", "message", ...extra}}` and `message` is Russian, ready to show. `parseError()` in `client.ts` already reads it. Codes worth handling explicitly:

| HTTP | code | Where | What the UI should do |
|---|---|---|---|
| 400 | `unknown_role` | any | Reset the stored role |
| 403 | `forbidden` | writes | `error.role` / `error.permission`: hide or disable the action for that role |
| 403 | `not_your_dialog`, `operator_name_required`, `not_your_appeal` | appeals | Operator acting on someone else's dialog or appeal |
| 409 | `already_open` | `POST /api/appeals` | Open the existing appeal instead |
| 409 | `not_open` | withdraw | Refresh the appeal |
| 409 | `conversation_exists` | paste / upload | Offer «Сохранить как копию» (`on_conflict: "rename"`, shows `suggested_id`) or «Заменить» (`"replace"`) |
| 409 | `conversation_busy` | paste / upload | The dialog is being evaluated; retry after the job |
| 409 | `nothing_to_resume` | job resume | Hide «Возобновить» |
| 422 | `validation_error`, `invalid_checklist`, `invalid_criterion` | forms | `errors: [{field, message}]` with field paths like `criteria[2].weight`; show next to the field |
| 422 | `comment_required`, `reason_required`, `new_answer_required` | appeals | Field-level message (`errors[0].field`) |
| 429 | `budget_exceeded`, `too_many_jobs`, `too_many_dialogs` | run, resume, test-criterion | Show `message`; `Retry-After` header when known |
| 502 | `judge_failed`, `audio_unavailable`, `import_failed` | trial, audio, import | Show `message`, offer retry or the fallback (file upload, transcript-only mode) |

### 1.3 Numbers and labels (one truth with results.json)

- **Turn numbers are 1-based on screen.** The API keeps `Turn.idx`, `evidence[].turn_idx` and `checked_span.from_turn/to_turn` 0-based; display `idx + 1`. results.json `evidence_turns` uses the same `idx + 1`.
- **Explanation texts are already 1-based.** `explanation` and `na_reason` cite turns as «реплика [10]» or «реплики 10–14». The API shifts them from the judge's 0-based labels on output (in `/api/conversations/{id}/evaluation`, appeals, violations, the overview, scorecards, the PDF and results.json). Only the raw request and response in the Trace tab keep the judge's labels; mark that tab "сырые данные модели".
- **Search hits.** `match.turn` (1-based) and `match.turn_idx` (0-based) come with list search results.
- **Totals have two decimals.** The comma in RU/KK is the UI's job (`Intl.NumberFormat("ru-RU", {minimumFractionDigits: 2})`). Show `final_total_score`, which includes reviewer decisions; `total_score` is the AI's original.
- **Answers.** Binary answers are `"yes"` / `"no"`, scale answers are numbers (`20, 15, 10, 5, 0` for C4; `10, 7, 4, 0` for C6), and not applicable is the string `"N/A"`. For the gross-error criterion (C8, `is_critical`), `"yes"` means a violation.
- **Points per criterion.** `weight_applied` is the criterion's share of 100 after N/A redistribution. `final_earned` is what it earned. «Почему снижена оценка» = rows with `weight_applied − final_earned > 0`. «Как посчитан итог» = Σ `final_earned` = `final_total_score`.
- **Seek time.** `evidence[].offset_seconds` and `turns[].offset_seconds` are seconds into the audio file. For calls they count from the recording start. A turn with `offset_seconds: null` was said before the recording started (listed in `meta.not_in_recording`): show «не в записи» and disable seeking.

### 1.4 Mock mode

`src/mock/server.ts` only knows the endpoints that existed when it was written. New screens need fixtures, or run them against the local API (`?api=http://localhost:8000`).

---

## 2. Roles, people and navigation

- **Picker.** `GET /api/roles` returns, for each role, `label {ru,en,kk}`, `description`, `permissions`, `home` and `nav: [{key, route, label}]`. Build the picker and the side nav from it rather than hard-coding them.
- **Who am I.** `GET /api/me` echoes the resolved role, name, permissions and `display` ("Алина (Оператор)").
- **People.** For the operator persona picker, `GET /api/operators` returns `operator_name` per operator (56 names in DEV: Алина, Тимур, Лера, Нұрлан, Maya…; `"—"` means unknown). Reviewers and leads can type any name.
- **Gating.** `me.permissions` together with `roles.write_routes` (method, path → permission) tells you which buttons to hide or disable for the current role. The server enforces the same rules (403 `forbidden`).

| Role | home | Main nav |
|---|---|---|
| visitor (Гость) | `#/` | home, overview, conversations, violations, analytics |
| operator (Оператор) | `#/my` | my, checklists |
| analyst (QA-аналитик) | `#/conversations` | conversations, violations, review, operators, analytics, checklists |
| reviewer (Ревьюер) | `#/review` | review, conversations, violations, calibration, analytics, checklists |
| lead (Руководитель группы) | `#/overview` | overview, conversations, violations, review, operators, analytics, checklists, calibration, audit |
| director (Директор) | `#/overview` | overview, analytics, violations, operators |
| methodologist (Методолог) | `#/checklists` | checklists, calibration, conversations, violations, analytics |
| admin (Администратор) | `#/settings` | settings, admin, audit, overview, … |

---

## 3. Screens and their calls

### Landing `#/` (visitor home)
- **Live numbers:** `GET /api/overview?days=30` (`coverage`, `quality.mean`, `critical.count`, `cost.per_dialog_usd`), `GET /api/version`, and `GET /api/system/health` for the status dot.
- **Readiness badge (optional):** `GET /api/system/readiness` (§3.12).
- **API down:** the page must still render with static text when every call fails.

### Conversations `#/conversations`
- **List:** `GET /api/conversations` with these filters:
  - `channel`, `language`, `status` (`evaluated` | `not_evaluated` | `under_review` | `failed`), `min_total`, `max_total`, `critical`;
  - `q`, which searches the id, the operator and the transcript; a hit returns `match: {field, turn, turn_idx, speaker, snippet}`;
  - `dataset_id`, `operator`, `checklist_version_id`;
  - **new:** `criterion` + `answer` (`yes`, `no`, `15`, `N/A`, or `lost` for "the criterion lost points"), the drill-down behind every analytics number;
  - **new:** `sort`, one of `imported` | `id` | `total_asc` | `total_desc` | `evaluated_desc` | `first_response_desc`.
- **Failed retries:** the latest *completed* evaluation is shown even when a later attempt failed. `last_attempt_failed: true` with `last_attempt.error` means the UI should offer «Повторить» (run with `conversation_ids: [id]`).
- **Sets:** `GET /api/datasets` lists them; `POST /api/datasets/import {group_id}` imports by group id. The import response now includes `created / updated / unchanged / busy` counts and `conversation_ids`. Re-importing the same group never duplicates and never resets evaluations.
- **Upload:** `POST /api/conversations/upload` (multipart `file`, query `on_conflict`). The response has `{created, updated, unchanged, renamed, conversation_ids, items:[{requested_id, conversation_id, status}]}` for JSON, or the conversation plus `import_status` for a text file.
- **Paste:** `POST /api/conversations/paste {conversation_id?, text | transcript, channel, language?, on_conflict?, dry_run?}`. It returns the conversation plus `import_status`: `created` | `unchanged` | `renamed` | `updated`.
  - **Language.** Leave `language` out (or send `"auto"`) and it is detected from the client and operator turns. Detection matches the organizers' labels on all 65 DEV dialogs. A given language that the text contradicts comes back with `language_warning`.
  - **Preview (new).** `dry_run: true` stores nothing and returns `{import_status (created | unchanged | conflict | busy), suggested_id, language, language_detected, operator_name, first_response_seconds, turn_count, speakers, turns[{idx, turn, speaker, text, segment}], steering_turns, warnings[]}`. Show it as «Предпросмотр» before «Создать и оценить».
  - A different transcript under an existing id answers **409 `conversation_exists`** with `suggested_id`. Offer «Сохранить как копию» (`on_conflict: "rename"`) or «Заменить» (`"replace"`).
- **Evaluate:**
  1. Run `POST /api/evaluations/run {conversation_ids | all, checklist_version_id, provider, mode, base_version_id, only_codes, dry_run: true}` first.
  2. Show `estimated_cost_usd` against `budget.remaining`. If `guard` is set, show `guard.message` and disable «Запустить».
  3. Then run the same body without `dry_run`, follow `job_id` with `EventSource('/api/jobs/{id}/events')`, and handle `interrupted` + `resumable` with `POST /api/jobs/{id}/resume`.
- **Exports:** `GET /api/export/evaluations.csv?checklist_version_id=`.
- **Operator attribution (new):** `PUT /api/conversations/{id}/operator {operator_name}` (analyst, lead, methodologist, admin). It covers dialogs where the greeting named nobody (the «—» group) or the wrong person; `null` restores the detected name. The response carries `operator_name_source` (`manual` | `detected`).
- **Per-row attention (new):** each `latest_evaluation` carries `attention: {low_confidence[], split_votes[], failed[], contradicted_claims, needs_review}` (12 of 65 DEV dialogs) and `checklist_version_no` (show «v1», not the id).

### Conversation card `#/conversations/:id`
- **Data:** `GET /api/conversations/{id}` (turns, `meta`, `dataset_id`) and `GET /api/conversations/{id}/evaluation` (the latest one with criteria). History is `GET /api/conversations/{id}/evaluations`; the trace is `GET /api/evaluations/{id}/trace`.
- **Per criterion:** `answer`/`final_answer`, `weight_applied`, `earned`/`final_earned`, `explanation`, `evidence[]`, `checked_span`, `confidence`, `flags`, `claims[]` (C4 knowledge-base check: `consistent` | `contradicted` | `unverified`), `review_state`, `appeal`/`appeals[]`, `options` (with `anchor` texts for the scale).
- **Mockup blocks:** a ring with `final_total_score / 100`; rows «earned / weight»; «Почему снижена оценка» = losing rows with «Перейти к фрагменту» (scroll to `turn_idx`, seek to `offset_seconds`) and «Оспорить оценку».
- **Card history timeline:** `GET /api/audit?q=<conversation_id>`.
- **Exports:** PDF via `GET /api/export/evaluations/{evaluation_id}.pdf` (1-based turn numbers, 1-based explanation texts).

### Calls (card when `channel === "voice call"`)
- **Player:** `GET /api/audio/{id}` (Range).
- **Two-track waveform:** `GET /api/audio/{id}/peaks?buckets=600` returns `tracks.client / operator / other` and `spans[]`. The cache now refreshes itself when turn timing changes.
- **Features:** `GET /api/audio/{id}/features` (pace, pauses, holds, `summary_ru`).
- **Timing now matches the audio.** p4-ru-002 was off by 30 s before the fix.
- **«Не в записи».** Turns in `meta.not_in_recording` have no seek time; show them greyed.
- **Calls: opening outside the recording (new, 2026-11-08).** C1 has no N/A any more, because JSON 1.0 contact is true/false only.
  - When the operator's first turn is in `meta.not_in_recording`, the C1 row comes back as `answer: "yes"` with `flags` containing `rule_contact_outside_recording`.
  - The row also has `confidence: 1` and `evidence[0]` = that turn with `role: "context"`, without `offset_seconds`.
  - Show a small neutral badge on the row: «Засчитано правилом: начало звонка вне записи». The `explanation` already says why.
  - Banner text for the card (replaces the old «Правило организаторов для звонков: C1 = N/A»): «Запись началась после начала разговора оператора. «Установление контакта» засчитано по правилу: снижать балл можно только по фрагменту, который можно прослушать. Звонок отмечен как пробел записи.»
  - The appeal dialog keeps offering only Да/Нет for C1 (`na_allowed_for_channel` is false on calls now).
  - Optional analytics card «Пробелы записи звонков»: `GET /api/analytics/recording-gaps`. In DEV it shows 1 of 5 calls, 30 s unrecorded, C1 credited by rule on p4-ru-002. It shows the telephony team where recordings start late.

### Gross violations `#/violations` (live ask 1)
- **Data:** `GET /api/violations?type=&language=&channel=&operator=&include_reviewed_out=` returns `{total, counts, types:[{code, ru, en, kk}], items}`.
- **Item fields:** `types`, `type_labels`, `explanation` (1-based), `evidence[0]` (`quote`, `turn_idx`, `timestamp`, `offset_seconds`), `final_total_score`, `confirmed_by_reviewer`, `overturned_by_reviewer`.
- **Chips:** `counts` (DEV: insult 1, paid bypass 4, disclosure 3).
- **Row action:** «Перейти к фрагменту» → `#/conversations/:id` focused on the criterion C8 at the quoted turn.
- **Export:** `GET /api/export/violations.csv` (same filters).
- **More item fields (new):** `rules[{code, ru, en, kk}]` for «Почему так классифицировано», `classified_by` (`explanation` | `quote` | `identification` | `default`), `related.C3.answer`, `open_appeal_id`, `decision`.
- **More filters (new):** `dataset_id`, `since`, `until`.
- **«Проверено: не нарушение» (new):** `GET /api/violations/candidates` lists dialogs cleared of gross errors despite a warning sign: `signals` of `identification_failed`, `low_confidence`, `split_votes` or `steering`, each with the judge's explanation (3 in DEV). It answers the jury's «а точно других грубых ошибок нет?».

### Appeals `#/review` and the appeal dialog (live ask 2)
- **File:** `POST /api/appeals {evaluation_criterion_id, comment}`, with the role headers giving the author.
  - `comment` must not be empty after trimming (422 `comment_required`); at most 4000 characters.
  - An operator can file only on their own dialog (403 `not_your_dialog`).
- **Withdraw (new):** `POST /api/appeals/{id}/withdraw` works only on an open appeal. An operator can withdraw only their own appeal. The criterion goes back to its previous state.
- **Queue:** `GET /api/appeals`.
  - **Order:** open first, then decided, then withdrawn.
  - **Filters:** `status`, `operator`, `conversation_id`, `author_role`, plus **new** `code`, `language`, `channel`, `q`.
  - **Item fields:** `author_role`, `author_name`, `operator_name`, `options`, `answer_type`, `na_allowed_for_channel`, `evidence`, `explanation`, `total_score`, `final_total_score`. **New:** `instruction`, `confidence`, `weight_applied`, `earned`/`final_earned`, `checked_span`, `flags`, `votes`, `claims`, `is_latest_evaluation` (false = the appeal is on an older evaluation).
- **Live preview:** `POST /api/appeals/preview {evaluation_criterion_id, new_answer}` returns `{final_total_score, sections, critical_error}`.
- **Decide:** `POST /api/appeals/{id}/decision {action: "confirm"|"change", new_answer, reason}`.
  - `reason` (non-empty) and `new_answer` are required for `change`. A `new_answer` of `null` or `""` means N/A where the criterion allows N/A on that channel, otherwise 422 `new_answer_required`. Deciding a withdrawn appeal is refused (400 `invalid_decision`).
  - N/A is `"N/A"` (also accepted: `na`, `н/п`, `not applicable`).
  - Offer N/A when `na_allowed_for_channel` is true; for calls this includes C1.
- **Export:** `GET /api/export/decisions.csv?status=&operator=&author_role=&code=`.

### Operator portal `#/my`
- **Scorecard:** `GET /api/operators/{encodeURIComponent(name)}` returns totals, `team_mean_total`, `coaching_points[]` (with `expected` and examples), `strengths[]`, `violations`, `appeals.items[]` and `conversations[]`.
- **My dialogs:** `GET /api/conversations?operator=<name>`. **My appeals:** `GET /api/appeals?operator=<name>` (or `author_role=operator`).

### Overview `#/overview` (lead, director)
- `GET /api/overview?days=30&checklist_version_id=` returns `coverage`, `quality` (mean, median, p25/p75, share ≥ 85, share < 60), `critical.by_type`, `appeals` funnel, `agreement` per criterion, `by_language`, `by_channel`, `criteria[]` (`pass_rate`, `mean_ratio`, `na_rate`, `lost_points_share`), `top_failing[]` with an example, `trend[]` (one per day), `cost` (`per_dialog_usd`, `by_model`), `first_response` and `operators.at_risk`.
- **Drill-down:** every number links to the list via the new filters (`criterion` + `answer=lost`, `critical=true`, `language=`).
- **Scope (new):** `language`, `channel`, `dataset_id` and `operator` narrow every panel, with the same params on `GET /api/analytics/summary`. Example: KZ only gives 15 dialogs, mean 83,22, 2 gross violations. The response echoes `scope`.

### Operators `#/operators`, `#/operators/:name`
- `GET /api/operators?language=&channel=&dataset_id=` for the directory (mean, critical count, open appeals) and `GET /api/operators/{name}` for the scorecard.

### Checklist Studio `#/checklists`, `#/checklists/:id/versions/:vid` (live ask 3)
- **Read:** `GET /api/checklists`, `GET /api/checklists/{id}/versions/{vid}`. The version JSON includes `weights: {total, by_code, normalized}` (for «Нормализовать до 100») and `export_ids`.
- **Save:** `POST /api/checklists/{id}/versions {criteria, sections?, scoring_rules?, change_note, activate}`. `activate: false` saves a draft. 422 `invalid_checklist` lists `errors[]` per field; `warnings[]` come back after save.
- **What-if (new):** `POST /api/checklists/{id}/versions/{vid}/impact` with the draft body (+ `conversation_ids?`, `dataset_id?`, `basis: "final"|"ai"`).
  - **Response:** per-dialog `before → after → delta`, `summary` (mean before/after, how many go up or down, crossings of 60 and 85, gross-error counts), and `changes` (`weight_only`, `rejudge_codes`, `pending_codes`, `notes`).
  - **Cost:** zero model calls, about 20 ms for 65 dialogs; every role may call it.
  - **Use:** show it live while weights are edited ("изменение веса C7 с 5 на 10: 20 диалогов изменятся (↑8 ↓12), средний 83,10 → 82,72", hosted 08.11).
- **Test a draft criterion:** `POST /api/checklists/test-criterion {criterion, conversation_id}` returns the verdict, `judge_spec` (save it with the criterion) and `cost_usd`. Nothing is stored.
- **Diff:** `GET /api/checklists/{id}/versions/{a}/diff/{b}` adds `changed[].values {field: {old, new}}`, `added_criteria`, `removed_criteria`, `from_version_no`/`to_version_no` and `weights {from, to}`. **Activate:** `POST …/activate`.
- **Two editors:** send `base_version_id` (the version the editor opened) with a save. If someone saved meanwhile, the answer is 409 `version_conflict {latest_version_no}`.
- **Apply:**
  1. Dry run: `POST /api/evaluations/run {mode: "partial", base_version_id, checklist_version_id, conversation_ids, dry_run: true}`. Weight-only changes cost $0 (`rejudge_codes: []`).
  2. Run it. Reviewer decisions on unchanged criteria carry over.
- **Portable JSON:** `GET …/versions/{vid}/export?download=true` and `POST /api/checklists/import`.

### Calibration `#/calibration`
- `GET/PUT /api/calibration/labels`, `POST /api/calibration/import` (with dry-run preview), `GET /api/calibration/export`, `GET /api/calibration/agreement` (confusion matrices, disagreements), `GET /api/calibration/stability`. Details: `docs/arch/calibration.md`.

### Audit `#/audit`
- `GET /api/audit?action=&entity_type=&actor_role=&actor_name=&status_code=&q=&since=&until=&limit=&before_id=`, `GET /api/audit/facets`, `GET /api/audit/export.csv` (admin).

### Settings `#/settings` and Admin `#/admin`
- **Settings:** `GET/PUT /api/settings`. Appearance keys are `mascot_enabled` (default `false`), `default_theme` (`light`) and `team_name`. Writes require `settings.write` (admin).
- **Snapshots:** `GET/POST /api/admin/snapshots`, `POST /api/admin/snapshots/{name}/restore {confirm: name}`, `GET …/download`. Ask for `X-CQA-Admin-Token` when `token_required`. Details: `docs/arch/admin.md`.
- **System:** `GET /api/system/health` (`warnings[]`, `budget`, `jobs`), `GET /api/system/metrics`, `GET /api/version`, `GET /api/jobs`.
- **3.12 Readiness (new):** `GET /api/system/readiness?group_id=DEV` returns `{ready, ready_for_demo, summary, failed[], checks[{code, ok, severity, message, value}]}`.
  - **Checks:** organizer checklist, dataset size, coverage, results.json validity, running jobs, budget reserve, organizer URL, seeded appeals, gross violations, `baseline` snapshot, corporate style.
  - **Use:** one green/red panel for the demo morning.

### Results export dialog (analyst, lead, admin)
- **Validate:** `GET /api/export/results/validate?group_id=<group>&expect=<n>` returns `{valid, errors, warnings, summary, records, ok, error_records, model}`.
- **Download:** `GET /api/export/results.json?group_id=<group>&download=true`. The headers `X-Results-Valid/Errors/Warnings` repeat the check.

---

## 4. The four live asks: shortest paths

1. **Gross errors with explanations.** `#/violations`: chips with counts, then a row with type, quote and explanation, then «Перейти к фрагменту» into the card at C8.
2. **Operator appeals.**
   1. As Оператор · Алексей: `#/my`, then dialog p1-ru-029, then C7 «Оспорить оценку» with a comment.
   2. Switch to Ревьюер: `#/review` shows the appeal with the author. «Изменить» to «Нет» shows the preview (51,25 → 45,00), then save with a reason.
   3. On the card, original → final with the timeline (`/api/audit?q=p1-ru-029`).
3. **Add, change or reweight a criterion.** Studio:
   1. Change C7's weight; the live what-if (`…/impact`) updates.
   2. Add C9; «Проверить на диалоге» (`test-criterion`).
   3. «Сохранить как черновик» (`activate: false`), then the diff, then «Применить» (partial run, dry run first), then activate.
4. **Dialog result with justification.** The card: ring, «Почему снижена оценка», evidence jumps, N/A reasons, «Как посчитан итог»; for p4-ru-002, the waveform with the cited fragment and the C1 rule explanation: «Да», because the opening is outside the recording. Flag `rule_contact_outside_recording`; see «Calls: opening outside the recording» below.

---

## 5. What changed in the backend in this build

All changes are additive except the three behaviour fixes marked ⚠.

- ⚠ Explanation texts (`explanation`, `na_reason`) cite turns 1-based everywhere people read them, including results.json `rationale`.
- ⚠ Paste and upload no longer overwrite an existing dialog with a different transcript: 409 `conversation_exists` unless `on_conflict` is `rename` or `replace`. An identical paste returns `import_status: "unchanged"`.
- ⚠ Call offsets count from the recording start. Turns before it have `offset_seconds: null` and are listed in `meta.not_in_recording`. Evidence seek times follow the current transcript.
- **Appeals:**
  - New: `POST /api/appeals/{id}/withdraw` and the status `withdrawn`.
  - Comment and reason validation; N/A spellings accepted.
  - Queue filters `code`, `language`, `channel`, `q`, and the order open → decided → withdrawn.
- **List:** `criterion` + `answer` (incl. `lost`), `sort`, `checklist_version_id`, `status=failed`, `match` snippets, `last_attempt_failed`.
- **Import:** re-import is idempotent (`created / updated / unchanged / busy`, `conversation_ids`). A changed transcript sets `meta.transcript_changed_at`.
- **Gross violations:** the gross-error criterion comes from the checklist (`is_critical`), not a hard-coded C8. A violation type named only in a negated clause («оскорблений не было») no longer counts.
- **New endpoints:** `POST /api/checklists/{id}/versions/{vid}/impact`, `GET /api/system/readiness`, `GET /api/export/violations.csv`, `GET /api/export/decisions.csv`, `GET /api/violations/candidates`, `PUT /api/conversations/{id}/operator`.
- **Scope params:** on `/api/overview`, `/api/analytics/summary`, `/api/operators` and `/api/violations`.
- **Paste:** `dry_run` preview and automatic language.
- **Judge facts for calls:** the judge gets the recording start. Since 2026-11-08, C1 on a call whose opening is outside the recording is decided by a code rule (Да, flag `rule_contact_outside_recording`), never N/A.
- **`CQA_HEADERLESS_ROLE` (deployment variable):** set it to `visitor` once your app sends role headers. Header-less API calls then become read-only.

---

## 6. If the current web app is still the one deployed at demo time

A contract check ran the current `web/` app's exact requests against the new API. Reads are all compatible. The backend absorbs these changes:
- **Appeal comments:** any non-empty comment is accepted.
- **Change reasons:** any non-empty reason is accepted.
- **N/A decisions:** `new_answer: null` counts as N/A where the criterion allows N/A on that channel.
- **JSON uploads:** a single-record upload returns `id`.

What is left needs these small web changes:

| Where | Change | Why |
|---|---|---|
| `web/src/features/card/Transcript.tsx:106`, `web/src/features/card/CriterionRow.tsx:121` | show `turn.idx + 1` / `turn_idx + 1` | explanations now cite turns 1-based (as results.json does); 0-based chips would point one turn off |
| `web/src/features/jobs/useJobProgress.ts:18`, `web/src/features/jobs/JobProgress.tsx:35` | add `"interrupted"` to the terminal statuses (optionally a «Возобновить» button: `POST /api/jobs/{id}/resume`) | a job running during a deploy restart becomes `interrupted`; the card otherwise never closes |
| `web/src/features/checklists/CriterionEditor.tsx:113`, `web/src/features/checklists/model.ts:59` | merge the voice override instead of replacing it: `{...c.channel_overrides, "voice call": {...c.channel_overrides?.["voice call"], instruction}}`; keep overrides that have `na_allowed` without an instruction | editing C1's voice instruction today silently drops the organizers' C1 = N/A rule for calls |
| `web/src/features/conversations/UploadDialog.tsx:34` | on 409 `conversation_exists`, offer «Сохранить как копию» (`on_conflict: "rename"`, shows `suggested_id`) or «Заменить» (`"replace"`) | paste and upload never overwrite a different transcript silently any more |
| `web/src/features/checklists/ChecklistEditorPage.tsx:118` | disable «Пересобрать спецификацию» on versions that have evaluations, or confirm and send `?force=true` | 409 `version_in_use` protects the organizer v1, which results.json is pinned to |
| `web/src/mock/fixtures/conversation_details.json` | replace the real call transcript with a synthetic one | the repository is public; the call recordings and transcripts are real (PII is masked, but names spelled out by ASR remain) |

## 7. New API fields for the redesign (2026-11-08 night, build after 876c1de)

An audit of the work packages in `docs/BUILD_BACKLOG.md` against the API found gaps, and they are now closed. **Everything is additive:** no field was renamed, removed or retyped, so the current web app keeps working. Timestamps now carry an explicit `+00:00`; it is the same instant, and `fmtDate` already handles the offset.

**Card: `GET /api/conversations/{id}/evaluation`, `GET /api/evaluations/{id}`, and the `evaluation` returned by a decision.** Each `criteria[]` row adds:
- `section`: the section code, for grouping rows with subtotals.
- `is_critical` and `critical_value`: the C8 row and the gross strip, with no hard-coded C8.
- `na_allowed` and `na_allowed_for_channel`: offer N/A in the appeal dialog only when the second is true. C1 on calls gives `false`.
- `answer_option` and `final_option`:
  - the AI and final answers, typed like `options[].value` («Да» → `"yes"`, `"15"` → `15`);
  - `"N/A"` for a non-applicable row;
  - use these to look up anchors and to disable the current option. `answer` and `final_answer` are unchanged.
- `final_breakdown` {`applicable`, `weight_applied`, `earned`}:
  - present on every row as soon as any row has a decision, `null` otherwise;
  - drives the «После решения» column: the rows add up to `final_total_score` even when a decision toggles N/A.

The evaluation also gets an `attention` block: `low_confidence`, `split_votes`, `failed`, `contradicted_claims`, `needs_review`.

**Card states: `GET /api/conversations/{id}`.**
- `evaluation_in_progress` {`evaluation_id`, `job_id`, `status`, `started_at`} or `null`.
- Show «Оценивается…» instead of treating the 404 from `…/evaluation` as "not evaluated".

**Gross violations: `GET /api/violations`.**
- `counts_all` and `total_all`: chip numbers before the type filter. `counts` and `total` keep their filtered meaning.
- `evaluated`: «проверено N».
- `checklist_versions` [{`id`, `version_no`}] and `generated_at`: «по данным на HH:MM».
- New query param `conversation_id`: the card's gross strip, one call with the type and quote.

**Appeal objects (list, item, create, withdraw, decision).**
- `is_critical` and `critical_value`: queue order, gross-error appeals first.
- `evidence[].offset_seconds` for calls: mini-player seek.

**Analytics: `GET /api/analytics/summary`.**
- `bands` {`critical` < 60, `attention` 60–85, `norm` ≥ 85}: the distribution legend.
- `per_evaluation` {`n`, `mean_cost_usd`, `mean_latency_ms`}: one full evaluation, partial runs excluded. This is the «≈ $0,22 за оценку · ≈ 15 с» figure for the landing and the «Модель и стоимость» block.
- Query param `basis=final|ai` (default `ai`), echoed as `basis`: the heatmap and N/A rates with reviewer decisions applied.

**Overview: `GET /api/overview`.** `cost.per_evaluation_usd` (the same figure as above) and `cost.evaluations_completed`.

**Export dialog: `GET /api/export/results/validate`.**
- `schema_version` and `team_name`.
- `issues` [{`conversation_id` | null, `severity`, `message`}]: errors and warnings with links to the dialogs.

**Checklist versions** (every version JSON, and `versions[]` in the checklist list):
- `version_id` (= `id`).
- `evaluations`: the count of completed evaluations. Open v1, and any version that has evaluations, read-only.

**Downloads:** CSV and PDF responses expose `Content-Disposition`, so a fetch-based download gets the file name.

**Card 404s.** The `code` stays `not_found`; new: `reason` and a Russian `message`.
- `conversation_not_found`: «Диалог не найден в CQA», for the embed and card states.
- `no_evaluation`: «Оценки пока нет».
- `evaluation_not_found`.

**Jobs:** `started_at`, `finished_at` and `created_at` now carry `+00:00`.

**Ops: `GET /api/system/health`.** `guards` {`daily_budget_usd`, `max_dialogs_per_run`, `max_concurrent_jobs`}. Readiness's `baseline_snapshot` check now reports `baseline_age_minutes`.
