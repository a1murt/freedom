# Package "overview": executive overview and operator scorecards

Audience: directors and QA leads (one-page executive view) and the operator portal (personal scorecard with coaching points). Read-only analytics over the existing tables: no schema change, no writes, no LLM calls.

| File | Role |
|---|---|
| `api/cqa/analytics/overview.py` | `overview(db, checklist_version_id=None, days=30)`; shared "final view" helpers (post-review answers, lost points) |
| `api/cqa/analytics/coaching.py` | `operator_scorecard(db, name, checklist_version_id=None)`, `resolve_operator`, `OperatorNotFound` |
| `api/cqa/routers/insights.py` | adds `GET /api/overview` and `GET /api/operators/{name}`; `GET /api/violations` and `GET /api/operators` are unchanged |
| `api/tests/test_overview.py` | 33 tests, in-memory SQLite, real review service for appeals and decisions |

## Endpoints

### `GET /api/overview?checklist_version_id=&days=30`

| Param | Default | Meaning |
|---|---|---|
| `checklist_version_id` | none | Restrict to one checklist version. Omitted: every version, the latest completed evaluation per conversation (same scope as `GET /api/analytics/summary`). |
| `days` | 30 | Trend window, 1..365. Only `trend`, `period` and `cost.period_usd` depend on it. |

Errors (envelope `{"error": {...}}`):
- 422 `validation_error`, e.g. `days=0`, `days=abc`, `checklist_version_id=abc`:
  `{"error": {"code": "validation_error", "message": "Некорректные параметры запроса (days: период должен быть от 1 до 365 дней)", "errors": [{"field": "days", "message": "период должен быть от 1 до 365 дней"}]}}`
- 404 `checklist_version_not_found`: `{"error": {"code": "checklist_version_not_found", "message": "Версия чек-листа 999 не найдена"}}`
- No evaluations (empty DB, new version) is not an error: counts are 0, rates/means are `null`, lists are empty, `trend` still has one item per day.

Response (test fixture, `days=7`, lists shortened):

```json
{
  "generated_at": "2026-11-07T12:00:00+00:00", "checklist_version_id": 1, "days": 7,
  "coverage": {"conversations": 6, "evaluated": 4, "not_evaluated": 2, "failed": 1, "pct": 66.67},
  "quality": {"n": 4, "mean": 75.84, "median": 83.44, "p25": 70.53, "p75": 88.75, "share_ge_85": 0.5, "share_lt_60": 0.25,
              "good_threshold": 85.0, "low_threshold": 60.0},
  "critical": {"count": 1, "rate": 0.25, "reviewed": 0, "by_type": [
      {"code": "insult", "ru": "Оскорбление клиента", "en": "Insulting the client", "kk": "Клиентті қорлау", "count": 1},
      {"code": "paid_bypass", "ru": "…", "en": "…", "kk": "…", "count": 0},
      {"code": "disclosure", "ru": "…", "en": "…", "kk": "…", "count": 0},
      {"code": "other", "ru": "…", "en": "…", "kk": "…", "count": 0}]},
  "appeals": {"total": 5, "open": 1, "decided": 4, "changed": 2, "confirmed": 2, "overturn_rate": 0.5,
              "mean_hours_to_decision": 4.5, "oldest_open_hours": 26.0},
  "agreement": [{"code": "C1", "title": "Установление контакта", "n_reviewed": 2, "agreement_rate": 0.5, "kappa": 0.3333}, "…one per criterion"],
  "by_language": [{"language": "EN", "n": 1, "mean": 85.0, "critical": 0}, {"language": "KZ", "n": 1, "mean": 81.88, "critical": 0},
                  {"language": "RU", "n": 2, "mean": 68.23, "critical": 1}],
  "by_channel": [{"channel": "agent chat", "n": 3, "mean": 72.78, "critical": 1}, {"channel": "voice call", "n": 1, "mean": 85.0, "critical": 0}],
  "criteria": [
    {"code": "C1", "title": "Установление контакта", "section": "S1", "weight": 10.0, "answer_type": "binary", "is_critical": false,
     "n": 4, "n_applicable": 3, "pass_rate": 1.0, "mean_ratio": null, "na_rate": 0.25,
     "lost_points": 0.0, "lost_points_share": 0.0, "dialogs_losing_points": 0},
    {"code": "C4", "title": "Корректная и полная консультация", "section": "S2", "weight": 20.0, "answer_type": "scale", "is_critical": false,
     "n": 4, "n_applicable": 3, "pass_rate": null, "mean_ratio": 0.75, "na_rate": 0.25,
     "lost_points": 18.01, "lost_points_share": 0.1864, "dialogs_losing_points": 2}, "…"],
  "top_failing": [
    {"code": "C6", "title": "Управление диалогом, скорость и грамотность", "answer_type": "scale", "weight": 15.0,
     "lost_points": 31.21, "lost_points_share": 0.3229, "dialogs_losing_points": 3, "pass_rate": null, "mean_ratio": 0.7,
     "example": {"conversation_id": "o-en-004", "evaluation_id": 5, "evaluation_criterion_id": 38, "operator_name": null,
                 "channel": "voice call", "language": "EN", "final_total_score": 85.0, "ai_answer": "7", "final_answer": "7",
                 "lost_points": 15.0, "explanation": "…", "review_state": "none", "reviewer_reason": null,
                 "evidence": [{"turn_idx": 1, "speaker": "operator", "quote": "…", "role": "supports", "timestamp": "00:10", "offset_seconds": 10.0}]}},
    "…3 items at most"],
  "trend": [{"date": "2026-11-05", "evaluated": 2, "mean_total": 83.44, "critical": 0},
            {"date": "2026-11-06", "evaluated": 1, "mean_total": 36.47, "critical": 1},
            {"date": "2026-11-07", "evaluated": 1, "mean_total": 100.0, "critical": 0}],
  "period": {"days": 7, "from": "2026-11-01", "to": "2026-11-07", "evaluated": 4, "mean_total": 75.84, "critical": 1},
  "cost": {"total_usd": 0.6, "per_dialog_usd": 0.15, "evaluations": 6, "tokens_in": 36600, "tokens_out": 8700,
           "cache_read_tokens": 4000, "period_usd": 0.57, "by_model": [
      {"provider": "anthropic", "model": "claude-opus-5-5", "requests": 14, "tokens_in": 22300, "tokens_out": 4900, "cost_usd": 0.45, "share": 0.75},
      {"provider": "anthropic", "model": "claude-sonnet-5-5", "requests": 10, "tokens_in": 14300, "tokens_out": 3800, "cost_usd": 0.15, "share": 0.25}]},
  "first_response": {"mean_seconds": 172.0, "over_threshold_share": 0.4, "threshold_seconds": 180.0, "n": 5},
  "operators": {"count": 3, "evaluated": 2, "at_risk_threshold": 60.0,
                "at_risk": [{"operator_name": "Алексей", "n": 1, "mean_total": 36.47, "critical_count": 1, "reasons": ["critical", "low_mean"]}]}
}
```

(`trend` is shortened above; it always has exactly `days` items, empty days as `{"evaluated": 0, "mean_total": null, "critical": 0}`.)

On the local demo database (65 dialogs) the call takes about 90 ms and reproduces `/api/analytics/summary` (mean 80.6, median 95.0, 8 gross violations, N/A rates, first response) and `/api/violations` (8 = 1 insult + 4 paid bypass + 3 disclosure); total cost $17.72 matches the sum of the LLM traces.

### `GET /api/operators/{name}?checklist_version_id=`

`name` is the operator name as stored on conversations, URL-encoded in the path (`/api/operators/%D0%90%D0%BB%D0%B8%D0%BD%D0%B0`). The server decodes it once; a double-encoded name (for example the raw `X-CQA-User` header value encoded again) and `+` for spaces are also accepted, and matching falls back to case- and space-insensitive. `—` (`%E2%80%94`) addresses the conversations without a detected operator name, like the `"—"` row of `GET /api/operators`. Names containing `/` are not supported.

Errors: 404 `operator_not_found` (`{"error": {"code": "operator_not_found", "message": "Оператор «Nobody» не найден"}}`), 404 `checklist_version_not_found`, 422 `validation_error` (`checklist_version_id=abc`).

Response (test fixture, operator Алина, lists shortened):

```json
{
  "operator_name": "Алина", "checklist_version_id": 1, "generated_at": "2026-11-07T12:00:00+00:00",
  "conversations_total": 2, "evaluated": 2, "mean_total": 90.94, "median_total": 90.94, "critical_count": 0,
  "team_mean_total": 75.84, "languages": ["KZ", "RU"], "channels": ["agent chat"],
  "conversations": [
    {"id": "o-ru-001", "channel": "agent chat", "language": "RU", "status": "evaluated", "evaluation_id": 2, "total_score": 100.0,
     "final_total_score": 100.0, "critical_error": false, "evaluated_at": "2026-11-07T10:00:00+00:00", "review_state": "reviewed"},
    {"id": "o-kz-003", "channel": "agent chat", "language": "KZ", "status": "evaluated", "evaluation_id": 4, "total_score": 88.13,
     "final_total_score": 81.88, "critical_error": false, "evaluated_at": "2026-11-05T15:00:00+00:00", "review_state": "under_review"}],
  "criteria": [{"code": "C7", "title": "Завершение", "section": "S3", "weight": 5.0, "answer_type": "binary", "is_critical": false,
                "n": 2, "n_applicable": 2, "pass_rate": 0.5, "mean_ratio": null, "na_rate": 0.0, "lost_points": 6.25,
                "lost_points_share": 0.3448, "dialogs_losing_points": 1, "team_pass_rate": 0.6667, "team_mean_ratio": null}, "…one per criterion"],
  "strengths": [{"code": "C2", "title": "Выявление потребности", "answer_type": "binary", "weight": 15.0, "pass_rate": 1.0, "mean_ratio": null,
                 "team_pass_rate": 1.0, "team_mean_ratio": null, "n_applicable": 2}, "…2 at most"],
  "coaching_points": [
    {"code": "C7", "title": "Завершение", "answer_type": "binary", "weight": 5.0, "pass_rate": 0.5, "mean_ratio": null,
     "team_pass_rate": 0.6667, "team_mean_ratio": null, "n_applicable": 2, "lost_points": 6.25, "dialogs_losing_points": 1,
     "expected": "Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался. …",
     "examples": [{"conversation_id": "o-kz-003", "evaluation_id": 4, "evaluation_criterion_id": 31, "operator_name": "Алина",
                   "channel": "agent chat", "language": "KZ", "final_total_score": 81.88, "ai_answer": "yes", "final_answer": "no",
                   "lost_points": 6.25, "explanation": "…", "review_state": "changed",
                   "reviewer_reason": "Оператор попрощался, не уточнив, остались ли вопросы",
                   "evidence": [{"turn_idx": 1, "speaker": "operator", "quote": "…", "role": "supports", "timestamp": "00:10", "offset_seconds": 10.0}]}]},
    "…2 at most, 1-3 examples each"],
  "violations": {"total": 0, "counts": {"insult": 0, "paid_bypass": 0, "disclosure": 0, "other": 0}, "types": ["…"], "items": []},
  "appeals": {"total": 4, "open": 1, "decided": 3, "changed": 1, "confirmed": 2, "overturn_rate": 0.3333,
              "mean_hours_to_decision": 5.67, "oldest_open_hours": 26.0,
              "items": [{"id": 5, "status": "open", "evaluation_criterion_id": 30, "comment": "…", "author_label": "QA analyst",
                         "author_role": "operator", "author_name": "Алина", "created_at": "2026-11-06T10:00:00", "decision": null,
                         "conversation_id": "o-kz-003", "evaluation_id": 4, "code": "C6", "title": "…", "ai_answer": 7,
                         "final_answer": 7, "review_state": "under_review", "current_evaluation": true}, "…"]}
}
```

`violations` is exactly `GET /api/violations?operator=<name>` (same items, counts and type labels). Appeal items are `GET /api/appeals` items (same `appeal_dict` fields) plus context; open appeals come first, then newest first. `conversations[].status` is `evaluated | failed | not_evaluated`, newest evaluation first; `review_state` is the same as in conversation lists (`none | under_review | reviewed`).

## Definitions (single source: module docstrings of `overview.py` and `coaching.py`)

- **Scope**: the latest completed evaluation per conversation (`service.latest_evaluations`), optionally one checklist version. Appeals: every appeal on an evaluation in scope (process metric, also on superseded evaluations).
- **Final values**: totals are `final_total_score`, the critical flag is `critical_error` (both recomputed after decisions). A criterion's final answer is the reviewer's answer when a decision exists, otherwise the AI answer; only `agreement` compares AI vs final.
- **Lost points** of a criterion in one evaluation = `weight_applied − earned` on the 0–100 scale, recomputed with `cqa.scoring.engine.compute_score` from the final answers. This matters when a reviewer changes a criterion to or from N/A: stored `earned` of the other rows is not updated by `review.recompute_final`, the recomputation is. Per evaluation the lost points add up to `100 − final total` (checked by a test on every fixture evaluation and on all 65 demo evaluations). `lost_points_share` = this criterion's lost points / all lost points in scope.
- **pass_rate** (binary): passing answers / applicable answered rows; passing = "yes", and for a critical criterion (C8) passing = not the critical value (no gross error). **mean_ratio** (scale): mean of points / max_points. Rows the judge could not answer (`answer = null` on an applicable row) are excluded from `n`, rates and lost points. `na_rate` = final N/A / `n`.
- **agreement**: rows with `review_state` confirmed or changed; categories as in `analytics.service` (15 = "15"), plus N/A as its own category (a reviewer confirming or reversing an N/A counts). `agreement_rate` = equal categories / reviewed; `kappa` = Cohen's kappa (`service.cohen_kappa`, null for fewer than 2 pairs or when undefined).
- **critical**: `count` = latest evaluations with `critical_error`; `rate` = count / evaluated; `by_type` from `violations()` (C8 classification; one dialog can carry two types, so the sum can exceed `count`); `reviewed` = listed violations whose C8 a reviewer confirmed or set.
- **quality**: over final totals; `p25`/`p75` by linear interpolation; `share_ge_85` counts 85.00 as ≥ 85.
- **trend**: latest evaluations by UTC date of `completed_at`, one item per day of the window; `period` sums the window.
- **cost**: every evaluation run in scope (any status: re-runs, partial re-evaluations and failed runs cost money). `per_dialog_usd` = `total_usd` / evaluated dialogs (fully loaded). `by_model` comes from `llm_traces` (an ensemble is split per model); evaluations without traces are attributed to their own `provider`/`model`. `period_usd` = runs completed in the trend window.
- **first_response**: identical to `/api/analytics/summary` (all conversations with a measured first response; threshold from the version's `scoring_rules`, default 180 s).
- **operators**: `count` = distinct named operators across all conversations; `evaluated` = named operators with an evaluation in scope; `at_risk` = operator groups (the unnamed group "—" included, listed last) with mean final total < 60 or any gross violation, sorted by gross violations, then mean.
- **Scorecard**: the same final views; `team_*` fields are the same statistics over every operator in scope. `coaching_points` = the 2 weighted, non-critical criteria with the most lost points (ties: lower pass rate first), examples = the dialogs with the largest loss, preferring verdicts a reviewer did not overturn and rows with an explanation; `expected` = the criterion instruction from the checklist. `strengths` = the 2 best of the remaining rankable criteria (value, then weight). C8 is never a strength or a coaching point: gross violations are in `violations`. Evidence quotes: at most 3, "supports" first, then "contradicts", then "context", with the transcript timestamp.
- **Labels without a filter**: criterion titles/weights come from the version most of the latest evaluations use (else the active version), so the table is labelled even before anything is evaluated.

## Permissions, audit, guards

Both endpoints are GET: permission `read`, open to every role including `visitor` and `operator` (contract §1). No write, so no audit event and no platform guard involved. No LLM call, no cost. Nothing in this package reads the actor; the operator portal passes the operator's own name.

## Tests (`api/tests/test_overview.py`, 33 tests, ~0.5 s)

In-memory SQLite (`StaticPool`), the organizer checklist from `seeds/checklist_freedom_v1.json`, appeals and decisions through the real `cqa.review.service` (so final totals are production-exact). Fixture: a perfect chat; a critical insult chat with a judge error row; a chat with a reviewer change (C7 yes→no), a confirmation and an open appeal; an N/A-heavy call where a reviewer changed C1 to N/A (63.75 → 85.00); a superseded older evaluation; a checklist v2 evaluation; a failed-only dialog; an operator without evaluations; an empty v3.

Covered: every overview field (coverage, quality incl. percentiles and thresholds, critical vs `violations()`, appeals funnel and timings, agreement incl. N/A and kappa, groups, criteria incl. judge-error exclusion and critical pass semantics, lost points and shares, top failing with examples and evidence order, trend windows 30/7/1 and clamping, period, cost totals/period/by_model with trace and fallback attribution, first response, operators at risk), version scopes (none, v2, empty v3), an empty database, read-only behaviour (session stays clean), scorecards (two named operators, the "—" group, an operator with only a failed evaluation, case-insensitive resolution, not found), and HTTP: status codes, validation envelope for `days`/`checklist_version_id`, 404 envelopes, URL-encoded / raw / double-encoded / lower-case names, the unchanged `GET /api/operators` and `GET /api/violations`, OpenAPI paths. No Claude API, no `api/data/cqa.db`.

## Limitations

- The overview describes the current state (latest evaluation per conversation); only the trend, `period` and `cost.period_usd` are time-windowed. Re-evaluating a dialog moves it to the re-evaluation date in the trend.
- Without a version filter, versions are mixed exactly like `/api/analytics/summary`; pass `checklist_version_id` for a clean comparison after a checklist change.
- Lost points ignore a `critical.mode = "flag_and_cap"` cap (the organizer rules use `flag_only`).
- `critical.by_type` relies on the C8 classifier of `cqa.analytics.violations` (C8 is hard-coded there); a custom critical criterion with another code is counted in `count` but appears in no type.
- Whole-table, in-memory aggregation (fine for thousands of evaluations; the demo DB answers in ~90 ms). No caching.
- The scorecard is readable by every role (soft permissions); see integration request 4.
- Operator names containing `/` cannot be addressed in the path.

## Integration requests (shared files this package may not edit)

1. `docs/API.md`: add a section "Executive overview and operator scorecards" with the two endpoints, parameters, errors and the response examples above (all additive).
2. `cqa/main.py` (optional): a global `RequestValidationError` handler that returns `{"error": {"code": "validation_error", "message": "...", "errors": [{"field", "message"}]}}`. Until then the two new routes do this themselves (`_EnvelopeRoute` in `routers/insights.py`); existing routes still return FastAPI's `{"detail": [...]}` on 422. With a global handler `_EnvelopeRoute` stays harmless.
3. Roles package (`GET /api/roles` home/nav): director and lead home → the overview page backed by `GET /api/overview`; operator portal → `GET /api/operators/{X-CQA-User}` (the header is already URL-encoded; the endpoint accepts it encoded once or twice).
4. Security middleware (optional policy, not in the current contract): if operators must not read colleagues' scorecards, allow `GET /api/operators/{name}` for role `operator` only when the decoded `name` equals the decoded `X-CQA-User` (403 `forbidden` otherwise).
5. `docs/ARCHITECTURE.md` §9.4 (optional): mention the executive overview and the operator scorecard next to Analytics.
