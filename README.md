# CQA · Conversation Quality Analytics for Freedom Broker

Prototype built for the Freedom Broker CQA hackathon: an LLM judge scores support dialogs (RU / KZ / EN chats and RU voice calls) against an editable, versioned checklist, shows per-criterion answers, points, weights, explanations and evidence turns, supports appeals and reviewer decisions with recalculated totals, and proves every verdict with a full trace.

Hosted demo: see the link in the hackathon submission. Local run below.

## Run it in three commands

```bash
cp .env.example .env    # add ANTHROPIC_API_KEY and the organizer URL
```
```bash
docker compose up --build
```
```bash
open http://localhost:5173
```

Then press **Import** on the Conversations screen with group id `DEV` (or the control group id), pick the checklist and press **Evaluate**.

Without Docker: `cd api && uv sync && uv run uvicorn cqa.main:app --reload` (API on 8000) and `cd web && pnpm install && pnpm dev` (UI on 5173).

## What it does

- **Import** by organizer `group_id`, JSON upload, or pasted transcript. Bot and system turns are kept as context; the operator segment, duplicates, recording pauses and terminal events are detected automatically.
- **Checklist Studio**: criteria with Да/Нет or scale answers, weights, N/A rules, gross-error flag, channel overrides for calls; every save is a new version; a diff tells which criteria need re-judging; **partial re-evaluation** re-judges only those.
- **Judge**: one structured call per criterion (transcript first, compiled decision procedure second), evidence validated against the transcript, hard N/A rules from the checklist, deterministic scoring with N/A weight redistribution and two-decimal rounding exactly as the organizer PDF specifies.
- **Providers**: Claude Opus 5.5 (default), Claude Sonnet 5.5, a local open model through any OpenAI-compatible endpoint (vLLM / Ollama), and an **ensemble** mode with cross-model voting and an arbiter.
- **Trace**: model, prompt version hash, request, response, tokens, cost and latency for every call.
- **Appeals**: analyst disputes a criterion, reviewer confirms or changes it with a reason, totals are recalculated, and accepted decisions become few-shot examples for the judge.
- **Calls**: audio is resolved from the organizer player page, cached and streamed with seeking; acoustic features (pace, pauses, talk ratio, holds) feed criterion 6.
- **Analytics and export**: distributions, criterion heatmap, operator leaderboard, AI-vs-reviewer agreement, cost per provider; CSV of all per-criterion answers and totals; PDF card.
- **Dynamics 365 ready**: the web app builds to a single HTML file, has an embed mode (`?embed=1&conversation=<id>`), uses hash routing and no auth.

## Model and cost

Default judge: `claude-opus-5-5` with adaptive thinking, effort `high`, structured outputs. Measured on DEV: about **$0.20 per chat** (8 calls, ~12k input and ~7.5k output tokens, 20 s wall time) and about $0.40 per voice call. Sonnet 5.5 is about half; the local model is free. Ensemble (Opus + Sonnet + arbiter) is about 2.5× a single Opus run. Actual cost per evaluation is computed from token usage and shown on every card.

## Repository

```
api/      FastAPI service: ingest, checklist versions, scoring engine, judge, jobs, audio, review, analytics, export
web/      Vite + React UI (RU/EN/KK), single-file build for Dynamics 365
docs/     ARCHITECTURE.md · PLAN.md · API.md · ML_BRIEF.md · DATASET_NOTES.md · knowledge/
api/seeds Organizer checklist (v1) and compiled judge specs, claims ledger
```

Tests: `cd api && uv run pytest` (scoring engine, normalization, audio, analytics, export).

## Team

Product, frontend, pitch: Kassymzhomart Shubay. ML judge and calibration: teammate. Built with Claude Code.
