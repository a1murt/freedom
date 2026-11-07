# CQA API contract (v1)

Base URL: `http://localhost:8000` locally, `VITE_API_URL` in the web build. JSON everywhere, UTF-8, no auth. Errors: `{ "error": { "code": "...", "message": "..." } }` with 4xx/5xx. Timestamps ISO 8601 UTC.

## Conversations

`GET /api/conversations?channel=&language=&status=&min_total=&max_total=&critical=&q=&page=1&page_size=50`
→ `{ "items": [ConversationSummary], "total": 65 }`

```json
ConversationSummary {
  "id": "p1-ru-001", "channel": "agent chat", "language": "RU",
  "operator_name": "Алина", "turn_count": 12, "has_audio": false,
  "first_response_seconds": 37,
  "latest_evaluation": { "id": 12, "status": "completed", "total_score": 88.24,
      "final_total_score": 88.24, "critical_error": false, "provider": "anthropic",
      "mode": "single", "cost_usd": 0.17, "checklist_version_id": 1, "completed_at": "…" } | null,
  "review_state": "none|under_review|reviewed"
}
```

`GET /api/conversations/{id}` → `ConversationDetail = ConversationSummary + { "turns": [Turn], "audio_url_available": true, "imported_at": "…" }`

```json
Turn { "idx": 9, "timestamp_raw": "01:15", "offset_seconds": 75.0, "speaker": "operator",
       "text": "Здравствуйте! Меня зовут Алина…", "is_duplicate": false,
       "segment": "pre|operator|internal|post", "pii_masked_text": null }
```

`POST /api/datasets/import` body `{ "group_id": "DEV" }` → `{ "dataset_id": 1, "imported": 65, "by_language": {"RU": 35, "KZ": 15, "EN": 15}, "by_channel": {"agent chat": 60, "voice call": 5} }`

`POST /api/conversations/upload` multipart `file` (organizer JSON, or `.txt` with `speaker<TAB>text` lines) or JSON body `{ "conversation_id": "manual-001", "channel": "agent chat", "language": "RU", "transcript": [Turn-like {timestamp, speaker, text}] }` → `ConversationDetail`

`GET /api/audio/{id}` → audio/mpeg stream with Range support (resolves and caches the organizer MP3). `GET /api/audio/{id}/features` → `{ "duration_s": 312, "talk_ratio_operator": 0.62, "operator_wpm": 148, "long_pauses": [{"start": 71.2, "end": 76.9}], "overlaps": 2, "loudness_range_lu": 9.1 }`

## Checklists

`GET /api/checklists` → `[{ "id": 1, "name": "…", "active_version": ChecklistVersion, "versions": [{ "id", "version_no", "created_at", "change_note", "is_active" }] }]`

`GET /api/checklists/{id}/versions/{version_id}` → `ChecklistVersion`

```json
ChecklistVersion { "id": 1, "checklist_id": 1, "version_no": 1, "is_active": true,
  "change_note": "v1", "scoring_rules": {...}, "sections": [...], "criteria": [Criterion] }
Criterion { "id": 3, "code": "C3", "section": "S2", "title": "Идентификация", "order": 3,
  "answer_type": "binary|scale", "options": [{"value": "yes", "label": "Да", "points": 1, "anchor": null}],
  "max_points": 1, "weight": 20, "na_allowed": true, "na_condition": "…", "is_critical": false,
  "critical_value": null, "instruction": "…", "channel_overrides": {"voice call": {"instruction": "…"}},
  "judge_spec": { "definition": "…", "decision_steps": ["…"], "na_conditions": ["…"],
                  "evidence_requirement": "…", "channel_notes": {"voice call": "…"} } }
```

`POST /api/checklists` body `{ "name", "description", "scoring_rules", "sections", "criteria": [Criterion without id/judge_spec] }` → `ChecklistVersion` (version 1)

`POST /api/checklists/{id}/versions` body same shape + `{ "change_note" }` → new `ChecklistVersion` (judge_spec regenerated for changed criteria)

`POST /api/checklists/{id}/versions/{version_id}/activate` → `ChecklistVersion`

`GET /api/checklists/{id}/versions/{a}/diff/{b}` → `{ "added": [code], "removed": [code], "changed": [{ "code", "fields": ["instruction", "weight"] , "requires_rejudge": true }] }`

`POST /api/checklists/{id}/versions/{version_id}/judge-spec/{code}/regenerate` → `Criterion`

## Evaluations and jobs

`POST /api/evaluations/run` body
```json
{ "conversation_ids": ["p1-ru-001"] | null, "all": false,
  "checklist_version_id": 1, "provider": "anthropic|local|ensemble",
  "model": "claude-opus-5-5" | null, "mode": "full|partial", "base_version_id": null,
  "dry_run": false }
```
→ `{ "job_id": "j_01…", "queued": 65, "estimated_cost_usd": 11.5 }` (dry_run returns only the estimate)

`GET /api/jobs/{job_id}` → `{ "id", "type": "evaluate", "status": "queued|running|completed|failed|cancelled", "total": 65, "done": 12, "failed": 0, "cost_usd": 2.1, "started_at", "finished_at", "log": [{"ts", "level", "message"}] }`

`GET /api/jobs/{job_id}/events` → `text/event-stream`; events `job.progress {done,total,failed,cost_usd}`, `evaluation.started {conversation_id}`, `criterion.done {conversation_id, code, answer, score}`, `evaluation.done {conversation_id, evaluation_id, total_score, critical_error}`, `job.done {status}`

`POST /api/jobs/{job_id}/cancel` → job

`GET /api/conversations/{id}/evaluation` → latest `Evaluation`; `GET /api/conversations/{id}/evaluations` → history list; `GET /api/evaluations/{evaluation_id}` → `Evaluation`

```json
Evaluation { "id": 12, "conversation_id": "p1-ru-001", "checklist_version_id": 1,
  "status": "completed", "provider": "anthropic", "mode": "single", "model": "claude-opus-5-5",
  "total_score": 88.24, "final_total_score": 88.24, "critical_error": false,
  "sections": [{ "code": "S1", "title": "…", "score": 25.0, "max": 25.0 }],
  "cost_usd": 0.17, "input_tokens": 23110, "output_tokens": 3900, "latency_ms": 41000,
  "created_at": "…", "completed_at": "…",
  "criteria": [EvaluationCriterion] }
EvaluationCriterion { "id": 101, "code": "C3", "title": "Идентификация",
  "applicable": true, "na_reason": null, "answer": "yes" | 15, "score": 1, "max_points": 1,
  "weight": 20, "weight_applied": 20, "earned": 20.0, "confidence": 0.86,
  "explanation": "…", "evidence": [{ "turn_idx": 9, "speaker": "operator", "quote": "…",
      "offset_seconds": 75.0, "role": "supports|contradicts|context" }],
  "checked_span": { "from_turn": 9, "to_turn": 11 } | null,
  "flags": ["possible_factual_claim"], "votes": [{ "provider", "model", "answer", "score", "confidence" }] | null,
  "review_state": "none|under_review|confirmed|changed",
  "final_answer": "yes", "final_score": 1, "final_earned": 20.0,
  "appeal": Appeal | null }
```

`GET /api/evaluations/{evaluation_id}/trace` → `[{ "criterion_code", "provider", "model", "prompt_version", "request": {...}, "response": {...}, "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "latency_ms", "created_at" }]`

## Appeals and decisions

`POST /api/appeals` body `{ "evaluation_criterion_id": 101, "comment": "…", "author_label": "QA analyst" }` → `Appeal { "id", "evaluation_criterion_id", "comment", "author_label", "status": "open|decided", "created_at", "decision": Decision | null }`

`GET /api/appeals?status=open` → `[AppealWithContext]` where context adds `conversation_id`, `code`, `title`, original `answer`, `score`, `explanation`, `evidence`, `total_score`.

`POST /api/appeals/{id}/decision` body `{ "action": "confirm|change", "new_answer": "no" | 10 | null, "reason": "…", "reviewer_label": "Reviewer", "add_to_fewshot": true }` → `{ "appeal": Appeal, "evaluation": Evaluation }` (final totals recalculated)

`POST /api/appeals/preview` body `{ "evaluation_criterion_id": 101, "new_answer": "no" }` → `{ "final_total_score": 68.24, "section": {...} }`

## Analytics, settings, export

`GET /api/analytics/summary?checklist_version_id=` → `{ "count", "mean_total", "median_total", "distribution": [{"bucket": "80-90", "n": 12}], "by_language": [{"language", "mean_total", "n"}], "by_channel": [...], "criteria_heatmap": [{"code", "language", "yes_rate_or_mean"}], "operators": [{"operator_name", "n", "mean_total", "critical_count"}], "worst": [ConversationSummary], "agreement": [{"code", "n_reviewed", "agreement_rate", "kappa"}], "providers": [{"provider", "model", "n", "mean_cost_usd", "mean_latency_ms"}] }`

`GET /api/export/evaluations.csv?checklist_version_id=` → CSV: `conversation_id, channel, language, C1..C8 answer, C1..C8 score, C1..C8 applicable, total_score, final_total_score, critical_error, provider, model, evaluated_at`

`GET /api/export/evaluations/{evaluation_id}.pdf` → PDF card

`GET /api/settings` / `PUT /api/settings` → `{ "default_provider", "default_model", "explanation_language": "ru|en|kk", "pii_masking": true, "fewshot_from_reviews": true, "ensemble_members": [{"provider","model"}], "local_endpoint": "http://…/v1", "cost_rates": {...} }`

`GET /api/health` → `{ "ok": true, "db": "sqlite", "providers": { "anthropic": true, "local": false } }`

## Additions (2026-11-07 evening): roles, violations, results, waveform

All additive; existing fields unchanged.

### Datasets
`GET /api/datasets` → `[{ "id": 1, "group_id": "DEV", "source": "organizer_api", "imported_at": "…", "record_count": 65, "conversations": 65 }]` (one row per import; `conversations` = how many conversations currently point to it).
`GET /api/conversations?dataset_id=1` and `GET /api/conversations?operator=Алина` filter the list.

### Gross violations (criterion C8, export id `critical`)
`GET /api/violations?checklist_version_id=&type=insult|paid_bypass|disclosure|other&language=&channel=&operator=&include_reviewed_out=false`
```json
{ "total": 8,
  "counts": { "insult": 1, "paid_bypass": 4, "disclosure": 3, "other": 0 },
  "types": [{ "code": "insult", "ru": "Оскорбление клиента", "en": "Insulting the client", "kk": "Клиентті қорлау" }, …],
  "items": [{ "conversation_id": "p1-ru-029", "channel": "agent chat", "language": "RU", "operator_name": "Алексей",
      "evaluation_id": 12, "evaluation_criterion_id": 104, "total_score": 51.25, "final_total_score": 51.25,
      "types": ["insult"], "type_labels": ["Оскорбление клиента"], "explanation": "Грубые ошибки — Да. …", "confidence": 0.97,
      "evidence": [{ "turn_idx": 9, "speaker": "operator", "quote": "Вы вообще читать умеете?…", "timestamp": "01:31", "offset_seconds": 91 }],
      "review_state": "none", "ai_answer": "yes", "final_answer": "yes", "confirmed_by_reviewer": false, "overturned_by_reviewer": false,
      "evaluated_at": "…" }] }
```
Items are the latest evaluation per conversation where C8 is "yes" after reviewer decisions (pass `include_reviewed_out=true` to also see AI flags a reviewer overturned). Sorted by final total ascending.

### Operators (operator portal, coaching)
`GET /api/operators?checklist_version_id=` → `[{ "operator_name": "Алина", "conversations": 2, "evaluated": 1, "mean_total": 100.0, "critical_count": 0, "languages": ["RU"], "channels": ["agent chat"], "open_appeals": 0, "last_evaluated_at": "…" }]` (`"—"` groups conversations without a detected name).

### Appeals: authorship and filters
`POST /api/appeals` accepts `author_role` (`operator | analyst | reviewer | lead | methodologist | admin | director`) and `author_name` (operator name). When `author_role = "operator"` and `author_name` differs from the conversation's operator, the API returns 403 `not_your_dialog`.
`POST /api/appeals/{id}/decision` accepts `reviewer_role`.
`GET /api/appeals?status=&operator=&conversation_id=&author_role=`; items add `operator_name`, `author_role`, `author_name`, `na_allowed_for_channel` (N/A may be allowed by a voice-call override even when the base criterion does not allow it); decisions add `reviewer_role`.

### Results file (organizer JSON 1.0)
`GET /api/export/results.json?group_id=DEV|<control>&conversation_ids=&checklist_version_id=&team_name=&expect=10&download=true` → the file; headers `X-Results-Valid`, `X-Results-Errors`, `X-Results-Warnings`.
`GET /api/export/results/validate?group_id=&conversation_ids=&expect=` → `{ "valid": true, "errors": [], "warnings": [], "summary": "records=65 ok=65 …", "records": 65, "ok": 65, "error_records": [], "model": { "mode": "external_api", "name": "claude-opus-5-5" } }`.

### Waveform (two-track call view)
`GET /api/audio/{id}/peaks?buckets=600` → `{ "available": true, "duration_s": 312.26, "buckets": 600, "bucket_seconds": 0.52, "peaks": [0..1], "tracks": { "client": [0..1], "operator": [0..1], "other": [0..1] }, "spans": [{ "start": 15.5, "end": 21.0, "speaker": "operator" }] }`. Tracks are the mono peaks attributed to whoever speaks at that moment according to the transcript timing (bot/IVR/system and internal consultation → `other`).

### Settings
`GET/PUT /api/settings` also carry `mascot_enabled` (default `false`, organizer guidance: no cartoon characters by default), `default_theme` (`light`), `team_name` (used in results.json).

### Checklist channel overrides
`criteria[].channel_overrides["voice call"]` may contain `na_allowed: true` and `na_condition` (C1 on calls: N/A when the greeting is not in the recording, per the organizers). The UI should offer N/A for that criterion on calls.
