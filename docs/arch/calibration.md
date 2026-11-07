# Package "calibration": gold labels, AI-vs-human agreement, run-to-run stability

Audience: the ML teammate (gold set, the same numbers as the harness CLI), QA leads and methodologists (where the AI disagrees with people and why), and the jury (the organizers compare per criterion and value reproducible, reasoned scores).

No schema change (the `GoldLabel` model already existed), no LLM calls, no edits to shared files. `cqa/main.py` already lists the `calibration` router.

| File | Role |
|---|---|
| `api/cqa/calibration/service.py` | `upsert_labels`, `list_labels`, `labelers`, `default_labeler`, `import_gold`, `export_gold`, `agreement`, `stability`, value validation, organizer version resolution |
| `api/cqa/calibration/__init__.py` | re-exports the public service API |
| `api/cqa/routers/calibration.py` | the six endpoints under `/api/calibration` |
| `api/tests/test_calibration.py` | 25 tests, in-memory SQLite, numbers worked out by hand, plus a parity check against `cqa.eval.harness.score` |

## 1. Data model (`gold_labels`, model `GoldLabel`)

One row per (conversation, criterion code, labeler). The table has a unique constraint on those three columns. Columns: `value` (JSON), `note`, `split`, `created_at`, `updated_at`.

- **Labelers.** `team` is the reconciled gold set and the default everywhere. Individual labelers (`alina`, `kassym`, …) live side by side. They feed the human-human ceiling.
- **Values are stored in canonical form:** `"yes"`, `"no"`, `"N/A"` or a number (int when integral). Input also accepts `Да/Нет`, `Иә/Жоқ`, `true/false`, numeric strings (`"15"`, `"7,0"`) and `null`. `null` means N/A, as in the harness and in results.json. For binary criteria only, `1/0` are read as yes/no.
- **Validation** runs against a reference checklist version. The default is the organizer version 1, the same rule as the results.json export: the first version with `version_no = 1`. Binary criteria take yes/no. Scale criteria take one of their listed steps (C4: 20/15/10/5/0, C6: 10/7/4/0). N/A is accepted only where the criterion or its channel override allows it, the same rule the judge uses. So C1 = N/A is accepted on a voice call and rejected on a chat. Codes are matched case-insensitively (`c4` becomes `C4`). Codes that are not in the version are rejected.
- **Pseudo codes** let the teammate's `eval/gold.json` round-trip without loss:
  - `total`: the human total from the file, a number from 0 to 100. Optional: agreement recomputes the human total from the labels and uses this value only when the labels are incomplete.
  - `_note`: a free-text note for the whole dialog. The text is kept in `note` and `value` is `null`.
  - Every row has a `kind`: `criterion`, `total` or `note`.
- **The split is a property of the dialog for one labeler.** Setting `split` on one label applies it to all labels of that dialog and labeler; the response field `split_propagated` counts those rows. New labels inherit the dialog's split, and `split: null` clears it. Split names are lowercased.
- **Labels need the conversation to exist** in this database (FK). Import skips unknown dialogs and lists them; it does not reject the file.

## 2. Endpoints

Errors always use the envelope `{"error": {"code", "message", ...}}` with Russian messages. Validation failures are **422 `validation_error`** with `errors: [{"field", "message"}]`, the same code that `audit`, `admin` and `overview` use. The router builds these responses itself, so the envelope holds in any app, including the bare test apps.

### `GET /api/calibration/labels?conversation_id=&labeler=&criterion_code=&split=`

All filters are optional; `split=all` means no filter. Rows are ordered by labeler, then dialog, then criterion order (C1…C8, then `total`, then `_note`).

```json
{
  "items": [{"id": 12, "conversation_id": "t-ru-002", "criterion_code": "C4", "kind": "criterion", "value": 15, "labeler": "team",
             "note": "не назван срок зачисления", "split": "train",
             "created_at": "2026-11-07T13:41:36.122354+00:00", "updated_at": "2026-11-07T13:41:36.122354+00:00"}],
  "total": 1,
  "labelers": [{"labeler": "alina", "labels": 4, "dialogs": 2, "totals": 0, "updated_at": "2026-11-07T13:41:36.124653+00:00"},
               {"labeler": "team", "labels": 43, "dialogs": 7, "totals": 0, "updated_at": "2026-11-07T13:41:36.123922+00:00"}]
}
```

`labelers` counts criterion labels, labeled dialogs and `total` rows per labeler. The UI can fill a labeler picker from it.

### `PUT /api/calibration/labels` (bulk upsert, permission `calibration.write`)

```json
{
  "labeler": "team",
  "checklist_version_id": null,
  "dry_run": false,
  "items": [
    {"conversation_id": "p1-ru-029", "criterion_code": "C1", "value": "yes", "split": "train"},
    {"conversation_id": "p1-ru-029", "criterion_code": "C2", "value": "N/A"},
    {"conversation_id": "p1-ru-029", "criterion_code": "C4", "value": 15, "note": "не назван срок зачисления"},
    {"conversation_id": "p1-ru-029", "criterion_code": "total", "value": 62.31},
    {"conversation_id": "p1-ru-029", "criterion_code": "_note", "note": "сверено с PDF, стр. 3"},
    {"conversation_id": "p1-ru-007", "criterion_code": "C7", "labeler": "alina", "remove": true}
  ]
}
```

- `value` is required except for `remove: true` and for `_note` rows. `note` and `split` change only when the item contains them. An item's `labeler` overrides the body's `labeler`; the default is `team`.
- The batch is **atomic**: one invalid item rejects all of them with 422 and nothing is written. `dry_run: true` validates and counts, then rolls back.

Response:

```json
{"labelers": ["team"], "checklist_version_id": 1, "created": 9, "updated": 0, "unchanged": 0, "removed": 0, "split_propagated": 0,
 "dry_run": false, "items": [{"id": 1, "conversation_id": "e-ru-001", "criterion_code": "C1", "kind": "criterion", "value": "yes", "…": "…"}]}
```

Errors:
- 422, for example a chat labeled N/A for C1:
  `{"error": {"code": "validation_error", "message": "Метки не сохранены: ошибок — 1, подробности в errors.", "errors": [{"field": "items[0].value", "message": "N/A недопустимо для C1 в канале «agent chat»: критерий применим всегда"}]}}`.
  Error fields follow the shape of `items[3].conversation_id`, `items[4].criterion_code` or `items[1].value`. A duplicate inside one batch is reported as `items[9]`. A non-object body is reported as `body`, and a bad `checklist_version_id` under its own name.
- 404 `version_not_found`: `{"error": {"code": "version_not_found", "message": "Версия чек-листа 999 не найдена", "checklist_version_id": 999}}`.
- 409 `conflict`: a concurrent write hit the unique constraint. Retry the request.

### `POST /api/calibration/import` (permission `calibration.write`)

The body is `{labeler, doc, replace?, dry_run?, checklist_version_id?}`, where `doc` is the teammate's file. Alternatively the file itself is the body, with `?labeler=&replace=&dry_run=&checklist_version_id=` in the query (handy for `curl -d @eval/gold.json`). `doc` may also be JSON text.

```json
{"labeler": "team", "replace": false, "dry_run": false,
 "doc": {"split": {"train": ["p1-ru-029"], "validation": ["p1-ru-007"]},
         "labels": {"p1-ru-029": {"C1": "yes", "C2": "N/A", "C3": "N/A", "C4": 20, "C5": "no", "C6": 7, "C7": "no", "C8": "yes",
                                  "total": 62.31, "_note": "…"}}}}
```

- The default merges: labels that are not in the file stay. `replace: true` makes the file the labeler's whole set and deletes the labeler's other rows. It is refused (422 on `doc.labels`) when no label in the file matches a dialog of this database, so a file meant for another instance cannot wipe the set.
- Per dialog, `_note` becomes the dialog note, `_notes: {"C4": "…"}` becomes per-label notes (written by `export?include_notes=true`), and any other `_…` key is ignored and listed. Top-level keys other than `split` and `labels`, such as `_instructions` or `_meta`, are ignored.
- A dialog listed in several splits goes to `validation`, so the model is never trained on a validation dialog, and a warning is returned.
- Like PUT, the import is atomic: error paths look like `doc.labels.p1-ru-029.C4`, `doc.split.train` or `doc.labels.<cid>`.

Response:

```json
{"labeler": "team", "checklist_version_id": 1, "dry_run": false, "replace": false,
 "dialogs": 1, "labels": 8, "totals": 1, "notes": 1,
 "created": 10, "updated": 0, "unchanged": 0, "removed": 0, "split_propagated": 0,
 "split": {"train": 1}, "skipped_conversations": ["p1-ru-999"], "split_unlabeled": ["p1-ru-001", "…"],
 "ignored_keys": ["_comment"], "warnings": []}
```

`dialogs`, `labels`, `totals` and `notes` count what the file contained for dialogs known to this database. `created`, `updated`, `unchanged` and `removed` count rows. `split_unlabeled` lists dialogs in `split` that have no labels in the file; their membership is not stored.

### `GET /api/calibration/export?labeler=team&include_notes=false&download=false`

Returns one labeler's labels in the gold.json format. `python -m cqa.eval.harness --gold <file>` reads it as is, and so does the import.

```json
{"_meta": {"format": "cqa gold v1 (python -m cqa.eval.harness --gold <file>)", "labeler": "team", "exported_at": "2026-11-07T13:41:36+00:00",
           "dialogs": 7, "labels": 43, "notes_included": false},
 "split": {"train": ["t-kz-001", "t-ru-001", "t-ru-002", "t-ru-noeval"], "validation": ["t-en-001", "t-ru-call"]},
 "labels": {"t-en-001": {"C1": "yes", "C2": "no", "C3": "N/A", "C4": "N/A", "C5": "no", "C6": 10, "C7": "yes", "C8": "no"},
            "t-en-err": {"C1": "yes"}}}
```

- Codes are ordered C1…C8, then other codes, then `total`. `train` and `validation` are always present.
- `include_notes=true` adds `_note` and `_notes`. Leave it off for the harness, which would treat those keys as criteria.
- `download=true` adds `Content-Disposition: attachment; filename="gold_team.json"; filename*=UTF-8''…`, which is safe for Cyrillic labeler names.
- An unknown labeler returns an empty `labels` object.

### `GET /api/calibration/agreement?labeler=&split=&checklist_version_id=&source=ai&provider=`

Compares the AI with the human gold, per criterion.

| Param | Default | Meaning |
|---|---|---|
| `labeler` | `team` if it has labels, else the labeler with the most labels | the human reference |
| `split` | all | `train`, `validation` or `all` |
| `checklist_version_id` | organizer version 1 | the evaluations compared and the criteria definitions |
| `source` | `ai` | `ai`: the model's answers and `total_score`. `final`: answers after appeal decisions and `final_total_score` |
| `provider` | any | only evaluations by this provider (`anthropic`, `ensemble`, `local`, …) |

Response from the test fixture; the lists are shortened (`criteria` keeps one row per criterion of the version):

```json
{"checklist_version_id": 1, "labeler": "team", "labelers": ["alina", "kassym", "team"], "split": null, "source": "ai", "provider": null,
 "criteria": [
   {"code": "C2", "title": "Выявление потребности", "answer_type": "binary", "weight": 15.0, "n": 5, "exact": 0.6, "na_agree": 0.6,
    "kappa": 0.444, "qwk": null, "mae": null, "na_human": 3, "na_ai": 1,
    "confusion": {"labels": ["yes", "no", "N/A"], "rows": "human", "columns": "ai", "matrix": [[1, 0, 0], [0, 1, 0], [1, 1, 1]]}},
   {"code": "C4", "title": "Корректная и полная консультация", "answer_type": "scale", "weight": 20.0, "n": 5, "exact": 0.6, "na_agree": 1.0,
    "kappa": null, "qwk": 0.875, "mae": 2.5, "na_human": 1, "na_ai": 1,
    "confusion": {"labels": ["20", "15", "10", "5", "0", "N/A"], "rows": "human", "columns": "ai",
                  "matrix": [[1,0,0,0,0,0],[1,0,0,0,0,0],[0,0,1,0,0,0],[0,0,0,0,1,0],[0,0,0,0,0,0],[0,0,0,0,0,1]]}}],
 "overall": {"n_dialogs": 5, "n_pairs": 40, "exact_mean": 0.85, "exact_micro": 0.85, "kappa_mean": 0.832, "qwk_mean": 0.857,
             "total_mae": 2.08, "total_bias": -2.08, "n_totals": 5},
 "totals": [{"conversation_id": "t-ru-002", "ai": 85.5, "human": 90.5, "delta": -5.0, "source": "computed", "evaluation_id": 7}],
 "by_language": [{"language": "EN", "n_dialogs": 1, "n_pairs": 8, "exact": 1.0, "exact_mean": 1.0, "total_mae": 0.0,
                  "criteria": [{"code": "C1", "n": 1, "exact": 1.0}, "…"]}],
 "by_channel": [{"channel": "agent chat", "n_dialogs": 4, "n_pairs": 32, "exact": 0.875, "exact_mean": 0.875, "total_mae": 2.41, "criteria": ["…"]}],
 "disagreements": [{"conversation_id": "t-ru-002", "code": "C4", "title": "Корректная и полная консультация", "ai": 20, "human": 15,
                    "explanation": "…the model's rationale…", "na_reason": null, "confidence": 0.9, "evaluation_id": 7, "evaluation_criterion_id": 52,
                    "review_state": "changed", "final_answer": 15, "note": "не назван срок зачисления", "split": "train",
                    "language": "RU", "channel": "agent chat", "operator_name": "Дана"}],
 "coverage": {"labeled_dialogs": 7, "missing_evaluation": ["t-ru-noeval"],
              "skipped": [{"conversation_id": "t-en-err", "code": "C1", "reason": "ai_error"}], "unknown_codes": ["C9"]},
 "human_agreement": {"labelers": ["alina", "kassym"], "excluded": ["team"], "pairs": [{"labelers": ["alina", "kassym"], "n": 4, "exact": 0.5}],
                     "criteria": [{"code": "C1", "title": "Установление контакта", "n": 2, "exact": 0.5, "na_agree": 1.0, "kappa": 0.0, "qwk": null, "mae": null}],
                     "exact_mean": 0.5},
 "warnings": []}
```

Errors: 404 `version_not_found`, and 422 `validation_error` on `source` when it is not `ai` or `final`. When there are no labels or no evaluations, the response is not an error: counts are 0, rates are `null`, and every criterion row is present with `n: 0`.

### `GET /api/calibration/stability?checklist_version_id=&provider=`

Measures run-to-run stability over dialogs with at least two completed evaluations on the version (default: organizer version 1). `provider` restricts the runs, for example to compare Opus only with Opus.

```json
{"checklist_version_id": 1, "provider": null, "n_conversations": 4, "n_evaluations": 9, "single_run_conversations": 2,
 "runs": [{"provider": "anthropic", "model": "claude-opus-5-5", "mode": "single", "n": 7}, {"provider": "anthropic", "model": "claude-opus-5-5", "mode": "partial", "n": 1},
          {"provider": "local", "model": "qwen3:8b", "mode": "single", "n": 1}],
 "criteria": [
   {"code": "C1", "title": "Установление контакта", "answer_type": "binary", "n": 3, "agree": 2, "agreement": 0.667, "kappa": 0.0, "qwk": null, "mae": null,
    "majority_share": 0.833, "unanimous": 0.667, "flips": 1},
   {"code": "C4", "title": "Корректная и полная консультация", "answer_type": "scale", "n": 4, "agree": 1, "agreement": 0.25, "kappa": null, "qwk": 0.727, "mae": 5.0,
    "majority_share": 0.667, "unanimous": 0.25, "flips": 3}],
 "overall": {"agreement_mean": 0.865, "majority_share_mean": 0.924, "n_total_pairs": 2, "mean_abs_delta_total": 5.0, "max_abs_delta_total": 5.0,
             "max_delta_conversation_id": "t-ru-002", "flipped_conversations": 3, "n_flips": 5},
 "total_deltas": [{"conversation_id": "t-ru-002", "evaluation_ids": [1, 7], "totals": [90.5, 85.5], "delta": 5.0}],
 "flips": [{"conversation_id": "t-ru-call", "code": "C4", "title": "Корректная и полная консультация", "values": [0, 5, 0], "evaluation_ids": [4, 5, 10],
            "latest_pair_differs": true, "language": "RU", "channel": "voice call"}]}
```

Error: 404 `version_not_found`. This is the measurement in `docs/ML_BRIEF.md` ("C1 12/12, C4 8/12, mean |Δtotal| 4.55"), now available on demand from the product.

## 3. Definitions

- **Compared evaluation:** the latest (greatest id) evaluation with `status = completed` per conversation on the version, after the `provider` filter. Failed and running evaluations are ignored.
- **AI value of a criterion:** `"N/A"` when the row is not applicable, else `answer`. With `source=final`, the value is `final_answer` when a reviewer decided. Rows flagged `error` (the judge failed) or missing are **skipped** and listed in `coverage.skipped`. They are not counted as N/A.
- **Per criterion (the formulas of `cqa.eval.harness.score`, imported, not copied):**
  - `exact`: the share of pairs where `_norm(human) == _norm(ai)`.
  - `na_agree`: the share of pairs where both sides agree on whether the answer is N/A.
  - `kappa` (binary): Cohen's kappa with N/A as a class (`cohen_kappa`). It returns 1.0 when both sides use one class only.
  - `qwk` (scale): quadratic-weighted kappa over pairs where neither side is N/A, with levels from the criterion options (`quadratic_weighted_kappa`).
  - `mae` (scale): the mean absolute difference over the same pairs.
  - `confusion`: rows are human, columns are AI. Labels are the option values (scale descending) plus N/A when the criterion allows it in any channel or N/A was observed.
  - Rounding is the harness's: 3 decimals, MAE 2.
- **Harness parity:** a test runs `harness.load_predictions` and `harness.score` on the exported file and asserts identical `n`, `exact`, `na_agree`, `kappa`, `qwk` and `mae` for every criterion. The only intended difference is the treatment of failed AI rows (the CLI counts them as N/A).
- **Totals:** the human total is recomputed from the labels with the scoring engine (N/A weight redistribution, half-up rounding) whenever every weighted criterion is labeled. Otherwise the file's `total` is used, with `totals[].source = "label"`. When both exist and differ by more than 0.01, a warning is returned, which catches arithmetic slips in the gold file.
  - The AI total is `total_score`, or `final_total_score` with `source=final`. Evaluations with a failed criterion are left out of the totals.
  - `total_mae` is the mean |AI − human|. `total_bias` is the mean of AI − human; a negative value means the model is stricter.
- **Overall:**
  - `n_dialogs`: dialogs with at least one compared pair.
  - `exact_mean`: the mean of the per-criterion `exact` over criteria with pairs.
  - `exact_micro`: all pairs pooled.
  - `kappa_mean`, `qwk_mean`: the means over the criteria that have them.
- **Groups:** `by_language` and `by_channel` give, per group, pooled `exact`, `exact_mean` over criteria, `total_mae` and a per-criterion `{code, n, exact}`.
- **Human ceiling (`human_agreement`):** pooled agreement over every pair of labelers except `team`, which is the reconciled set and would inflate the ceiling. Same metrics, same split filter; `null` with fewer than two such labelers.
- **Stability:**
  - Per criterion and dialog, only the runs that actually judged the criterion count. Rows copied by a partial re-evaluation (flag `copied_from_previous_version`) and failed rows are skipped, so copied values cannot inflate stability.
  - `agreement`: the share of dialogs whose two latest judgments are equal. `kappa` and `qwk` are computed over those pairs.
  - `majority_share`: the mean over dialogs of (count of the most frequent value) / (number of runs).
  - `unanimous`: the share of dialogs where every run gave the same value.
  - `flips`: every dialog whose runs disagree, with the values in order.
  - `mean_abs_delta_total`: computed over the two latest **full** runs (not `partial`) without failed criteria.

## 4. Permissions and audit

| Endpoint | Permission | Audit action (security middleware) |
|---|---|---|
| `GET` labels, export, agreement, stability | `read` | none |
| `PUT /api/calibration/labels` | `calibration.write` (demo, reviewer, lead, methodologist, admin) | `calibration.labels.upsert`, captures `created`/`updated` |
| `POST /api/calibration/import` | `calibration.write` | `calibration.labels.import`, captures `created`/`updated` |

These rules already exist in `cqa/security/roles.py`. The router does not check roles itself, because the middleware is the single enforcement point, and it adds no semantic audit events. I verified this live on port 8017 against a scratch SQLite database (not `api/data/cqa.db`; no API key):
- `visitor` and `operator` get 403 `forbidden` with `permission: calibration.write`.
- `reviewer`, `lead` and requests without headers (`demo`) succeed.
- `GET /api/audit` shows the events with the actor (`reviewer`, `Дана`, request id `cal-live-1`) and the captured counts. Denied attempts are recorded as well.

The default labeler is `team`; it is never taken from `X-CQA-User`. The audit trail records who changed the team set.

## 5. How the ML teammate uses it

```bash
# load the reconciled gold set (and each person's file under their own name for the human ceiling)
curl -X POST 'http://localhost:8000/api/calibration/import?labeler=team' -H 'Content-Type: application/json' -d @api/eval/gold.json
curl -X POST 'http://localhost:8000/api/calibration/import?labeler=alina' -H 'Content-Type: application/json' -d @api/eval/gold_alina.json
# per-criterion agreement on the validation split, then the same file back for the CLI
curl 'http://localhost:8000/api/calibration/agreement?split=validation'
curl 'http://localhost:8000/api/calibration/export?labeler=team' -o api/eval/gold.json
cd api && uv run python -m cqa.eval.harness --gold eval/gold.json --split validation
```

Labels added in the UI through PUT flow back into `gold.json` through export. Use `dry_run=true` first on a shared instance.

## 6. Tests (`api/tests/test_calibration.py`, 25 tests, about 0.6 s)

The tests use in-memory SQLite with `StaticPool` and FastAPI `dependency_overrides[get_db]`. There are no network or Claude calls, and `api/data/cqa.db` is never touched.

The module fixture `world` contains:
- the organizer v1 and an active v2 with an extra criterion C9;
- seven dialogs (RU, KZ, EN chats, a RU call, a labeled dialog that was never evaluated, and a dialog whose C1 judgment failed);
- old, partial (copied rows), local-provider, failed and v2 evaluations;
- a reviewer decision;
- team, alina and kassym labels.

| Test | Covers |
|---|---|
| `test_fixture_totals`, `test_canonical_values` | fixture totals via the scoring engine; value parsing (Да/Нет/Иә/Жоқ, booleans, N/A spellings, numeric strings, rejects) |
| `test_upsert_create_update_unchanged_remove`, `test_split_is_per_dialog`, `test_upsert_validation_is_atomic` | create/update/unchanged/remove/dry run, pseudo codes, the voice-call N/A override, split propagation and inheritance, conflicting splits, 12 kinds of invalid items with exact error fields, nothing written on error |
| `test_list_labels_and_labelers`, `test_export_world` | filters, ordering, labeler summary, default labeler, export order and splits, notes |
| `test_import_export_round_trip`, `test_import_teammate_template`, `test_import_replace_and_dry_run`, `test_import_errors_and_split_conflicts` | the teammate's format, the real `api/eval/gold.template.json`, skipped dialogs, ignored keys, `_note`/`_notes`, export → import → export identity, idempotent re-import, replace (and its guard), dry run, malformed documents, JSON text, split conflicts |
| `test_agreement_per_criterion`, `test_agreement_overall_groups_and_disagreements`, `test_agreement_human_ceiling` | exact, kappa, QWK, MAE, N/A agreement and confusion matrices checked by hand (C1 κ 0.545, C2 κ 0.444, C4 QWK 0.875, C6 QWK 26/31 = 0.839); exact_mean 0.85, total MAE 2.08 and bias −2.08 with human totals recomputed (54.62 = 3550/65, 71.76 = 61/85); language and channel groups; disagreements with explanation, review state and note; coverage (missing evaluation, failed AI row, unknown code); human ceiling |
| `test_agreement_split`, `test_agreement_final_answers`, `test_agreement_other_version_labeler_and_provider` | validation split (single-class κ = 1.0, one-pair QWK = null), reviewer-corrected answers (QWK 0.929, MAE 3.08), version 2 with C9, another labeler, provider filter, explicit unknown labeler, 404/422 |
| `test_agreement_matches_harness_cli` | identical per-criterion numbers to `cqa.eval.harness.score` |
| `test_totals_fall_back_to_the_file_total`, `test_empty_database` | `total` fallback, computed total over the file total with a warning, default labeler without `team`, empty database |
| `test_stability`, `test_stability_filters` | two-latest agreement, κ, majority share, unanimous, flips (copied and failed rows excluded), mean and max \|Δtotal\| over clean full runs, provider and version filters, 404 |
| `test_router_routes`, `test_http_write_endpoints`, `test_http_read_endpoints` | the six routes; HTTP status codes and the envelope (422, 404), both import forms, dry run over HTTP, Cyrillic download filename |

Full suite at the time of writing: `506 passed, 6 xfailed` (one run while other packages were writing their tests showed a transient failure that did not reproduce in three later runs).

## 7. Limitations

- Labels exist only for dialogs present in the database (FK). Import the dataset first, then the gold file, on each instance (local, hosted).
- Split membership of dialogs without labels is not stored (it is reported in `split_unlabeled`).
- `agreement` uses the latest completed evaluation per dialog. Earlier runs are visible only through `stability`, and `stability` mixes providers unless `provider` is given (the `runs` breakdown shows the mix).
- The human ceiling leaves out the labeler named `team` by convention. A reconciled set stored under another name would be counted as an independent labeler.
- The MAE rounding follows the harness (Python `round`), not the half-up rounding of scores. The values differ by at most 0.01 on exact halves.
- There is no `DELETE` endpoint: a label is deleted with `PUT … {"remove": true}`. The route map has DELETE rules for `/api/calibration/labels`, and they are unused.
- The middleware records dry runs as writes (`calibration.labels.upsert` / `.import`), although nothing is saved.
- No pagination on `GET /labels`. The expected volume is a few hundred to a few thousand rows.

## 8. Integration requests (shared files and other packages)

1. **`docs/API.md`**: add a "Calibration" section with the six endpoints, their parameters, errors and the examples from §2. All of it is additive.
2. **Roles package, `cqa/security/roles.py`:**
   - Widen the audit captures: for upsert, `("created", "updated", "unchanged", "removed", "split_propagated", "labelers", "dry_run")`; for import, `("labeler", "dialogs", "labels", "created", "updated", "unchanged", "removed", "skipped_conversations", "replace", "dry_run")`.
   - Add a `refine` hook for dry runs (`body.dry_run is True`, or the query `dry_run=true` on import) that maps them to `calibration.labels.preview` or skips the audit, like `_dry_run` does for evaluations.
   - The rules for `POST /api/calibration/labels` and `DELETE /api/calibration/labels[/{label_id}]` have no endpoint. They are harmless and can stay for later.
3. **`web/`** (the user's frontend, not touched here): the `#/calibration` nav entry already exists in `GET /api/roles`. Suggested page:
   - a labeling grid per dialog (`GET`/`PUT` labels, values Да/Нет/N/A and scale steps, with the N/A offer per channel matching the checklist);
   - import and export buttons (`POST /import` with `dry_run` preview, `GET /export?download=true`);
   - an agreement table with a confusion matrix per criterion and the human ceiling next to it;
   - a disagreement list linking to the conversation card through `conversation_id` and `evaluation_criterion_id`;
   - a stability panel with the flips.
4. **Overview package (optional):** reuse `cqa.calibration.agreement(db)["overall"]` (`exact_mean`, `total_mae`) as an "AI vs gold" KPI next to the reviewer-agreement block.
5. **`cqa/eval/harness.py`** (owned by the ML teammate, optional): `score()` treats `_note`-style keys as criteria and counts failed AI rows as N/A. Skipping keys that start with `_` and rows flagged `error` would make the CLI match the API exactly.
6. **`docs/ARCHITECTURE.md` §7.6 and `docs/HANDOFF_ML.md` (optional):** mention that the calibration loop has an API (import gold → per-criterion agreement → stability → export for the harness), with the commands from §5.
