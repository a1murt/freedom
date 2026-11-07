# Handoff: ML teammate

Read this first, then [ML_BRIEF.md](ML_BRIEF.md) for depth. You own the judge's accuracy. Everything else (UI, API, scoring math, hosting) is done and stays stable.

## Why this matters

The organizers pick the top 50 mainly by how close our scores are to their hidden ground truth on 10 unseen dialogs (opened at H+23). They also reward a local-model option. Those two things are yours.

## Setup (10 minutes)

```bash
git clone https://github.com/k4ssymzhomart/freedom && cd freedom/api
```
```bash
uv sync --extra dev
```
Ask the team lead for the `.env` file (Claude key and organizer URL) and put it in the repo root. Then:
```bash
uv run pytest -q
```
```bash
uv run uvicorn cqa.main:app --reload
```
Open `http://localhost:8000/docs`. Press `POST /api/datasets/import` with `{"group_id": "DEV"}` to load the 65 dialogs. The hosted API runs the same code at https://api-production-58ab4.up.railway.app (Swagger at `/docs`).

## Where things live

| What | File |
|---|---|
| The organizer checklist (truth, do not edit casually) | `api/seeds/checklist_freedom_v1.json` |
| Decision procedures per criterion (your main lever) | `api/seeds/judge_specs_v1.json` |
| Prompts | `api/cqa/judge/prompts/v1/system.md`, `criterion.md` |
| Prompt assembly | `api/cqa/judge/packer.py` |
| Verdict schema | `api/cqa/judge/contract.py` |
| Validation and hard N/A rules | `api/cqa/judge/validate.py` |
| Voting and arbiter | `api/cqa/judge/ensemble.py` |
| Providers (Claude, local OpenAI-compatible) | `api/cqa/judge/providers/` |
| Reference facts for C4 | `api/seeds/claims_ledger.json`, `docs/knowledge/freedom_products.md` |
| Harness | `api/cqa/eval/harness.py` |
| Dataset facts and expected labels | `docs/DATASET_NOTES.md` |

Changing `judge_specs_v1.json` does not reach an existing database by itself. After editing, re-sync version 1 (snippet in `docs/ML_BRIEF.md`) or start from a fresh `api/data/cqa.db`.

## Tasks, in priority order

1. **Gold set (start now).** Copy `api/eval/gold.template.json` to `api/eval/gold.json`. Label the 23 listed dialogs from the organizer PDF literally, one answer per criterion. The team lead labels the same set separately; reconcile disagreements together and write the reasons into `api/eval/decisions.md`.
2. **Baseline.** Score the stored DEV verdicts against your labels:
   ```bash
   uv run python -m cqa.eval.harness --gold eval/gold.json --split train
   ```
3. **Improve.** One change per round in the specs or prompts, re-run with `--run`, keep the change only if the validation split improves. Known weak spots: C4 15 vs 20, C2 N/A vs Нет, C7 literal reading. Measured run-to-run agreement is in `ML_BRIEF.md`.
4. **Ensemble decision.** Compare single Opus with `--provider ensemble` (Opus, Opus, Sonnet plus arbiter) on validation. Cost is about 2.5×.
5. **Local model (diversity points).** Any OpenAI-compatible endpoint works: vLLM, Ollama, LM Studio, or a rented GPU. Set it in the app's Settings page (`local_endpoint`, `local_model`) or in `.env` as `LOCAL_ENDPOINT` and `LOCAL_MODEL`, then run the harness with `--provider local`. If no GPU is available, Ollama with `qwen3:8b` on a laptop is enough to show the option works.
6. **Freeze by H+22.** Write the winning provider, model, prompt version and effort into `api/judge_config_control.md` and tell the team lead.

## Ideas, yours to accept or reject

- Two-pass judging for C2, C3, C4: extract facts first (client questions, which got answers, what data was disclosed), then score from the facts.
- Few-shots from the reconciled gold set, matched by language and channel, three per call at most, never from the validation split.
- Code rules for the clear cases (C7 N/A on connection loss is already a rule) so the model only decides the borderline ones.
- For calls, use the acoustic summary already passed in the structural facts (pace, long pauses, holds) inside the C6 decision steps.

## Ground rules

- Do not change scoring math or the checklist seed; if the PDF reading looks wrong, raise it.
- Commit to a branch named `ml/<topic>` and open a PR; the team lead merges.
- Never commit `.env` or anything under `api/data/`.
- Every evaluation costs about $0.20 per dialog on Opus. A full DEV run is about $14; the gold set run is about $5.
