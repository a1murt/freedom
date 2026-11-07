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
