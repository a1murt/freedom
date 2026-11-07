# Judge configuration for the control run

Frozen by the ML owner on 2026-11-07. Evidence: the 65-dialog gold set in `api/eval/` (how it was built: `api/eval/decisions.md`) and the harness runs in `api/eval/runs.csv`.

## What runs on the control set

| Setting | Value | Why |
|---|---|---|
| Provider | `ensemble`: Opus 5.5, Opus 5.5, Sonnet 5.5; the Opus arbiter decides when the votes split | Same accuracy as single Opus on validation (0.966 vs 0.962 on 29 dialogs, within run-to-run noise), and three votes cut run-to-run variance on a one-shot set of 10 chats. About $0.58 per chat, so about $6 for the control set. |
| Fallback | `anthropic` (single Opus 5.5) | 0.965 on all 65 DEV dialogs, 0.973 on the 60 chats. Use it if the ensemble is rate-limited; local is the last resort. |
| Prompts | `v1` (`api/cqa/judge/prompts/v1`, unchanged) | |
| Judge specs | `api/seeds/judge_specs_v1.json` at this commit ("specs v2.2") | The startup seed sync writes them into the organizer checklist (version 1) on deploy. |
| Effort | `high` | |
| C7, bare farewell | `c7_bare_farewell_policy = na` (the code default) | The organizers answered "N/a" to exactly this case (p1-ru-001). 33 of 60 DEV chats end with a farewell and no summary or offer of help. The judge flags the case as `bare_farewell`; the setting decides the value. |
| PII masking | on | Unchanged. The local provider keeps data on the machine and is not masked. |
| Few-shots | none seeded (only reviewer decisions, if any) | Not needed at this accuracy, and DEV few-shots would leak into the DEV metrics. |
| Local model | qwen3:8b through Ollama, outside the control vote | The option works end to end but is much less accurate (see below). |

## Accuracy against the gold set (C7 policy N/A)

| Criterion | all 65 · before (specs v1) | all 65 · now (specs v2.2) | holdout 24 · before | holdout 24 · now |
|---|---|---|---|---|
| C1 Установление контакта | 0.98 | 0.98 | 1.00 | 1.00 |
| C2 Выявление потребности | 0.85 | 0.94 | 0.88 | 0.96 |
| C3 Идентификация | 0.95 | 0.97 | 1.00 | 1.00 |
| C4 Консультация | 0.88 | 0.91 | 0.92 | 0.92 |
| C5 Эмпатия | 0.91 | 1.00 | 1.00 | 1.00 |
| C6 Управление диалогом | 0.80 | 0.92 | 1.00 | 0.96 |
| C7 Завершение | 0.46 | 1.00 | 0.08 | 1.00 |
| C8 Грубые ошибки | 1.00 | 1.00 | 1.00 | 1.00 |
| **Mean per-criterion agreement** | **0.854** | **0.965** | **0.859** | **0.979** |

Single Opus 5.5 in both columns. Under the old C7 reading (farewell = Да, scored on both sides) the same comparison is 0.919 → 0.965 on all 65 dialogs, so the gain is not only the C7 policy. The 24 holdout dialogs were not used to write specs v2 and v2.1; three holdout C4 errors informed the v2.2 C4 wording.

| Provider on validation (29 dialogs) | Mean agreement | Errors / labels | Cost per dialog |
|---|---|---|---|
| ensemble (Opus, Opus, Sonnet + arbiter) | 0.966 | 8 / 232 | $0.58 |
| single Opus 5.5 | 0.962 | 9 / 232 | $0.24 |
| local qwen3:8b (Ollama, MacBook Pro M4, 16 GB) | see `runs.csv` (8 validation dialogs) | | $0, about 15–20 min per dialog |

The local model's first run (specs v1, before the provider fixes) reached 0.609 on the 8 validation dialogs, with several criteria lost to timeouts and malformed JSON; the run with specs v2.2 and the fixed provider is in `runs.csv`.

## Apply it on the hosted instance

1. Merge the PR and let Railway redeploy. On start-up the seed sync logs `organizer checklist v1 synced from seeds: C2.judge_spec, …`.
2. `GET /api/settings` must show `"c7_bare_farewell_policy": "na"`. A stored setting wins over the code default; to set it: `PUT /api/settings {"c7_bare_farewell_policy": "na"}`.
3. `local_endpoint` must be empty on the hosted instance: when it is set, the local model joins every ensemble vote.
4. Rehearse (10 DEV chats, no git, about $6): `api/.venv/bin/python scripts/control_run.py --rehearse --provider ensemble`
5. Control hour: `api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> --provider ensemble`

## If the organizers clarify C7 after the run

`PUT /api/settings {"c7_bare_farewell_policy": "yes"}` (or `"no"`), re-judge only C7 for the control dialogs (partial re-evaluation, about $0.03 per dialog with a single judge), export again. Scoring the alternatives offline: `uv run python -m cqa.eval.harness --gold eval/gold.json --c7-bare yes`.

## Run the local model

```bash
brew install ollama && brew services start ollama
```
```bash
ollama pull qwen3:8b && ollama create qwen3-cqa -f api/local/Modelfile.qwen3-cqa
```
```bash
cd api && LOCAL_ENDPOINT=http://localhost:11434/v1 LOCAL_MODEL=qwen3-cqa uv run python -m cqa.eval.harness --gold eval/gold.json --split validation --run --provider local --model qwen3-cqa --concurrency 1
```

`local_max_parallel` (default 1) caps concurrent requests to the endpoint; raise it for vLLM on a GPU. `local_timeout_s` (default 900) is the per-request timeout.
