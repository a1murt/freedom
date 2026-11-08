# CQA — Conversation Quality Analytics for Freedom Broker

**Team: «Я Тебя Могну»** · Freedom Broker CQA hackathon, November 2026

CQA scores how a support operator handled a conversation against the company checklist. It reads a chat or a call transcript (RU / KZ / EN), answers each of the eight criteria, backs every answer with a quoted turn, computes the final score exactly by the organizers' rules, and lets anyone contest a criterion. Every verdict is reproducible: the model, prompt version, request, response, tokens and cost are stored with it.

| | |
|---|---|
| **Live demo (no login)** | https://cqa-freedom-pi.vercel.app |
| **API and Swagger** | https://api-production-58ab4.up.railway.app/docs |
| **Control-set results** | [`results/prod/results.json`](results/prod/results.json) (FBroker, 10 chats, organizer format JSON 1.0) |
| **DEV results** | [`results/dev/results.json`](results/dev/results.json) (65 dialogs) |
| **Judge accuracy** | [`docs/eval/ACCURACY.md`](docs/eval/ACCURACY.md), [`api/judge_config_control.md`](api/judge_config_control.md) |

## Run it in three commands

```bash
cp .env.example .env    # set ORGANIZER_API_URL (the organizers' GET link without group_id) and the judge: a model API key or a local model
```
```bash
docker compose up --build
```
```bash
open http://localhost:5173
```

The API listens on `http://localhost:8000` (Swagger at `/docs`), the web app on `5173`. In the app: **Import** → `group_id` (`DEV`, `FBroker`) → **Evaluate**. A minute later every dialog has a card with scores and justifications.

Without Docker:

```bash
cd api && uv sync --extra dev && uv run uvicorn cqa.main:app --reload
```
```bash
cd web && pnpm install && pnpm dev
```

No cloud key is needed: point `.env` at any OpenAI-compatible model server (`LOCAL_ENDPOINT=http://localhost:11434/v1`, `LOCAL_MODEL=qwen3:8b` for Ollama) and the judge runs fully on-prem. See "Cloud or on-prem" below.

## Five minutes with the jury

1. **A dialog and its card.** The list of 65 DEV dialogs with the final score, the gross-violation badge, the provider and the cost. Open `p1-ru-029`: the ring shows **51.25 / 100**, the banner says «Оскорбление клиента» with the quote «Вы вообще читать умеете?». Each criterion shows the answer, points, weight, a justification in the checklist's own style and a jump to the evidence turn.
2. **N/A.** In the same dialog «Идентификация» is N/A with the reason: a navigation question, no account data disclosed. Its weight is redistributed over the applicable criteria.
3. **Appeal.** The Operator role sees its own dialogs and contests a criterion with a comment; the Reviewer role sees the disputed score, the comment and the fragment, and confirms or changes it with a reason. The total is recomputed at once; the card keeps the original score, the decision and the final result.
4. **Checklist.** Checklist Studio: add a criterion, change its wording, weight, answer type or N/A rule; every save is a new version; the diff shows which criteria need re-judging, and **partial re-evaluation** re-judges only those.
5. **Gross violations.** A dedicated screen with every dialog where an insult, a paid service bypassing the company or a disclosure without identification was found: type, explanation and quote.

Timed script with fallbacks: [`docs/DEMO.md`](docs/DEMO.md).

## How the score is computed

The organizers' checklist is loaded as is ([`api/seeds/checklist_freedom_v1.json`](api/seeds/checklist_freedom_v1.json)): eight criteria, weights 10 / 15 / 20 / 20 / 15 / 15 / 5, scales 0–20 and 0–10, the N/A conditions from page 1, gross errors as a separate flag without points.

- **Judge.** One structured model call per criterion: first the transcript with its segments marked (before the operator, operator, internal consultation, after the end), then the criterion with a compiled decision procedure. The answer is validated: quotes are matched against the transcript, N/A is accepted only where the checklist allows it, a «Нет» needs an evidence turn or the span where the action was expected.
- **Score.** Deterministic, no model involved: «Да» gives the full weight, scale steps are converted proportionally, the weights of N/A criteria are redistributed over the applicable ones, the total is rounded half-up to two decimals (50.625 → 50.63). A gross error does not change the total and is flagged separately.
- **The organizers' answers** are built in as rules: a polite goodbye without a summary or an offer of help is N/A for «Завершение» (switch `c7_bare_farewell_policy` in Settings); data that sufficed from the start is N/A for «Выявление потребности»; a call whose greeting the recording missed gets no penalty on «Установление контакта».
- **Reference facts.** The operator's statements about menu paths, deadlines and fees are checked against the company fact base ([`api/seeds/claims_ledger.json`](api/seeds/claims_ledger.json), [`docs/knowledge/`](docs/knowledge/)); deviations are flagged, nothing is invented.

## Accuracy

Two independent gold sets on the 60 DEV chats, three labelers each, who read only the checklist and the organizers' answers and never saw our judge. The two gold sets agree with each other on 95.6 % of the cells.

| | Per-criterion agreement |
|---|---|
| Judge before calibration on the organizers' answers | 84.6 % |
| Judge now, against gold set 1 / gold set 2 | **95.0 % / 95.2 %** |
| Cloud ensemble (two models and an arbiter), control-run rehearsal | 98.8 % |
| Local model qwen3.8-27B vs the cloud model, 24 held-out chats | 96.9 % vs 98.4 % |

Mean absolute error of the final score against the gold totals: **2.9 points**, 43 of 60 totals exact. Two runs of the same judge differ on about 2 of 60 cells of the scale criteria, which is why the control set is judged by an ensemble. Method, decision log and every label: [`docs/eval/ACCURACY.md`](docs/eval/ACCURACY.md), [`api/eval/decisions.md`](api/eval/decisions.md), [`api/eval/gold.json`](api/eval/gold.json).

## The control set

`results/prod/results.json` holds the 10 chats of the **FBroker** set, judged on the frozen code and specs: a cloud ensemble of two models with an arbiter, checklist v1, 48 seconds. 10 records `ok`, two gross-violation flags. The **Swap** set is saved (the cached organizer response and an import into the hosted database); it will be evaluated on the organizers' additional instruction.

Validate the file, or rebuild it:

```bash
cd api && uv run python -m cqa.export.results_json --validate ../results/prod/results.json --expect 10
```
```bash
cd api && uv run python -m cqa.export.results_json --api https://api-production-58ab4.up.railway.app --group FBroker --out ../results/prod/results.json --expect 10
```

The control hour in one command (import, evaluate, retry failures, export, validate, commit, push): `api/.venv/bin/python scripts/control_run.py --group <GROUP_ID> --provider ensemble`. The same set on the local model, next to the official run and without any cloud call: `scripts/control_local.py`.

## Cloud or on-prem

The judge is model-agnostic; the provider is switched in Settings without a redeploy:

- **Local open model** through any OpenAI-compatible server (vLLM, Ollama). At the hackathon: qwen3.8-27B (Q4) on a university server with two NVIDIA A10, $0, about 2 minutes per chat with three requests in flight, accuracy on a par with the cloud model (table above). Data never leaves the perimeter. Setup and access: [`api/local/GPU_SERVER.md`](api/local/GPU_SERVER.md).
- **Cloud model** (the paid option the organizers ask us to name: Anthropic Claude Opus 5.5, Sonnet 5.5 as the second voice): about **$0.20 and 20 s per chat**, about $0.40 per call; the ensemble with an arbiter is 3–4× that ($0.80 per chat on the control set). PII is masked before anything is sent to the cloud; the cost of each evaluation is computed from the tokens and shown on the card. A switch in `.env` guarantees that no request leaves for the cloud.
- **Ensemble**: several models vote, an arbiter decides on disagreement; the local model can be one of the voices.

## Architecture

```
web/        React + Vite (RU / EN / KK), single-file build and embed mode for Dynamics 365
api/        FastAPI: import → normalization → judge → validation → scoring → appeals → analytics → export
api/seeds/  the organizers' checklist (v1), the judge's decision procedures, the fact base
api/eval/   gold labels (65 dialogs), labeling protocol, decision log, harness runs
docs/       architecture, API contract, demo script, hosting, accuracy reports
scripts/    control run (cloud and local model), smoke checks, demo data
results/    results.json for DEV and for the control set
```

Storage: SQLite (or Postgres via `DATABASE_URL`); every model call is kept in a trace table. Hosting: API on Railway, web app on Vercel; the same stack runs locally with `docker compose`. Roles (analyst, reviewer, operator, manager and others) with a permission matrix and an audit trail, a daily spend cap, database snapshots, a Dynamics 365 integration (`POST /api/integrations/dynamics/transcripts`). As built: [`docs/ARCHITECTURE_V2.md`](docs/ARCHITECTURE_V2.md); API contract: [`docs/API.md`](docs/API.md).

Tests: `cd api && uv run --extra dev pytest -q` — about 1,300 tests, offline (a deterministic fake provider replaces the model). CI: `.github/workflows/ci.yml`.

## Team «Я Тебя Могну»

- **Kassymzhomart Shubay** — product, architecture, frontend, hosting, presentation.
- **Alnur Kengesbay** — ML: judge calibration, gold labeling, the local model and the on-prem path.
