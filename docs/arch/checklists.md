# Package `checklists`: checklist governance

Covers two live asks from the jury: "add a new criterion, change an existing one or its weight" and the control-check task "replace one criterion and apply to processed dialogs" (docs/ORGANIZER_UPDATES.md §2–3). Contract: docs/ARCH_CONTRACTS.md §4–5.

| File | Role |
|---|---|
| `api/cqa/checklist/validation.py` | `validate_checklist_payload(payload) -> [{field, message}]`; `prepare_checklist_payload` (normalised copy + errors + warnings); `prepare_criterion_payload` (one criterion); export ids |
| `api/cqa/checklist/service.py` | validation in `create_checklist` / `create_version`, `activate` flag, judge_spec reuse across versions, concurrent spec compilation before any write, `weights` / `export_ids` in the version JSON, `export_version`, `run_criterion_test` |
| `api/cqa/routers/checklists.py` | 422 envelope, `activate`, `GET …/export`, `POST /api/checklists/import`, `POST /api/checklists/test-criterion`; Russian 404 messages |
| `api/tests/test_checklist_governance.py` | 88 tests (84 pass, 4 strict `xfail` that document runner findings) |

No database schema change. Every existing endpoint and field is unchanged. New fields are additive.

---

## 1. Endpoints

### 1.1 `POST /api/checklists`: new checklist (version 1)

Body as before (`name, description, scoring_rules, sections, criteria, change_note, compile_specs`) plus `activate` (default `true`). The payload is validated and normalised (§2) before anything is written. Criteria without `judge_spec` are compiled, all at once and before the first database write (§3.3). Response: `ChecklistVersion` (§1.3) plus `warnings`.

```json
{ "id": 3, "checklist_id": 2, "version_no": 1, "is_active": true, "change_note": "v1",
  "scoring_rules": {…}, "sections": [{"code": "S1", "title": "…"}, …], "criteria": [Criterion, …],
  "weights": {…}, "export_ids": {…},
  "warnings": [{"field": "criteria[8].section", "message": "Раздел «S5» не найден в sections — создан автоматически с названием «S5». Переименуйте его при необходимости."}] }
```

### 1.2 `POST /api/checklists/{id}/versions`: new version

Body: same shape plus `activate` (default `true`).

- `activate: true` (default, current behaviour): the new version becomes the active one and every other version of this checklist is deactivated.
- `activate: false`: saved as a **draft**. The current active version stays active; the draft is activated later with `POST /api/checklists/{id}/versions/{vid}/activate`.
- `sections` and `scoring_rules` omitted or empty: inherited from the latest version, as before. Validation checks section references and export ids against what will actually be stored.
- **judge_spec reuse**: a criterion sent without `judge_spec` (Checklist Studio strips it) keeps the spec of an earlier version when its judge-relevant fields (title, instruction, answer_type, options, N/A rule, channel overrides, gross-error flag/value) are identical. The previous version is searched first, then every older version of the checklist, newest first. An edit that is later reverted therefore gets the reviewed spec back instead of a fresh compile. Only new or changed criteria are compiled (unless `compile_specs: false`).
- 404 `not_found` takes precedence over 422 when the checklist does not exist.

```http
POST /api/checklists/1/versions
{ "change_note": "черновик: +C9 персонализация", "activate": false,
  "criteria": [ …8 stored criteria without id/judge_spec…,
    {"code": "C9", "section": "S3", "title": "Персонализация", "answer_type": "binary", "weight": 10,
     "instruction": "Да — оператор хотя бы раз обратился к клиенту по имени…", "na_allowed": true,
     "na_condition": "Клиент не назвал своё имя в диалоге.", "judge_spec": {…from test-criterion…}} ] }
→ 200 { "id": 2, "version_no": 2, "is_active": false, "created_from_version_id": 1,
        "weights": {"total": 110.0, …}, "export_ids": {…, "C9": "c9"},
        "warnings": [{"field": "criteria", "message": "Сумма весов 110, а не 100: итоговый балл всё равно нормируется к 100 пропорционально весам."}] }
```

### 1.3 Version JSON additions (every endpoint that returns a `ChecklistVersion`)

```json
"weights": {
  "total": 110.0,
  "by_code":    {"C1": 10.0, "C2": 15.0, "C3": 20.0, "C4": 20.0, "C5": 15.0, "C6": 15.0, "C7": 5.0, "C8": 0.0, "C9": 10.0},
  "normalized": {"C1": 9.09, "C2": 13.64, "C3": 18.18, "C4": 18.18, "C5": 13.64, "C6": 13.64, "C7": 4.55, "C8": 0.0, "C9": 9.09}
},
"export_ids": {"C1": "contact", "C2": "discovery", "C3": "identification", "C4": "consultation",
               "C5": "empathy", "C6": "dialogue", "C7": "closure", "C8": "critical", "C9": "c9"}
```

- Weights are relative. The scoring engine always rescales applicable weights to 100 and redistributes N/A, so a checklist whose weights sum to 110 scores exactly like its `normalized` form. `normalized` is `weight × 100 / total`, rounded half-up to two decimals; the UI can show it and offer "normalise to 100".
- `export_ids` = `scoring_rules.export_ids[code]` when set, else the organizer id for C1..C8 (results.json "JSON 1.0"), else `code.lower()`. `ORGANIZER_EXPORT_IDS` equals `cqa.export.results_json.DEFAULT_EXPORT_IDS`; a test checks this. Export ids must be unique within a version (validated).

### 1.4 `GET /api/checklists/{id}/versions/{vid}/export[?download=true]`: portable JSON

Accepted as is by `POST /api/checklists`, `POST /api/checklists/import` and `POST /api/checklists/{id}/versions`. Criteria carry their `judge_spec`, so an import compiles nothing. `download=true` adds `Content-Disposition: attachment; filename="checklist-1-v2.json"`.

```json
{ "format": "cqa.checklist/1",
  "name": "Freedom Broker · Чек-лист качества чатов", "description": "…",
  "scoring_rules": {…}, "sections": [{"code": "S1", "title": "Начало и понимание запроса"}, …],
  "criteria": [{"code": "C9", "order": 8, "section": "S3", "title": "Персонализация", "instruction": "…",
                "answer_type": "binary", "options": [{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}],
                "max_points": 1.0, "weight": 10.0, "na_allowed": true, "na_condition": "…", "is_critical": false,
                "critical_value": null, "channel_overrides": {}, "judge_spec": {…}}, …],
  "exported_from": {"checklist_id": 1, "version_id": 2, "version_no": 2, "change_note": "…", "exported_at": "2026-11-07T13:37:22+00:00"} }
```

`format` and `exported_from` are ignored by the create endpoints. 404 `not_found` when the version does not exist or belongs to another checklist.

### 1.5 `POST /api/checklists/import`: new checklist from an export file

The roles route map already has a rule for it (`checklist.import`). Body: the export JSON, or any checklist JSON with `criteria`. `format`, when present, must be `cqa.checklist/1` (otherwise 422 with field `format`). The default change note is `"Импорт: чек-лист #1, версия 2"` (from `exported_from`) or `"Импорт из файла"`; `change_note`, `compile_specs` and `activate` work as in §1.1. Same validation and response as §1.1.

### 1.6 `POST /api/checklists/test-criterion`: judge one criterion on one dialog, nothing stored

```http
POST /api/checklists/test-criterion
{ "criterion": {"code": "C9", "title": "Персонализация", "answer_type": "binary", "weight": 10,
                "instruction": "Да — оператор хотя бы раз обратился к клиенту по имени…",
                "na_allowed": true, "na_condition": "Клиент не назвал своё имя в диалоге."},
  "conversation_id": "live-gov-001",
  "provider": "anthropic",            // anthropic | local | fake | ensemble; default: settings.default_provider
  "model": null,                       // optional model override
  "compile_spec": true }               // compile a judge_spec with the LLM when the criterion has none (default)
```

Response (real run on the local API, Opus judge + Sonnet compiler; trimmed):

```json
{ "verdict": { "criterion_code": "C9", "applicable": true, "answer": "yes", "confidence": 0.97, "na_reason": "",
               "evidence": [{"turn_idx": 4, "speaker": "operator", "quote": "Спасибо, Ержан!", "role": "supports"}, …],
               "explanation": "Персонализация — Да. Оператор обратился к клиенту по имени после того, как клиент представился. Основание — реплика [4] «Спасибо, Ержан!» и реплика [6] «Рада помочь, Ержан!».",
               "checked_span": null, "step_results": ["…"], "flags": ["possible_factual_claim"], "claims": [{…}] },
  "answer": "yes", "applicable": true, "confidence": 0.97, "score": 1.0, "points": 1.0, "max_points": 1,
  "judge_spec": { "definition": "…", "decision_steps": ["…"], …, "_compiled_by": "claude-sonnet-5-5" },
  "judge_spec_source": "compiled",     // provided | compiled | fallback
  "cost_usd": 0.021344, "latency_ms": 6358, "model": "anthropic:claude-opus-5-5", "provider": "anthropic",
  "conversation_id": "live-gov-001", "code": "C9", "votes": null, "issues": [],
  "input_tokens": 1891, "output_tokens": 689, "warnings": [], "persisted": false }
```

- The criterion goes through the same per-criterion validation as a checklist (§2), so the saved version will accept exactly what was tested.
- Judging reuses the production path in `cqa.judge.runner`: `_runtime_settings`, `_members` (provider members, ensemble), `_to_norm_turns` with the same PII-masking rule (masked unless `local`), `_criterion_pack` (built from a transient, never-added `Criterion` row), `_load_fewshots`, `reference_facts_for`, and `judge_criterion` (validation, retry, vote, arbiter). `ConversationPack` is built exactly as `evaluate_conversation` builds it. No `Evaluation`, `EvaluationCriterion` or `LlmTrace` row is written (tested).
- `fake` resolves `get_provider("fake")` directly (the runner's `_members` does not route `fake` yet) and uses the deterministic fallback spec, so it is free and offline. `compile_spec: false` also uses the fallback spec.
- The returned `judge_spec` can be saved with the criterion (`criteria[i].judge_spec`), so the version uses exactly the tested procedure and no second compile happens (tested).
- `answer` / `applicable` / `confidence` / `score` / `cost_usd` are also top-level, because the audit route map captures them (`checklist.criterion.test`).
- Budget guard: for paid providers (`anthropic`, `ensemble`) the call is refused with 429 `budget_exceeded` when evaluation spend in the last 24 h plus the estimate ($0.05 × members) exceeds `DAILY_BUDGET_USD`. `local` and `fake` are never capped.
- Timing seen live: about 17 s to compile the spec, then about 6 s to judge. With a given spec, or `compile_spec: false`, only the judge call remains.

### 1.7 Errors (envelope `{"error": {"code", "message", …}}`, messages in Russian)

| Status | code | When | Extra |
|---|---|---|---|
| 422 | `invalid_checklist` | create / version / import payload fails validation; unknown import `format` | `errors: [{field, message}]`, `warnings` |
| 422 | `invalid_criterion` | test-criterion: criterion invalid, `conversation_id` missing, unknown `provider` | `errors`, `warnings` |
| 422 | `empty_conversation` | test-criterion on a dialog without turns | |
| 404 | `not_found` | checklist / version / criterion / conversation does not exist | |
| 400 | `provider_unavailable` | provider cannot be created (e.g. unknown to `get_provider`) | |
| 429 | `budget_exceeded` | test-criterion with a paid provider over the daily budget | `budget: {spent_24h, limit, estimate}` |
| 502 | `judge_failed` | the judge returned no usable verdict (all members failed) | `cost_usd`, `latency_ms`, `model` |

Example: `{"error": {"code": "invalid_checklist", "message": "Чек-лист не прошёл проверку: 3 ошибки. Укажите название критерия.", "errors": [{"field": "criteria[0].title", "message": "Укажите название критерия."}, {"field": "criteria[1].weight", "message": "Критерий грубой ошибки не начисляет баллы и отмечается флагом отдельно: укажите вес 0."}, …], "warnings": […]}}`

---

## 2. Validation rules (`cqa/checklist/validation.py`)

All problems are collected and returned in one answer. Field paths: `criteria[2].weight`, `criteria[0].options[1].value`, `criteria[0].channel_overrides["voice call"].na_condition`, `sections[1].code`, `scoring_rules.export_ids.C9`; test-criterion uses `criterion.…`.

| Rule | Field | Message (abridged) |
|---|---|---|
| criteria is a non-empty list | `criteria` | «Добавьте хотя бы один критерий.» |
| each criterion is an object | `criteria[i]` | «Критерий должен быть объектом…» |
| code matches `^[A-Za-z][A-Za-z0-9_-]{0,15}$` | `.code` | «Код «9C» не подходит: он начинается с латинской буквы…» |
| codes unique (case-insensitive, export ids default to `code.lower()`) | `.code` | «Код «C1» уже используется в критерии №1…» |
| title non-empty, at most 200 characters | `.title` | «Укажите название критерия.» |
| answer_type `binary` or `scale` (missing means binary) | `.answer_type` | «Тип ответа: «binary» (Да/Нет) или «scale»…» |
| binary: exactly yes/no; points numeric ≥ 0; «Нет» ≤ «Да» | `.options`, `.options[j].value/points` | «Для типа «Да/Нет» нужны ровно два варианта…» |
| scale: ≥ 2 steps, numeric distinct values, points numeric ≥ 0, anchor is text | `.options`, `.options[j].…` | «Шкала должна содержать не меньше двух шагов…» |
| weight > 0 needs a best option worth > 0 points (not reported for gross errors) | `.options` | «Ни один вариант не даёт баллов…» |
| weight numeric ≥ 0 (booleans rejected) | `.weight` | «Вес должен быть числом…», «Вес не может быть отрицательным.» |
| gross error ⇒ binary and weight 0; critical_value yes/no | `.answer_type`, `.weight`, `.critical_value` | «…укажите вес 0.» |
| at least one criterion with weight > 0 | `criteria` | «Хотя бы один критерий должен иметь вес больше 0…» |
| na_allowed ⇒ na_condition non-empty | `.na_condition` | «Опишите условие N/A…» |
| channel_overrides keys only `agent chat` / `voice call` (case and `_` normalised); fields only instruction / na_allowed / na_condition; override N/A needs a condition (override's or base) | `.channel_overrides["…"]…` | «Канал «email» не поддерживается…» |
| booleans (`na_allowed`, `is_critical`) are booleans | `.na_allowed` | «Ожидается true или false.» |
| order is an integer | `.order` | «Порядок должен быть целым числом.» |
| judge_spec (when given) matches `JudgeSpec` | `.judge_spec` | «judge_spec не соответствует схеме…» |
| sections: object with code ≤ 16 characters, unique | `sections[i].code` | «Раздел «S1» указан дважды.» |
| criterion section code ≤ 16 characters | `.section` | «Код раздела длиннее 16 символов.» |
| scoring_rules (when given): total_scale > 0, rounding.decimals 0..6, critical.mode flag_only / flag_and_cap, export_ids values non-empty | `scoring_rules.…` | |
| export ids unique within the version | `criteria[i].code` | «Идентификатор выгрузки results.json «contact» уже занят…» |
| name / description are text, name ≤ 200 | `name`, `description` | |

Warnings (saved anyway): section referenced but missing (auto-created `{code, title: code}`), weights not summing to 100, `max_points` replaced, empty instruction, `scoring_rules.export_ids` key without a criterion.

Normalisation (what is stored): binary options become `[yes, no]` with labels «Да»/«Нет» and points 1/0 when missing (0/0 for a gross-error criterion, like the organizer C8); Russian or boolean values (`Да`, `нет`, `true`) map to yes/no. Scale values and points become numbers ("15" → 15), points default to the value, labels default to the value, empty anchors are dropped, steps are sorted from the top points down (the judge prompt lists anchors in that order and `validate_verdict` treats `options[0]` as full marks). `max_points = max(points)`. Gross error gets `critical_value = "yes"`. Normalisation keeps already valid stored data byte-for-byte in its judge-relevant fields: ints stay ints, unknown option keys are kept, text is never trimmed. Re-saving an unchanged criterion therefore keeps its signature and its judge_spec (tested on the organizer seed: valid, no warnings, idempotent).

---

## 3. Data and behaviour notes

1. **Schema**: unchanged. Export id overrides live in `scoring_rules.export_ids` (JSON column). Warnings are not stored.
2. **Seed**: `ensure_seed` validates the organizer checklist. If a future seed ever fails validation, start-up logs a warning and stores it unvalidated, so a fresh deploy never fails to start.
3. **Compilation**: missing specs are compiled concurrently (`asyncio.gather` over `compiler.compile_spec_async`) and **before** any row is flushed, so the SQLite write lock is never held during LLM calls. The old code flushed the version first and could block job progress writes past `busy_timeout` (5 s). There is a test for this ordering. The compiler never raises: on error, or without a key, it returns the deterministic fallback spec.
4. **Activation**: `_create_version` and `activate()` query a checklist's versions explicitly instead of trusting the possibly stale `checklist.versions` collection in a long-lived session. `created_from_version_id` is still the latest version (unchanged meaning).
5. **Several checklists**: every `POST /api/checklists` or import creates a checklist with its own active version. `GET /api/checklists/active` and the evaluation default (`svc.active_version`) return the first active version by row order. This behaviour predates the package; see integration request 7.

## 4. Control-check playbook: "replace one criterion and apply to processed dialogs"

1. *(optional, ≈ $0.02 per dialog plus one compile)* `POST /api/checklists/test-criterion` with the new criterion (e.g. `C6b`) on one or two processed dialogs. Check the verdict and keep `judge_spec`.
2. `POST /api/checklists/{id}/versions` with the stored criteria, `C6` replaced by `C6b` (with the tested `judge_spec`). Use `activate: false` to prepare it before the control set arrives, then `…/activate`.
3. `GET /api/checklists/{id}/versions/{v2}/diff/{v3}` gives `added: ["C6b"], removed: ["C6"], rejudge_codes: ["C6b"]`.
4. `POST /api/evaluations/run {"mode": "partial", "base_version_id": <old>, "checklist_version_id": <new>, "conversation_ids": [...]}`: only `C6b` is judged, the other rows are copied (`flags: ["copied_from_previous_version"]`), totals and sections are recomputed with the new weights.

Verified with `cqa.judge.runner.evaluate_conversation` and a stub judge (tests in §6):

| Step | Judged | Weights | Total |
|---|---|---|---|
| v1 organizer checklist, full | C1..C8 | 100 | 100.00 |
| v2 = v1 + C9 (weight 10, answer «Нет») | C9 only | 110 → normalised | 90.91 |
| v3 = v2 with C6 → C6b (weight 15, answer «Нет») | C6b only; C6 absent | 110 | 77.27 |
| weight-only change C9 10 → 5 | nothing ($0) | 105 | 95.24 (from v2) |

Caveat for results.json: the organizer file has fixed ids and value domains (`dialogue` ∈ {0, 4, 7, 10}). Map a replacement to an organizer id via `scoring_rules.export_ids` only when its answers have the same domain. results.json does not read version export ids yet (integration request 5).

## 5. Permissions and audit (enforced by the roles middleware, `cqa/security/roles.py` ROUTE_MAP)

| Endpoint | Permission | Audit action |
|---|---|---|
| `GET /api/checklists…` incl. `…/export` | `read` | — |
| `POST /api/checklists` | `checklists.write` | `checklist.create` |
| `POST /api/checklists/import` | `checklists.write` | `checklist.import` |
| `POST /api/checklists/{id}/versions` | `checklists.write` | `checklist.version.create` (captures `is_active`) |
| `POST …/versions/{vid}/activate` | `checklists.write` | `checklist.version.activate` |
| `POST …/judge-spec/{code}/regenerate` | `checklists.write` | `checklist.spec.regenerate` |
| `POST /api/checklists/test-criterion` | `checklists.write` | `checklist.criterion.test` (captures answer, score, applicable, confidence, cost_usd) |

Checked live on the local API (port 8013, throwaway database): `visitor` gets 403 on test-criterion and the refusal is audited. The methodologist's trial, draft version, import and activation appear in `/api/audit` with the captured fields. The router does no permission checks of its own.

## 6. Tests (`api/tests/test_checklist_governance.py`)

Offline by construction: an autouse fixture clears `ANTHROPIC_API_KEY` (the repo `.env` sets a real one), replaces `compiler.compile_spec_async` with a recording fake, and makes `judge_runner.get_provider` refuse unless a test installs the stub judge. Every database is an in-memory SQLite (`StaticPool` + `dependency_overrides[get_db]`; `cqa.judge.runner.SessionLocal` / `cqa.jobs.runner.SessionLocal` are patched for runner flows).

- Validation: seed valid, canonical and idempotent (judge signatures unchanged); binary / scale normalisation; auto-created section and channel-key normalisation; inherited sections; warnings; **48 parametrised invalid cases**, one per rule, each checking the field path and a Russian message; all errors returned at once; zero-weight rule next to unrelated errors; single-criterion rules.
- Weights and export ids: summary for 100 and 110, organizer ids, parity with `results_json.DEFAULT_EXPORT_IDS`, overrides, clash detection.
- judge_spec: unchanged criteria reuse specs (no compile, empty diff); edited / new criteria compiled together; revert reuses the older version's spec; `compile_specs: false`; compilation happens before any write; `ensure_seed`.
- API: 422 envelope and nothing written; version validation and 404; weights / export ids / warnings in responses; `activate=false` keeps the active version, then activate, then default activation; export round trip (import and re-import as a version: identical criteria and specs, no compile, empty diff); import endpoint (format check, validation, hand-written file); download header and 404s.
- test-criterion: draft criterion judged with compile, no rows persisted; returned spec saved with the version; given spec / `fake` / `compile_spec: false`; PII masked for cloud only; ensemble votes; validation errors; 404 / 400 / 502; budget 429 with `local` exempt.
- Partial re-evaluation via `evaluate_conversation` with a stubbed `get_provider`: full v1; **(a)** +C9 → only C9 judged, others copied with the flag, total 90.91, weight_applied sums to 100, section S3 recomputed; **(b)** C6 → C6b → C6 absent, C6b judged, C9 copied, total 77.27; **(c)** weight-only change → zero calls, total 95.24.
- Strict `xfail(raises=AssertionError)` documenting runner findings (integration requests 1–4). Set-up problems inside them fail loudly via `pytest.fail`; only the final expectation is the expected failure.

Full suite at hand-off: `504 passed, 6 xfailed` (4 of the xfails are this package's; other packages were still adding tests).

## 7. Limitations

- test-criterion spend is not stored as an `Evaluation`, so it does not count towards the 24-hour budget (it is visible in the audit log via `cost_usd`). The compile call's cost is not included in `cost_usd`.
- *Superseded by the fix pass (`docs/arch/fixes.md`):* trial and compilation spend is booked in the spend ledger and counts towards the 24-hour budget (failed trials too); a trial reserves its estimate (judge + compile) until it ends and at most `MAX_CONCURRENT_TRIALS` run at once; compilation is budgeted ($0.03 per criterion, at most 4 at once) and falls back to the deterministic spec when the budget is spent; checklists are limited to 100 criteria and 4000-character instructions.
- The budget check is a local query with the contract's definition (sum of `Evaluation.cost_usd`, last 24 h). It does not use a shared platform helper.
- `channel_overrides` accept only `instruction`, `na_allowed` and `na_condition`, per the contract. A future seed field synced into v1 would make a re-save of v1 fail until it is added to `OVERRIDE_FIELDS`.
- The Studio's "gross error" toggle should also set the weight to 0; otherwise saving returns 422 (by design).
- Request bodies that are not JSON, or typed flags of the wrong type (`compile_specs: "maybe"`), still get FastAPI's default 422 `{"detail": …}` (integration request 6).

---

## 8. Integration requests

1. **`cqa/jobs/runner.py` turns an empty `only_codes` into "judge everything" (bug).** `_run` does `codes = set(only_codes) if only_codes else None`. A weight-only change has `rejudge_codes == []`, so `evaluate_conversation(…, only_codes=None)` re-judges every criterion. This contradicts docs/ARCHITECTURE.md §5.3 ("weight-only changes need no LLM calls") and the dry-run estimate of $0. Fix: `codes = set(only_codes) if only_codes is not None else None`. Test: `test_job_runner_weight_only_change_needs_no_llm_calls` (strict xfail; it currently re-judges 8 criteria). `evaluate_conversation` itself is correct with `only_codes=set()` (`test_partial_after_weight_only_change_needs_no_llm_calls`).
2. **Partial re-evaluation copies criteria that failed in the base evaluation (bug, `cqa/judge/runner.py`).** `base_rows` includes rows flagged `"error"` (answer `None`), so they are copied as failures forever and drop out of the total. results.json then marks the dialog `status: "error"`. Fix in `evaluate_conversation`: `base_rows = {r.code: r for r in base.criteria if "error" not in (r.flags or []) and not (r.applicable and r.answer is None)}`; codes missing from `base_rows` are already judged. Test: `test_partial_rejudges_criteria_that_failed_in_the_base_evaluation`.
3. **Duplicate `copied_from_previous_version` flag on chained partials (minor bug, `cqa/judge/runner.py`).** `flags=list(b.flags or []) + ["copied_from_previous_version"]` appends again on every chained partial (v1 → v2 → v3 gives the flag twice). Fix: `flags=[f for f in (b.flags or []) if f != "copied_from_previous_version"] + ["copied_from_previous_version"]`. Test: `test_chained_partials_keep_a_single_copied_flag`.
4. **Reviewer decisions are dropped on copied criteria (design gap, `cqa/judge/runner.py`).** Copied rows get `review_state="none"` and no `final_answer`, and `final_total_score` is the AI total. A reviewer's «Нет» on C7 in v1 is lost in the v2 partial evaluation (95.00 → 90.91 instead of 86.36). The e2e test observes the same thing (`bad_c1_carried_review`). Proposal: copy `review_state`, `final_answer`, `final_score` into copied rows, then call `cqa.review.service.recompute_final(db, ev)` before commit. Test: `test_partial_keeps_reviewer_decisions_on_copied_criteria`. The owner decides; delete the test if fresh AI-only partials are intended.
5. **`cqa/export/results_json.py`: use the version's export ids.** `from_db` calls `build_record(...)` without `export_ids`, so `scoring_rules.export_ids` (§1.3) is ignored. Suggested: `ids = cqa.checklist.validation.export_ids_for([c.code for c in version.criteria], version.scoring_rules)`, keep the entries whose id is in `REQUIRED_IDS`, and pass `export_ids=` (and `criteria_defs=`) to `build_record`.
6. **`cqa/main.py`: envelope for `RequestValidationError`.** Malformed JSON or wrong primitive types still answer `{"detail": [...]}`. Suggested handler: 422 `{"error": {"code": "invalid_request", "message": "Некорректный запрос: проверьте формат JSON и типы полей.", "errors": [{"field": ".".join(map(str, e["loc"][1:])), "message": e["msg"]} for e in exc.errors()]}}`.
7. **Default checklist with several checklists.** `svc.active_version()` (used by `/api/checklists/active` and as the evaluation default) picks the first active version by row order once imports exist. Suggest a setting `default_checklist_id`, or ordering by checklist id explicitly; the organizer checklist stays the default.
8. **`cqa/judge/runner.py`: extract `conversation_pack(db, conv, provider_name) -> ConversationPack`** (masking and structural facts) and call it from `evaluate_conversation`. `run_criterion_test` would then reuse it instead of mirroring the construction. Until then, any change to the pack in `evaluate_conversation` must be mirrored in `service.run_criterion_test`.
9. **Platform budget**: optionally add `checklist.criterion.test` audit costs (`details.response.cost_usd`) to `spent_24h`, and expose a shared guard helper that `run_criterion_test` can call instead of its local query.
10. **`docs/API.md`**: merge §1 (new endpoints, `activate`, `weights`, `export_ids`, `warnings`, error codes).
11. **Web (owner: the user)**, for the Studio redesign:
    - read `weights.total` / `weights.normalized` for the weight sum and "normalise to 100";
    - show `error.errors[].field/message` next to the rows (index = position in the sent `criteria`) and `warnings` after save;
    - "Save as draft" = `activate: false`;
    - "Test on a dialog" = `POST /api/checklists/test-criterion`, then send the returned `judge_spec` with the criterion so it is saved as tested;
    - export / import via `…/export?download=true` and `POST /api/checklists/import`. The current client import strips `judge_spec`; sending the file as is keeps the reviewed specs.
