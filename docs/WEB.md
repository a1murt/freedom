# CQA web app (`web/`)

Vite + React 18 + TypeScript, Tailwind (v3), shadcn-style primitives on Radix, TanStack Query, react-router `HashRouter`, i18next (RU default, EN, KK), recharts, sonner toasts. Talks to the API described in `docs/API.md`.

## Run

```bash
cd web
pnpm install
pnpm dev              # http://localhost:5173, proxies /api -> http://localhost:8000
VITE_MOCK=1 pnpm dev  # same UI from bundled fixtures, no backend needed
pnpm typecheck        # tsc --noEmit
pnpm build            # dist/  (multi-file, served by nginx in Docker)
pnpm build:single     # dist-single/index.html (one file, ~1.7 MB, for a Dynamics 365 web resource)
```

Backend for local data: `cd api && .venv/bin/uvicorn cqa.main:app --port 8000`. Hosted API: `https://api-production-58ab4.up.railway.app` (CORS `*`), usable with `VITE_API_URL` or the `?api=` override below.

## Routes (hash router)

| Route | Screen |
|---|---|
| `#/conversations` | Table with filters (channel, language, status, score range, search, gross-error only), select-all, Import by group_id, Upload JSON / Paste transcript, Evaluate selected / all (dry run → estimate, Run → job), live job progress (SSE with polling fallback), CSV export link |
| `#/conversations/:id` | Card: transcript (speaker styles, `[idx]` + timestamp, segment separators, duplicates collapsed, search, PII marker) and for calls an audio player (`/api/audio/{id}`, speed, seek, pause/hold markers, acoustic chips). Right pane tabs Result · History · Trace · Audio insights. Buttons Re-evaluate, Export PDF, Copy JSON. Evidence chips scroll/highlight the turn (and seek audio); `checked_span` highlights a range. Appeal per criterion (analyst role) |
| `#/checklists` | Checklists with versions, Activate, Diff dialog, import a new checklist from JSON |
| `#/checklists/:id/versions/:vid` | Version editor: inline criteria editing (code, section, title, instruction, answer type, options with points/anchors, weight, N/A + condition, gross-error flag, voice-call override), add/remove/reorder, Advanced drawer with `judge_spec` + Regenerate, scoring rules panel, Save as new version (change note, compile_specs), Activate, Diff, Apply to dataset (dry run then run with progress), Import/Export JSON |
| `#/review` | Review queue: open appeals first with a decision panel (confirm / change with answer picker + N/A when allowed, reason, add-to-few-shot switch, live total preview via `POST /api/appeals/preview`), decided appeals below |
| `#/analytics` | KPI tiles, total distribution chart, criteria × language heatmap, operators, worst dialogs, AI-vs-reviewer agreement, providers (cost/latency), first-response tile, CSV export; filter by checklist version |
| `#/settings` | Judge provider/model/effort, explanation language, PII masking, few-shot switch, local endpoint/model, health panel (`/api/health`), cost rates |

Global: collapsible side nav, top bar with role toggle (QA analyst / Reviewer; it only changes which actions are shown: Appeal/Evaluate for the analyst, decision panel for the reviewer), provider badge from `/api/settings` + online indicator from `/api/health`, language switcher RU/EN/KK, theme toggle (dark default, `localStorage` `cqa.theme`). Mascot poses (`web/public/mascot`, bundled from `src/assets/mascot`) are used once per screen via `<Mascot pose size />`.

## Environment and runtime config

| Variable | Meaning |
|---|---|
| `VITE_API_URL` | API base at build time. Empty string = same origin (dev proxy / nginx proxy). docker-compose passes `""`. |
| `VITE_MOCK=1` | Serve every screen from fixtures in `src/mock/fixtures/*.json` (captured from the real backend) through an in-memory mock of the API (`src/mock/server.ts`). Mutations (appeals, decisions, new versions, settings, jobs) are kept in memory for the page lifetime; jobs advance on every poll. `?mock=1` in the URL does the same at runtime. |

Runtime override of the API base, highest priority first:

1. `?api=https://host` query parameter (before the `#`, or inside the hash route query);
2. `window.CQA_CONFIG = { apiUrl: "https://host" }` set by a host page (Dynamics 365 web resource);
3. `VITE_API_URL` from the build.

The `.env.example` in `web/` lists the variables.

## Embed mode

`index.html?embed=1&conversation=<id>` (or `window.CQA_CONFIG = { embed: true, conversation: "<id>" }`) renders only the conversation card without nav, top bar or router — the view a Dynamics 365 Conversation form tab would show. Combine with `?api=` when the resource is served from a different origin than the API. Appeals are hidden in embed mode (read-only card with History/Trace/Audio tabs).

## Jobs and live progress

`POST /api/evaluations/run` returns a `job_id`. The UI opens `EventSource(/api/jobs/{id}/events)` and listens to `job.progress`, `evaluation.started`, `evaluation.done`, `job.status`, `job.done`. If the stream errors before any message (no SSE, proxy buffering) or in mock mode it polls `GET /api/jobs/{id}` every second; when the server closes the stream it fetches the job once more to get the final status and log. Running jobs are also picked up from `GET /api/jobs` after a page reload (job ids are kept in `localStorage` `cqa.jobs`). After a job ends the conversations list, evaluations and analytics queries are invalidated.

## Docker

`web/Dockerfile` is multi-stage: `node:22-alpine` runs `pnpm build` (build arg `VITE_API_URL`, default `""`), then `nginx:1.27-alpine` serves `dist/` with `try_files … /index.html` and proxies `/api/` to `http://api:8000/api/` (the service name from the root `docker-compose.yml`; buffering off so SSE and audio ranges pass through). `web/.dockerignore` excludes `node_modules`, `dist*` and `.env`. The Docker daemon was not running on the build machine, so the image itself has not been built here; the nginx config and stages follow the compose contract.

## Contract notes (observed differences from `docs/API.md`)

Everything in the UI was verified against the running backend (SQLite, 65 DEV conversations). Differences the UI accounts for:

- `ConversationSummary.latest_evaluation.model` carries a provider prefix (`anthropic:claude-opus-5-5`); the UI strips it for display. Summaries also carry `terminal_event`, `imported_at`, `dataset_id`; details carry `meta` (`operator_start_idx`, `escalation_idx`, `duplicates`, `speakers`).
- `EvaluationCriterion.answer` for scale criteria arrives as a string (`"20"`), not a number; the UI treats numeric strings as scale values. Criteria also carry `answer_type`, `options`, `claims[]` and `appeals[]` (full thread) in addition to `appeal`.
- An evaluation whose criterion call failed has `status: "failed"`, `error` text and the criterion with `answer: null`, flag `error`; the UI shows a status banner on the card. Partial re-evaluations copy unchanged criteria (`copied_from_previous_version` flag) and have no trace rows for them, so `GET /api/evaluations/{id}/trace` can be empty for a partial run.
- `POST /api/evaluations/run` additionally returns `rejudge_codes` and `checklist_version_id` (also for dry runs). `GET /api/jobs` (list, newest first) exists and is used to resume progress.
- SSE emits an extra `job.status {status, done, failed, total, cost_usd}` event and closes the stream after `job.done`; keepalive comments are sent while idle.
- `GET /api/checklists/{id}/versions/{a}/diff/{b}` also returns `from_version_id`, `to_version_id`, `scoring_rules_changed` and `rejudge_codes`; changed `fields` can include `judge_spec` (a regenerated spec alone marks the criterion `requires_rejudge`).
- `GET /api/appeals` items include `evaluation_id`, `answer_type`, `options`, `na_allowed`, `applicable`, `na_reason`, `max_points`, `weight`, `final_total_score`, `review_state`, `final_answer`, `language`, `channel`; the endpoint returns all statuses when `status` is omitted (the UI sorts open first).
- `POST /api/appeals/preview` returns `sections[]` (plural) and `critical_error` in addition to `final_total_score`.
- `GET /api/analytics/summary` heatmap rows carry `title`, `channel` (`ALL` rows included), `value`, `n`; scale criteria are already normalised to 0..1, so the heatmap shows percentages for every criterion. Extra fields: `na_rates[]`, `critical_count`, `first_response {mean_seconds, over_threshold_share, threshold_seconds, n}`; `agreement` rows have `null` rates when `n_reviewed = 0`.
- `GET /api/settings` exposes `secondary_model`, `local_model`, `judge_effort`, `max_parallel_criteria`, `fewshot_count`, `anthropic_configured`; `PUT` accepts the editable subset. `GET /api/health` adds `organizer_api`.
- `GET /api/audio/{id}/features` adds `summary_ru`, `long_pauses_count`, `hold_segments`, `repeated_operator_lines`, `speech_rate_label`, `operator_talk_s`, `client_talk_s`; pauses have `duration`.
- `POST /api/conversations/paste` body is `{ text, channel, language, conversation_id? }` (also accepts `transcript[]`); the API returns 404 `{error:{code:"not_found", message:"no evaluation yet"}}` for a conversation without evaluation, which the card treats as an empty state.
- No endpoint deletes conversations or appeals; the UI does not offer deletion.

## Layout of `web/src`

```
api/        types.ts (contract types), client.ts (fetch + ApiError), endpoints.ts, queries.ts (TanStack hooks)
mock/       server.ts (in-memory API) + fixtures/
i18n/       ru.ts (source dictionary, typed), en.ts, kk.ts
components/ ui/* (button, input, select, dialog, tabs, table, switch, checkbox, badge, tooltip, progress…), common.tsx (ScoreRing, ScorePill, EmptyState, JsonView…), layout/AppShell.tsx, Mascot.tsx, ErrorBoundary.tsx
features/   conversations/, card/ (transcript, audio player, evaluation panel, tabs, appeal), checklists/ (list, editor, diff), review/, analytics/, settings/, jobs/ (useJobProgress, JobProgress)
lib/        config.ts (API base, embed, mock flags), format.ts, toast.ts, utils.ts, useThemeColors.ts
```

Design tokens live in `src/index.css` (`--bg`, `--surface`, `--line`, `--text`, `--accent`, `--green-50..900`, `--accent-hover`, `--accent-subtle`, `--selection`, semantic colours) and are mapped in `tailwind.config.ts`.
