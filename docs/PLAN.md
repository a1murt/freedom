# CQA · 24-hour delivery plan

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Clock is relative to the hackathon start (H+0). Control transcripts open at H+23, stop-code at H+24.

## Team and operating model

| Role | Who | Owns |
|---|---|---|
| Product + full-stack + design + pitch | You | Frontend, UX, deck, demo, repo, hosting, organizer comms |
| ML / judge | Teammate | Gold set, prompts, judge providers, calibration harness, ensemble, audio features |
| Build engine | Claude (this session and parallel sessions) | Scaffolding, backend modules, frontend screens, tests, docs; runs under your review |

Rules: contracts first (verdict schema, checklist schema, API routes) frozen at H+2; one owner per package; the ML teammate never edits `web/`, the frontend never edits `api/cqa/judge/prompts/`; every merge keeps `docker compose up` working; commit at least hourly to the public repo.

## Phase clock

| Phase | Window | Goal | Exit check |
|---|---|---|---|
| P0 Prep | before H+0 | Spec, scaffold, design tokens, deck template, checklist seed from the PDF, gold-set protocol | `docker compose up` serves an empty app; DEV import works |
| P1 Spine | H+0 → H+4 | End-to-end on one dialog: import → judge → card with evidence → total | One RU chat fully evaluated in the UI with trace |
| P2 Core | H+4 → H+10 | All R1–R9: checklist studio, batch runs, appeals and decisions, audio proxy, exports | Full DEV batch evaluated; appeal flow demoable |
| P3 Accuracy | H+6 → H+18 (ML track, parallel) | Gold set, harness, prompt v2–v4, ensemble, few-shot memory | Validation kappa reported per criterion; config chosen |
| P4 Edge | H+10 → H+18 | Differentiators in §11 order: trace UI, provider switch, partial re-eval, audio insights, agreement dashboard, PII, claims ledger, embed mode | Each shipped feature has a demo step |
| P5 Freeze | H+18 → H+22 | Bug bash, README, hosting, deck, rehearsal ×2, repo link sent to organizers | Rehearsal under 5 minutes; cold-start test on a clean machine |
| P6 Control | H+22 → H+24 | Frozen config; at H+23 import the control group_id, run ensemble, verify, apply the replaced criterion, commit | Results ready for all 10; last commit before H+24 |

Cut lines: at H+8 anything not in P1/P2 is reconsidered; at H+18 nothing new starts; at H+22 only fixes.

## Epics and user stories

Story format: `ID · As a <role> I want <capability> so that <value>` with acceptance criteria. Owner tags: FE (you), BE (Claude under you), ML (teammate), PM (you).

### E1 · Data ingestion (BE)

- S1.1 As a QA analyst I want to import a dataset by `group_id` so that DEV and the control set load in one click. AC: backend calls the organizer URL, validates `isSuccess`, upserts conversations and turns, shows count and languages; errors are readable.
- S1.2 As a QA analyst I want to upload a JSON file in the organizer format, or paste a transcript as text, so that I can evaluate an arbitrary dialog. AC: parser accepts `MM:SS` and ISO timestamps, `speaker<TAB>text` lines, and the organizer JSON; new conversation appears in the list.
- S1.3 As the system I want normalized turns (index, offset seconds, duplicate flag, operator segment start, internal-consultation flag) so that the judge and UI share one view. AC: unit tests on three chats and two calls from DEV.

### E2 · Checklist engine (BE)

- S2.1 As a QA lead I want to create and edit criteria (section, title, instruction, type binary/scale/choice, scale anchors, weight, N/A allowed, critical flag, channel overrides) so that the checklist matches our policy. AC: JSON schema validated; saved as a new version; previous versions immutable.
- S2.2 As a QA lead I want weighted totals with N/A redistribution, section totals, rounding, and separate critical flag so that scores follow the organizer PDF. AC: golden tests for all cases in ARCHITECTURE §5.4; formula visible in the UI tooltip.
- S2.3 As a QA lead I want a diff between versions and "Apply to dataset" with full or partial mode and a cost preview so that changing one criterion is cheap and fast. AC: partial mode re-judges only changed criteria; weight-only edits need zero LLM calls.
- S2.4 As the system I want a `judge_spec` compiled per criterion (definition, decision steps, N/A conditions, evidence rule, channel notes) so that prompts are consistent. AC: generated on save, editable in Advanced drawer, stored with the version.

### E3 · Judge pipeline (ML + BE)

- S3.1 As the system I want a `JudgeProvider` interface with Anthropic (Opus 5.5, Sonnet 5.5) and Ollama implementations so that providers are swappable. AC: same verdict schema from all; provider and model recorded per trace.
- S3.2 As the system I want one structured call per criterion with transcript-first prompt layout and validated evidence so that verdicts are grounded. AC: evidence turn indices exist and quotes fuzzy-match; invalid verdicts are re-asked once; N/A requires a reason.
- S3.3 As the system I want ensemble voting with an arbiter on disagreement so that the control run is robust. AC: majority/median merge, votes stored, arbiter note shown.
- S3.4 As the system I want deterministic scoring after judging and one transaction per evaluation with traces so that totals are reproducible. AC: re-running the scoring engine on stored verdicts reproduces the total.
- S3.5 As a QA analyst I want batch evaluation with live progress, cancel, resume, and cost accounting so that 65 dialogs run unattended. AC: SSE progress; failed items retry; job log visible.
- S3.6 As the system I want PII masking before cloud calls (phones, IIN, cards, emails, IBAN) so that the compliance story holds. AC: toggle in Settings; placeholders in traces; originals in UI.

### E4 · Conversation card (FE)

- S4.1 As a QA analyst I want a list with filters, search, status, total, critical flag, cost, and bulk actions so that I can run and find evaluations. AC: filters by channel, language, status, score range; select-all; keyboard-free demo path.
- S4.2 As a QA analyst I want a card with transcript and per-criterion results where evidence chips jump to the turn or audio offset so that every verdict is checkable. AC: click highlights the turn; for calls the player seeks; N/A rows show reasons; missed-action rows highlight the checked span; section totals shown.
- S4.3 As a QA analyst I want a Trace tab with model, prompt version, tokens, cost, latency, raw request/response and votes so that the process is auditable. AC: present for every criterion; copy JSON button.
- S4.4 As a QA analyst I want an audio player with waveform and acoustic chips for calls so that call quality is reviewable. AC: audio streamed from the backend proxy; chips show pace, pauses, talk ratio.
- S4.5 As a QA analyst I want export of the card (PDF) and of all evaluations (CSV with per-criterion answers, points, totals) so that results can be compared with ground truth. AC: CSV columns documented in README.

### E5 · Appeals and review (FE + BE)

- S5.1 As a QA analyst I want to appeal a criterion with a comment so that disputed verdicts get a human look. AC: row shows "Under review"; appeal stored.
- S5.2 As a reviewer I want a queue showing the disputed verdict, comment, evidence excerpt, and transcript link, and a form to confirm or change with a reason, with the recalculated total previewed, so that decisions are informed. AC: decision immutable; final totals recomputed by the engine; card shows original, decision, final.
- S5.3 As the system I want accepted changes to become few-shot examples for the criterion so that the judge improves. AC: opt-in toggle; examples matched by language and channel; count shown in Analytics.

### E6 · Accuracy program (ML)

- S6.1 As the ML owner I want a gold set of 20 DEV dialogs labeled by two people and reconciled so that we have a ceiling and a target. AC: `eval/gold.json`; human-human agreement reported.
- S6.2 As the ML owner I want an eval harness reporting per-criterion exact match, MAE, weighted kappa, total MAE, and cost so that prompt changes are measured. AC: one command; results appended to `eval/runs.csv`.
- S6.3 As the ML owner I want prompt versions v1..vN with one change each and a train/validation split so that improvements are real. AC: best version chosen by validation kappa; frozen before H+22.
- S6.4 As the ML owner I want a claims ledger from DEV and optional reference-doc retrieval for criterion 4 so that correctness is checked against something. AC: ledger JSON reviewed; claims flagged as consistent/contradicted/unverified.
- S6.5 As the ML owner I want call-specific adaptations (operator segment after agent-join, internal consultation excluded, post-hangup lines marked late, ASR tolerance) so that call scores are fair. AC: documented in judge_spec channel notes; checked on the 5 calls.

### E7 · Analytics (FE + BE)

- S7.1 As a QA lead I want distributions, a criterion heatmap by language and channel, an operator leaderboard, and worst dialogs so that I see where the team fails. AC: aggregates from stored evaluations; updates after each job.
- S7.2 As a QA lead I want AI-vs-reviewer agreement per criterion and cost/latency per provider so that I can trust and budget the system. AC: kappa and agreement rate computed from decisions.

### E8 · Platform and delivery (PM + BE)

- S8.1 As a jury member I want `docker compose up` and a README with three commands so that I can run the fixed commit. AC: cold start on a clean machine under 5 minutes; `.env.example` complete.
- S8.2 As the team I want a hosted demo (web on Vercel, API on Railway or local with a tunnel) so that the demo does not depend on one laptop. AC: public URL; data pre-imported.
- S8.3 As a D365 integrator I want a single-file build, embed mode, and a Dataverse adapter stub so that the component can move into Dynamics. AC: `?embed=1&conversation=ID` renders the card only; adapter documented.
- S8.4 As the team I want the control-stage runbook executed at H+23 so that results are ready. AC: see runbook below.

### E9 · Pitch (PM)

- S9.1 Deck of 10 slides from the Figma template recolored to Freedom green: Title, Problem, Roles (analyst, reviewer, lead), Flow (import → judge → review → learn), Measured (gold-set agreement, cost per evaluation), AI modules (judge, ensemble, audio, claims), Analytics, Architecture and security (PII masking, local option, D365 path), Effect and adoption, Team.
- S9.2 Demo script in `docs/DEMO.md` rehearsed twice; fallback recording of the demo in case of network issues.

## Batches (parallel work packages)

| Batch | Window | Owner | Contents |
|---|---|---|---|
| B0 | P0 | PM + Claude | Repo scaffold (api, web, compose), schema contracts, seed checklist from PDF, design tokens, i18n skeleton, DEV import |
| B1 | P1 | Claude (BE) | Ingest, normalize, checklist CRUD, scoring engine with tests, Anthropic provider, single evaluation endpoint, trace storage |
| B2 | P1 | You (FE) | App shell, Conversations list, Card (transcript + results + evidence jumps), Trace tab |
| B3 | P1 | ML | Gold-set protocol and labeling start; prompt v1 and judge_spec compiler; harness skeleton |
| B4 | P2 | Claude (BE) | Batch jobs + SSE, audio proxy and features, appeals and decisions, exports, partial re-eval |
| B5 | P2 | You (FE) | Checklist Studio with diff and apply, Review Queue, audio player, Settings |
| B6 | P3 | ML | Gold set complete; harness; v2–v4; ensemble + arbiter; few-shot memory; claims ledger; call adaptations |
| B7 | P4 | Claude + You | Provider switch UI, analytics screens, PII masking, embed mode, Dataverse stub, PDF export |
| B8 | P5 | All | Bug bash, README, hosting, deck, rehearsals, organizer link |
| B9 | P6 | All | Control run, criterion swap, final commit |

## Control-stage runbook (H+22 → H+24)

1. H+22: freeze `DEFAULT_PROVIDER=ensemble`, prompt version, effort, few-shot bank; tag commit `freeze`.
2. H+23:00: paste the control `group_id` into Import; confirm 10 records, languages, channels.
3. H+23:02: Evaluate all with the active checklist in ensemble mode; watch SSE; retry failures; if Claude rate-limits, switch to Sonnet-only, then local.
4. H+23:20: spot-check 3 cards (one per language if present); export CSV; keep a copy outside the repo.
5. H+23:30: apply the organizer's criterion replacement in Checklist Studio, save v2, Apply partial, confirm new totals.
6. H+23:50: final commit and push; verify the repo link opens; stop coding.

## Definition of done for the demo

- One click from list to card; evidence jump works for a chat and a call.
- A N/A example and a critical-error example exist in DEV results.
- Appeal → decision → new total works without reload.
- Criterion edit → v2 → partial apply → changed totals visible in under 2 minutes for 10 dialogs.
- Trace tab shows a real timestamped call for the dialog on screen.
- Pasting a new dialog evaluates live in under 60 seconds.

## Inputs status (2026-11-07, hackathon running)

1. Organizer checklist: received, encoded in `api/seeds/checklist_freedom_v1.json`; expected DEV labels per criterion in DATASET_NOTES §7.
2. Clock: started 2026-11-07; control set at H+23, stop-code at H+24.
3. Claude API key: in `.env` (git-ignored). Opus 5.5 primary, Sonnet 5.5 secondary.
4. ML teammate: brief in `docs/ML_BRIEF.md`; university GPU cluster available for vLLM with an open model as the local provider; Ollama on the M4 as fallback.
5. Hosting: both. Hosted (Vercel web + Railway API) is what the jury opens; `docker compose up` reproduces it locally from the tagged commit.
6. Design: Figma kit recolored to Freedom green with the mascot kept; prompt in `docs/DESIGN_SYSTEM_PROMPT.md`.
