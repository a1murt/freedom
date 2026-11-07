# CQA · Conversation Quality Analytics for Freedom Broker

Architecture and design spec for the 24-hour CQA hackathon. Companion document: [PLAN.md](PLAN.md) (phases, epics, user stories, owners).

Status: draft v1, 2026-11-07. Open inputs are listed in section 15.

---

## 0. Thesis: how this wins

The organizers told us (Telegram) that the top-50 cut is decided mainly by **accuracy against hidden ground truth on 10 unseen transcripts**, then by functionality, usability, **proof that the system really evaluates new dialogs**, and **diversity of architectures (local vs cloud LLM)**. The product owner is a Dynamics 365 shop (the dataset API is Power Automate, the recordings are served from `freedomholdingcorporation.crm11.dynamics.com`), and they said the real goal is a *flexible checklist system that closes the business task*, not a one-off scorer.

So the solution is built around five bets:

1. **Accuracy engine, not a prompt.** Per-criterion structured judging, evidence that is validated against the transcript, a deterministic scoring engine, a hand-labeled gold set on DEV, and a calibration loop with measured agreement. Ensemble voting across models for the control run.
2. **Proof of work.** Every verdict carries a trace: model, prompt version hash, request and response, tokens, cost, latency. A jury member can paste a brand-new dialog and watch it get evaluated live.
3. **Hybrid inference.** One provider interface, three backends: Claude (cloud, primary), a local open-weight model via Ollama (data-sovereignty story for a broker), and an ensemble mode. The UI shows which one produced each verdict.
4. **Checklist as a product.** Versioned checklists, any criterion type, sections, weights, N/A rules, channel adaptations (chat vs call), and *partial re-evaluation*: change one criterion and only that criterion is re-judged across the dataset. This is exactly what the control stage tests.
5. **Human in the loop that feeds back.** Appeals and reviewer decisions are stored, recalculated, and become few-shot examples for the judge. An agreement dashboard shows AI-vs-reviewer kappa per criterion.

Everything else (dashboards, audio insights, exports, D365 embed mode) is built on top of these.

---

## 1. Requirements digest and traceability

Source: `CQA_Hackathon_Student_Guide_v3.pdf`, organizer Telegram messages.

| ID | Requirement (guide) | Where it lands |
|---|---|---|
| R1 | Checklist editor: add/edit criteria, wording, weight, answer type, N/A availability, numeric scale; new criterion applies through settings; save configuration | Checklist Studio (§9.3), checklist schema (§5) |
| R2 | Evaluate a selected dialog: upload transcript, pick checklist, run; RU/KZ/EN; bot and system turns are context only | Evaluation pipeline (§6), judge prompts (§7) |
| R3 | Show per criterion: answer, points, weight, explanation, evidence turn; for a missed action show the checked span; N/A with reason; total and section totals | Conversation Card (§9.2), verdict schema (§7.3) |
| R4 | Weighted total, N/A weight redistribution, critical error flagged separately, rounding rules from the checklist PDF | Scoring engine (§5.4) |
| R5 | Card: list → transcript + result; evidence link jumps to the message or audio fragment; audio player | Conversation Card, audio proxy (§8) |
| R6 | Appeal: pick criterion, comment, save; reviewer sees disputed score, comment, fragment | Appeals (§10) |
| R7 | Decision: confirm or change, reason, recalculated total; card shows original, decision, final | Appeals (§10), scoring engine |
| R8 | Data API: GET by group_id, response format, roles client/operator/bot/system, MM:SS for chats, ISO 8601 for calls, audio_url for calls | Ingest adapter (§6.1) |
| R9 | Control stage at H+23: new group_id, replace one criterion via UI, re-apply checklist | Import by group_id, checklist versioning, partial re-evaluation |
| R10 | HTML/CSS/JS UI adaptable as a Dynamics 365 HTML component; no login | Frontend build (§9.5), embed mode |
| R11 | Open Git repo, README with run steps, commit before stop-code | Repo layout (§12) |
| R12 | State model name and approximate cost per evaluation | Cost model (§7.7), Trace tab, Settings |
| R13 | CORS: fetch data through backend | Backend proxy (§6.1, §8) |
| R14 | 5-minute demo: UI + dialog → score, explanation, evidence jump, N/A, total → appeal, decision, new total → change criterion, re-apply | Demo script (§14) |

Judging criteria (Telegram) and our response:

| Criterion | Response |
|---|---|
| Accuracy vs ground truth | §7 (judge design), gold set and calibration loop, ensemble for the control run |
| Functionality and unique features | §11 feature list: trace, hybrid inference, partial re-eval, audio insights, agreement dashboard, PII masking, exports |
| Usability | Three-screen core flow, evidence-first card, keyboard-less demo path, RU/EN/KZ UI |
| Correctness of process (not canned results) | Trace tab, live "paste a dialog" evaluation, timestamps, job log, no precomputed results in the repo |
| Diversity of approaches | Provider switch: cloud Claude, local Ollama, ensemble |

---

## 2. Dataset facts that shape the design

From the DEV pull (65 records, fetched 2026-11-07):

- 60 synthetic chats: 30 RU, 15 KZ, 15 EN. 5 real RU voice calls (`p4-ru-001..005`).
- Turns per conversation: 11 to 115. Speakers across the set: client 505, operator 339, bot 315, system 109.
- Every chat starts with the AIM bot, a privacy consent line, a bot answer, then an escalation system event, then the operator. The operator segment is what we score; bot and system turns are context.
- Call transcripts are noisy ASR: IVR prompts garbled, duplicated consecutive lines, PII masks like `[Имя скрыто]`, `[Персональные данные скрыты]`, system events for recording pause and resume, operator names misheard.
- Chat timestamps are `MM:SS`; call timestamps are ISO 8601 with offset. The recording starts at the first system event, and pause/resume events mean audio offsets must subtract paused intervals.
- `audio_url` for calls does not return audio. It returns an HTML player page whose `<source src>` is a Dynamics file-download URL with a SAS token valid for about one hour. The real file is MP3, 16 kHz mono, about 5 minutes for `p4-ru-001`. The backend must resolve the page, extract the URL, download, and cache.
- KZ chats cite Russian UI labels; EN chats cite Russian UI labels too («Кабинет»). The judge must treat that as normal, not as a language error.

The full profile (topics, cross-language scenario pairs, operator failure modes with ids, claims ledger seed, per-call quality, engineering notes, inferred rubric) is in [DATASET_NOTES.md](DATASET_NOTES.md). Three facts matter most for the judge: the escalation system event is always at index 8 and the operator segment starts at 9 or 10; five chats end with "Оператор закрыл чат" and it is always a negative signal, while "Соединение прервано" is neutral; compliance violations (paid private advice, funds to a personal card, disclosure without ID) are paired with otherwise correct answers, so they must be gating rather than one point among eight.

---

## 3. System architecture

```
┌──────────────────────────────── Browser ────────────────────────────────┐
│  CQA Web (React + Vite, static bundle, hash-router, i18n RU/EN/KZ)       │
│  Conversations · Card · Checklist Studio · Review Queue · Analytics      │
│  Embed mode (?embed=1&conversation=ID) for Dynamics 365 web resource     │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ REST + SSE (JSON)
┌───────────────▼──────────────────────────────────────────────────────────┐
│  CQA API (FastAPI, Python 3.12)                                           │
│                                                                           │
│  ingest/      Power Automate adapter · file upload · normalizer           │
│  checklist/   schema · versions · rubric compiler · diff                  │
│  scoring/     deterministic weights · N/A redistribution · rounding       │
│  judge/       provider interface · prompts · validators · ensemble        │
│  audio/       player-page resolver · mp3 cache · features (ffmpeg)        │
│  review/      appeals · decisions · recalculation · few-shot memory       │
│  jobs/        async batch runner · SSE progress · cost accounting         │
│  analytics/   aggregates · agreement metrics · exports                    │
└──────┬───────────────┬──────────────────┬──────────────────┬─────────────┘
       │               │                  │                  │
┌──────▼──────┐ ┌──────▼──────┐  ┌────────▼───────┐  ┌───────▼────────┐
│ SQLite /    │ │ Claude API  │  │ Ollama (local) │  │ Organizer API  │
│ Postgres    │ │ Opus 5.5    │  │ Qwen3 8B/14B   │  │ Power Automate │
│ (SQLAlchemy)│ │ Sonnet 5.5  │  │ or GPU vLLM    │  │ + Dynamics mp3 │
└─────────────┘ └─────────────┘  └────────────────┘  └────────────────┘
```

Why this shape:

- **Python backend** because the ML teammate works in Python and the judge, audio, and calibration code all live there. One service, no microservices.
- **Static frontend bundle** because the guide asks for HTML/CSS/JS that can become a Dynamics 365 HTML web resource. A Vite build with inlined assets is a single `index.html` that drops into D365; a hash router avoids server rewrites inside an iframe.
- **SQLite by default, Postgres by env var** so `docker compose up` works with zero configuration for the jury, and the same code deploys to Supabase or Railway Postgres for the hosted demo.
- **SSE for batch progress** so the control run at H+23 shows live progress in the UI without websockets.

---

## 4. Data model

Tables (SQLAlchemy 2, Alembic migrations):

- `datasets` (id, group_id, source, imported_at, record_count)
- `conversations` (id = conversation_id from source, dataset_id, channel, language, audio_url, operator_name nullable, meta JSON, imported_at)
- `turns` (id, conversation_id, idx, timestamp_raw, offset_seconds nullable, speaker, text, is_duplicate bool)
- `checklists` (id, name, description, created_at)
- `checklist_versions` (id, checklist_id, version_no, is_active, scoring_rules JSON, created_at, created_from_version nullable, change_note)
- `criteria` (id, checklist_version_id, code, order, section, title, instruction, answer_type, scale JSON, weight, na_allowed, is_critical, channel_overrides JSON, judge_spec JSON)
- `evaluations` (id, conversation_id, checklist_version_id, status, provider, mode, total_score, final_total_score, critical_error, cost_usd, input_tokens, output_tokens, latency_ms, created_at, completed_at, job_id)
- `evaluation_criteria` (id, evaluation_id, criterion_id, applicable, na_reason, answer, score, max_score, weight_applied, confidence, explanation, evidence JSON, checked_span JSON, votes JSON, final_answer, final_score, review_state)
- `appeals` (id, evaluation_criterion_id, comment, author_label, status, created_at)
- `decisions` (id, appeal_id, action confirm|change, new_answer, new_score, reason, reviewer_label, created_at)
- `llm_traces` (id, evaluation_criterion_id nullable, evaluation_id, provider, model, prompt_version, request JSON, response JSON, input_tokens, output_tokens, cache_read_tokens, cost_usd, latency_ms, created_at)
- `audio_assets` (conversation_id, local_path, duration_s, fetched_at, features JSON)
- `jobs` (id, type, status, total, done, failed, started_at, finished_at, params JSON, log JSON)
- `fewshot_examples` (id, criterion_code, language, channel, transcript_excerpt, verdict JSON, source appeal|gold, created_at)

Invariants:

- An `evaluation` is immutable once `completed`; reviews write to `final_*` columns and `decisions`, never overwrite the AI verdict.
- `final_total_score` is recomputed by the scoring engine from `final_score` per criterion every time a decision is saved.
- Re-evaluating a conversation creates a new `evaluation`; the card shows the latest, with history available.

---

## 5. Checklist engine

### 5.1 Checklist schema (JSON, stored per version)

```json
{
  "name": "Freedom Broker · Chat QA v1",
  "scoring_rules": {
    "total_scale": 100,
    "normalize_by": "max_points",
    "na_policy": "redistribute_weight",
    "rounding": {"mode": "half_up", "decimals": 0},
    "critical": {"mode": "flag_and_cap", "cap_total": 0},
    "sections_enabled": true
  },
  "criteria": [
    {
      "code": "C1",
      "section": "Opening",
      "title": "Greeting and introduction",
      "instruction": "Operator greets the client and introduces themselves by name in the first operator message.",
      "answer_type": "binary",
      "scale": {"yes": 1, "no": 0},
      "weight": 10,
      "na_allowed": false,
      "is_critical": false,
      "channel_overrides": {
        "voice call": {"instruction": "Operator greets, names the company and themselves at the start of the operator segment."}
      }
    },
    {
      "code": "C6",
      "section": "Communication",
      "title": "Speech and writing quality",
      "answer_type": "scale",
      "scale": {"min": 0, "max": 3, "anchors": {"0": "...", "1": "...", "2": "...", "3": "..."}},
      "weight": 15,
      "na_allowed": true
    }
  ]
}
```

`answer_type` is `binary` (Да/Нет) or `scale` (listed steps with anchors); both are expressed as `options[]` with `value`, `label`, `points`, so the UI and the engine treat them uniformly. The organizer's eight criteria, weights, scales, N/A conditions, rounding, and the gross-error rule are shipped as `api/seeds/checklist_freedom_v1.json` and loaded as version 1 on first start. Each criterion also carries `channel_overrides["voice call"]` with the organizer's guidance for calls (criterion 4: answered every question, no wrong information, explained actions; criterion 6: logic, clarity, no inappropriate wording, pause management, pace and diction when audio exists).

### 5.2 Rubric compiler

For every criterion the backend derives a `judge_spec`: a decision procedure the model follows. It is generated once per criterion version by Claude from the criterion fields (instruction, scale anchors, N/A rule, channel override) and then **hand-reviewed by the team** and stored with the version. Fields:

- `definition` (what the criterion means in this company's context)
- `decision_steps` (ordered checks the judge performs)
- `pass_examples` / `fail_examples` (short, in RU; filled from gold set and reviewer decisions)
- `na_conditions` (explicit list)
- `evidence_requirement` (what to quote; for missed actions, what span to report as checked)
- `channel_notes` (chat vs call adaptations, per organizer guidance on criteria 4 and 6)

Editing a criterion in the UI regenerates its `judge_spec` (visible and editable in an "Advanced" drawer), creating a new checklist version.

### 5.3 Versioning and partial re-evaluation

Saving a checklist creates a new version; the previous one stays attached to past evaluations. The diff between versions is computed per criterion (added, removed, changed instruction, changed weight, changed scale). "Apply to dataset" offers two modes:

- **Full re-evaluation**: new evaluations for all selected conversations.
- **Partial re-evaluation**: only criteria whose judge-relevant fields changed are re-judged; other verdicts are copied; totals are recomputed. Weight-only changes need no LLM calls at all.

This makes the control-stage "replace one criterion" step take minutes and cost cents, and it is a visible feature in the demo.

### 5.4 Scoring engine (pure functions, unit-tested)

Rules taken verbatim from the organizer checklist (`CQA_Chat_Quality_Checklist`, page 2, "Порядок расчёта"), encoded in `api/seeds/checklist_freedom_v1.json`:

```
# 1. applicability: N/A only where allowed and the stated condition holds
A          = { i : applicable_i and weight_i > 0 }
# 2. answer: "Да" gives full weight, "Нет" gives zero; scale picks one listed step
# 3. scale → points proportional to weight; the top step gives the full weight
earned_i   = weight_i * points_i / max_points_i
# 4. exclude N/A and redistribute their weights proportionally; max stays 100
W          = Σ_{i∈A} weight_i
total      = Σ_{i∈A} earned_i * (total_scale / W)
# 5. round to two decimals; if no applicable weighted criteria, no total
total      = round_half_up(total, 2)   or   null when W == 0
section_s  = same formula restricted to section s (informational)
critical   = answer_C8 == "yes"        # "Грубые ошибки": flagged separately, no points
```

Checklist facts the engine encodes: weights 10/15/20/20/15/15/5 (sum 100) plus criterion 8 with no weight; criterion 4 scale 20/15/10/5/0 and criterion 6 scale 10/7/4/0 with written anchors; N/A allowed on criteria 2, 3, 4, 7 only, each with its own condition; the 3-minute first-response threshold, tags and attachments are explicitly outside the score (we show first-response time as information only). `critical.mode` defaults to `flag_only` to match the PDF; `flag_and_cap` stays available for other checklists.

The engine is the single source of truth for totals, section totals, and recalculation after review. The LLM never does arithmetic. Golden tests cover: all applicable (max 100), one N/A (weights rescale), several N/A, all weighted criteria N/A (total null), gross error flag with a high total, rounding edge cases (x.xx5), and weight edits between versions.

---

## 6. Evaluation pipeline

```
import ──▶ normalize ──▶ segment ──▶ mask PII ──▶ pack ──▶ judge ×N criteria ──▶ validate ──▶ aggregate ──▶ score ──▶ persist + trace
```

### 6.1 Ingest adapters

- `PowerAutomateAdapter.fetch(group_id)`: GET the organizer URL with the given `group_id` (the signed base URL is in `.env`), validate `isSuccess`, upsert conversations and turns. Used for DEV now and the control group at H+23.
- `FileAdapter`: upload JSON in the organizer format, or plain text / CSV with `speaker<TAB>text` lines, or paste a transcript in the UI. Parses `MM:SS` and ISO timestamps.
- All external fetches go through the backend (CORS requirement R13).

### 6.2 Normalize and segment

- Keep original timestamps; add `offset_seconds` (chat: parsed MM:SS; call: delta from the first transcript timestamp, subtracting recording-paused intervals detected from system events).
- Mark exact duplicate consecutive lines from the same speaker within 1 s as `is_duplicate` (present in call ASR); the judge sees them once, the UI shows them dimmed.
- Compute `operator_segment`: first operator turn index to end. Bot and system turns before it are context. The judge prompt labels every turn with `[idx] speaker (timestamp)` so evidence can reference `idx`.

### 6.3 PII masking (toggle, default on for cloud providers)

Regex pass for phone numbers, 12-digit IIN, card numbers, emails, IBAN; replaces with typed placeholders (`[PHONE_1]`). Names are left as-is for chats (needed to judge "introduced themselves"); calls already come masked by the source. The mask map is kept server-side so the UI shows the original text. This is a compliance talking point for a broker, and it is honest: cloud models see masked text, the local model sees everything.

### 6.4 Judge fan-out

For each criterion in the version, build one judge request (§7) and run them concurrently (bounded, default 8 in flight per evaluation, 4 evaluations in parallel per job). Each verdict is validated:

- `evidence[].turn_idx` must exist and belong to the expected speaker; `quote` must fuzzy-match the turn text (ratio ≥ 0.7) or it is dropped and confidence is reduced.
- A failed binary criterion with no evidence and no `checked_span` is re-asked once with a stricter instruction.
- `applicable=false` requires a non-empty `na_reason`; otherwise re-asked.
- Answers are coerced to the criterion scale; out-of-range values are rejected and re-asked.

### 6.5 Aggregate, score, persist

Votes (if ensemble) are merged (§7.5), the scoring engine computes totals, and everything is written in one transaction with the traces. The job emits SSE events `evaluation.started`, `criterion.done`, `evaluation.done`, `job.progress`.

---

## 7. LLM judge design

### 7.1 Providers

`JudgeProvider` interface: `complete(system, messages, schema, options) -> (parsed_json, usage, raw)`. Implementations:

| Provider | Model | Role | Notes |
|---|---|---|---|
| `anthropic` | `claude-opus-5-5` | Primary judge | Structured outputs via `client.messages.parse` with a Pydantic schema; adaptive thinking; effort `high` for scoring, `medium` for extraction; server-side refusal fallback enabled |
| `anthropic` | `claude-sonnet-5-5` | Second voice, dev iterations | Same schema; roughly half the cost |
| `openai_compatible` | vLLM on the university GPU cluster serving `Qwen3-32B` (or `Qwen3-30B-A3B` for speed) | Local judge, the data-sovereignty option | OpenAI-compatible `/v1/chat/completions` with JSON schema response format; the ML teammate owns deployment; a tunnel exposes it to the API |
| `ollama` | `qwen3:8b` on the M4 | Fallback local judge | Same adapter as above (Ollama speaks the OpenAI-compatible API); weaker in KZ, flagged in UI |
| `ensemble` | Opus + Sonnet (+ local) | Control run | Vote per criterion, arbiter on disagreement |

Sampling temperature is not available on Claude 5.5 models, so self-consistency is achieved by cross-model voting rather than resampling.

### 7.2 Prompt layout (per criterion call)

```
system:   role (QA auditor for a broker's support desk) · global rules · language policy
          · evidence rules · N/A rules · output contract           [stable, cacheable]
user[0]:  conversation header (id, channel, language, operator segment start)
          + transcript as "[idx] speaker (ts): text"                [cache_control breakpoint]
user[1]:  criterion judge_spec (definition, decision steps, anchors, N/A conditions,
          channel notes) + up to 3 few-shot examples matched by language and channel
          + "Return the verdict JSON."
```

Putting the transcript before the criterion block lets the eight criterion calls share a cached prefix when the transcript is long enough to cross the cache minimum; otherwise it costs nothing extra.

Language policy: judge in the dialog's language, write `explanation` in the UI language chosen in Settings (default RU for QA staff), quote evidence verbatim in the original language.

### 7.3 Verdict schema

```json
{
  "criterion_code": "C4",
  "applicable": true,
  "na_reason": null,
  "answer": "partial",
  "score": 1,
  "confidence": 0.78,
  "evidence": [
    {"turn_idx": 9, "speaker": "operator", "quote": "В веб-версии откройте вкладку «Кабинет»…", "role": "supports"},
    {"turn_idx": 4, "speaker": "client", "quote": "Я сейчас с ноутбука…", "role": "context"}
  ],
  "checked_span": {"from_turn": 9, "to_turn": 11},
  "explanation": "Оператор дал корректный путь, но не уточнил, что тариф виден и в приложении…",
  "flags": ["possible_factual_claim"],
  "claims": [{"turn_idx": 9, "claim": "Тариф отображается под ФИО во вкладке Кабинет", "status": "unverified"}]
}
```

### 7.4 Knowledge check for consultation correctness

Criterion 4 (correctness and completeness of the consultation) needs reference material. Two layers:

1. **Claims ledger**: operator claims extracted from all DEV dialogs (menu paths, fees, timelines) and clustered; claims contradicted by the majority of other dialogs are flagged. Generated once by the pipeline, reviewed by the team, stored as `seeds/claims_ledger.json`.
2. **Reference docs** (if organizers share the product materials): chunked and embedded (local `bge-m3` or Claude-generated summaries), top-k retrieved into the criterion-4 prompt as "reference facts". If none are provided, the ledger alone is used and the UI labels claims as "unverified" rather than "wrong".

### 7.5 Ensemble and arbiter

- Each criterion is judged by K providers. Binary and choice answers take the majority; scale answers take the median; confidence is averaged.
- On disagreement the `arbiter` call (Opus, effort `high`) receives the transcript, the criterion spec, and the competing rationales, and returns a final verdict with a note. The card shows "2 of 3 judges agreed" and the arbiter note.
- All votes are stored in `votes` and visible in the Trace tab.

### 7.6 Calibration loop (the ML teammate's core job)

1. **Gold set**: 20 DEV conversations (10 RU incl. 2 calls, 5 KZ, 5 EN) labeled independently by both teammates using the organizer checklist, then reconciled. Store as `eval/gold.json`. Measure human-human agreement first; it is the ceiling.
2. **Harness**: `python -m cqa.eval --gold eval/gold.json --provider anthropic --prompt-version v2` prints per-criterion exact-match, MAE, quadratic-weighted kappa, total-score MAE, cost, and a confusion table. Results are appended to `eval/runs.csv`.
3. **Hill-climb** prompt versions (`prompts/v1..vN`) on a train split of 12, validate on 8. Change one thing per version: anchors, decision steps, few-shots, evidence rules, N/A conditions. Keep the best by validation kappa.
4. **Reviewer feedback**: accepted decisions become `fewshot_examples` for that criterion; the harness re-runs to confirm no regression.
5. **Control-run config** is frozen before H+22: provider mode, prompt version, effort, few-shot bank.

### 7.7 Cost model (stated in Settings and Trace)

Per chat evaluation, eight criterion calls, average chat about 1.2k transcript tokens:

| Mode | Approx. cost per chat | Per call (5 min, ~5k tokens) |
|---|---|---|
| Opus 5.5 single | $0.15–0.20 | $0.30–0.40 |
| Sonnet 5.5 single | $0.07–0.10 | $0.15–0.20 |
| Local Qwen3 | $0 (electricity) | $0 |
| Ensemble Opus + Sonnet + arbiter | $0.30–0.40 | $0.60–0.80 |

Full DEV run in ensemble mode is roughly $20; the control set of 10 is under $5. Budget available: $205 of Claude API credit. Actual per-evaluation cost is computed from `usage` on every response and shown on the card.

---

## 8. Audio pipeline for calls

- `GET /audio/{conversation_id}` resolves `audio_url` → player HTML → `<source src>` → downloads the MP3 once into `data/audio/` and streams it with range support. Re-resolves if the cached SAS link is stale. The frontend player never touches the organizer URL (no CORS, no expiry).
- Features computed with ffmpeg and the transcript (no ML needed): duration, operator and client talk time, talk ratio, pauses over 3 s (`silencedetect`), operator speech rate (words per minute per turn from timestamps), overlapping turns, loudness range (`ebur128`). Optional: `faster-whisper small` word-level confidence as a diction proxy if time allows.
- The acoustic summary is passed to criterion 6 for calls (organizer guidance: logic, clarity, pauses, pace, diction) and shown as chips on the card: "pace 148 wpm · 3 long pauses · talk ratio 62/38".
- Evidence for calls carries `offset_seconds`; clicking it seeks the player.

---

## 9. Frontend

Stack: React 18, Vite, TypeScript, Tailwind, shadcn/ui, TanStack Query, react-router (hash), i18next (RU default, EN, KZ), recharts for analytics. Design tokens from the user's Figma kit (dark theme, mascot) recolored to Freedom green (`#51AF3D` primary, `#0F5431` deep). Roles are a header toggle (QA analyst / Reviewer), no login (R10).

### 9.1 Conversations

Table with channel, language, operator, turns, latest total, critical flag, status, cost. Filters, search, select-all. Actions: Import by group_id, Upload file, Paste transcript, Evaluate selected (choose checklist version and provider mode), live progress bar from SSE, cost estimate before run and actual after.

### 9.2 Conversation Card

Two panes. Left: transcript with speaker colors, bot and system turns dimmed, duplicate lines collapsed, PII badges, search, and for calls a player with waveform and acoustic chips. Right: total ring, critical banner if any, section totals, criterion rows (answer, points of max, weight, confidence bar, explanation, evidence chips). Clicking a chip scrolls and highlights the turn or seeks audio. N/A rows show the reason. For missed actions the checked span is highlighted as a range. Buttons: Appeal (per criterion), Re-evaluate, Export PDF/JSON. Tabs: Result · History (original → appeal → decision → final) · Trace (model, prompt version, tokens, cost, latency, request and response JSON, votes) · Audio insights (calls).

### 9.3 Checklist Studio

List of checklists and versions. Editor: criteria table with inline editing of section, title, instruction, type, scale and anchors, weight, N/A allowed, critical flag, channel overrides; drag to reorder; "Advanced" drawer with the generated judge_spec. Save creates a version with a change note; Diff view; "Apply to dataset" with full or partial mode and a dry-run cost estimate. Import and export as JSON.

### 9.4 Review Queue and Analytics

Review Queue: appeals with status, criterion, comment, evidence excerpt, original score; decision form (confirm or change, new answer, reason); shows recalculated total before saving. Analytics: distribution of totals, criterion heatmap by language and channel, operator leaderboard, worst dialogs, AI-vs-reviewer agreement per criterion, cost and latency per provider.

### 9.5 Dynamics 365 readiness

- `vite-plugin-singlefile` produces one `index.html` that can be uploaded as an HTML web resource; the API base URL comes from `window.CQA_CONFIG` or a query parameter.
- Embed mode `?embed=1&conversation=<id>` renders only the Card, which is what a D365 Conversation form tab would show. The data adapter interface has a `DataverseAdapter` stub that maps `msdyn_transcript` records (base64 `documentBody` in annotations) to our transcript format, documented but not wired.
- No cookies, no auth, no absolute paths, hash routing, so the bundle works inside the D365 iframe sandbox.

---

## 10. Appeals and review

1. Analyst opens a criterion row → Appeal → comment → save. The row shows "Under review".
2. Reviewer toggles role → Review Queue → sees the disputed verdict, comment, evidence excerpt, transcript link.
3. Reviewer confirms or changes the answer, enters a reason, sees the recalculated total, saves. The decision is immutable; a second appeal creates a new thread.
4. The card shows original score, decision, final score, and the audit trail. Accepted changes feed `fewshot_examples` for the criterion (opt-in toggle in Settings).

---

## 11. Feature list for the "functionality and unique features" axis

Core (must ship): import by group_id, upload, paste; checklist editor with versions; batch evaluation with progress; card with evidence jumps; N/A with reasons; totals and sections; appeals and decisions with recalculation; audio playback with seek; README and Docker.

Differentiators (ship in order):

1. Trace tab and live evaluation of a pasted dialog (proof of process).
2. Provider switch: cloud, local, ensemble; the card badge says which.
3. Partial re-evaluation on criterion change with cost preview.
4. Acoustic insights for calls.
5. Agreement dashboard (AI vs reviewer) and reviewer feedback into few-shots.
6. PII masking for cloud calls.
7. Claims ledger and reference-fact retrieval for consultation correctness.
8. Exports: CSV of per-criterion scores (ground-truth comparison format), PDF card, JSON API.
9. Embed mode and Dataverse adapter stub for D365.

---

## 12. Repository layout and run steps

```
freedom/
  README.md                 run in 3 commands, architecture summary, model and cost statement
  docker-compose.yml        api + web (+ ollama profile)
  .env.example              ANTHROPIC_API_KEY, ORGANIZER_API_URL, DATABASE_URL, DEFAULT_PROVIDER
  api/                      FastAPI app (cqa package), alembic, tests, prompts/, seeds/, eval/
  web/                      Vite React app, i18n, design tokens
  docs/                     ARCHITECTURE.md, PLAN.md, DATASET_NOTES.md, DEMO.md
  deck/                     pitch deck export
```

Run: `cp .env.example .env` → add the Claude key → `docker compose up` → open `http://localhost:5173`. Without Docker: `uv run fastapi dev` and `pnpm dev`.

Both run modes are supported and documented, because the jury evaluates the fixed commit and should not need to run anything locally:

- **Hosted (primary for the jury):** web on Vercel (static bundle), API on Railway as a Docker service with a persistent volume for SQLite and cached audio (Postgres on Railway or Supabase is a one-line `DATABASE_URL` switch). The hosted instance has DEV pre-imported and pre-evaluated; the control set is imported there at H+23. The README's first line is the public URL.
- **Local (reproducibility):** `docker compose up` from the tagged commit gives the same app with an empty database and the same seed checklist; the README shows the three commands and the import button.

Repo hygiene: hackathon materials (PDF, video, Telegram notes, signed API URL) are git-ignored; the signed URL lives only in `.env`.

---

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Checklist PDF not yet in hand; scoring rules unknown | Engine is rule-driven; seed file is swapped when the PDF arrives; ask organizers early |
| Ground-truth format unknown | Export per-criterion answers, points, and totals; keep both raw and rounded totals |
| Claude rate limits or outage at H+23 | Start the control run immediately; ensemble falls back to Sonnet then local; results cached per criterion, resumable job |
| Audio SAS links expire | Backend re-resolves the player page on demand; MP3 cached locally |
| KZ quality of local model | Local mode is a demonstrated option, cloud is default; UI shows a language-support note |
| Judges suspect canned results | Trace tab with timestamps and raw payloads; live paste-and-evaluate in the demo; no result files committed |
| 24-hour scope creep | MVP cut at H+8, feature cut at H+18, freeze at H+22; differentiators shipped in the order in §11 |
| Team parallelism conflicts | Backend and ML share the `judge/` package behind the provider interface and the verdict schema; contracts frozen at H+2 |

---

## 14. Demo script (5 minutes, maps to the guide's plan)

1. (0:00) Conversations list: filters, a KZ chat and a RU call visible; click a RU chat. Provider badge shows "Claude · ensemble".
2. (1:00) Card: total ring, criterion rows; click an evidence chip → transcript scrolls and highlights; show a N/A row with its reason; show the checked span on a missed-action criterion; open Trace tab for ten seconds (model, cost, raw JSON).
3. (2:30) Switch to the call: play from an evidence timestamp; acoustic chips.
4. (3:00) Appeal criterion 4 with a comment → toggle Reviewer → Review Queue → change the score with a reason → card shows original, decision, final total.
5. (4:00) Checklist Studio: edit a criterion's wording and weight → save as v2 → Apply (partial) → progress → new totals on the list; paste a fresh dialog from the jury and evaluate it live.

---

## 15. Inputs status

1. Organizer checklist PDF: received and encoded (`api/seeds/checklist_freedom_v1.json`, §5).
2. Product materials: none from the organizers; a public-web knowledge base is being compiled into `docs/knowledge/freedom_products.md` for the reference-fact layer.
3. Hackathon started 2026-11-07; the phase clock in PLAN.md counts from that start.
4. Claude API key: in `.env` (git-ignored). ML teammate has access to the university GPU cluster; brief in `docs/ML_BRIEF.md`.
5. Hosting: both hosted (Vercel + Railway) and local Docker, §12. Design: Figma kit recolored, prompt in `docs/DESIGN_SYSTEM_PROMPT.md`.
