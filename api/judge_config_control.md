# Judge configuration for the control run

Frozen by the ML owner on top of `main` at 876c1de. Evidence: two independent AI-panel gold sets (`api/eval/gold.json`, 65 dialogs; `api/eval/gold_ai_panel.json`, 60 chats), the decision log `api/eval/decisions.md`, the harness runs in `api/eval/runs.csv` and `docs/eval/ACCURACY.md`.

## What runs on the control set

| Setting | Value | Why |
|---|---|---|
| Provider | `ensemble`: Opus 5.5, Opus 5.5, Sonnet 5.5; the Opus arbiter decides when the votes split | Same accuracy as single Opus (0.966 vs 0.962 on 29 validation dialogs, within noise), and three votes reduce run-to-run variance on a one-shot set of 10 chats. About $0.58 per chat, about $6 for the control set. |
| Fallback | `anthropic` (single Opus 5.5) | Use it if the ensemble is rate-limited; `local` is the last resort. |
| Prompts | `v1` (`api/cqa/judge/prompts/v1`) | Unchanged. |
| Judge specs | `api/seeds/judge_specs_v1.json` at this commit: the deployed specs of 876c1de plus one C7 sentence that asks for the `bare_farewell` flag | Synced into the organizer checklist (version 1) on start-up. The ML specs v2.2 tied with them on independent data and were not merged (`decisions.md`). |
| Effort | `high` | |
| C7, polite farewell without a summary or offer of help | N/A: the spec answers N/A, and `c7_bare_farewell_policy = na` (code default) keeps it | The organizers answered "N/a" to exactly this case. The flag makes `yes`/`no` a Settings switch if they clarify otherwise. |
| C1 on calls | no N/A; an opening outside the recording is credited by the system rule | Organizer answer 2026-11-08 (upstream). |
| PII masking | on | The local provider keeps data on the machine and is not masked. |
| Few-shots | none seeded | |
| Local model | qwen3:8b through Ollama, outside the control vote | Works end to end; much less accurate (see below). |

## Accuracy of this configuration (single Opus 5.5, C7 policy N/A, fresh local run)

| Criterion | ML gold, 65 dialogs | Panel gold, 60 chats | ML holdout, 24 dialogs |
|---|---|---|---|
| C1 Установление контакта | 0.98 | 1.00 | 1.00 |
| C2 Выявление потребности | 0.92 | 0.93 | 1.00 |
| C3 Идентификация | 0.95 | 0.97 | 1.00 |
| C4 Консультация | 0.88 | 0.82 | 0.92 |
| C5 Эмпатия | 0.94 | 0.95 | 1.00 |
| C6 Управление диалогом | 0.85 | 0.88 | 0.96 |
| C7 Завершение | 0.97 | 0.95 | 1.00 |
| C8 Грубые ошибки | 1.00 | 1.00 | 1.00 |
| **Mean per-criterion agreement** | **0.936** | **0.938** | **0.984** |
| Same judge with the specs before 2026-11-07 evening | 0.854 | 0.844 | 0.859 |

The two gold sets agree with each other on 95.6% of the shared chat labels; C4 (87%) and C6 (88%) are soft in any gold set, so most of the remaining judge differences sit where the labelers themselves differ.

| Provider (validation, 29 dialogs, ML specs v2.2) | Mean agreement | Errors / labels | Cost per dialog |
|---|---|---|---|
| ensemble (Opus, Opus, Sonnet + arbiter) | 0.966 | 8 / 232 | $0.58 |
| single Opus 5.5 | 0.962 | 9 / 232 | $0.24 |
| local qwen3:8b (Ollama, MacBook Pro M4, 16 GB, 8 validation dialogs) | see `runs.csv` | | $0, 15–20 min per dialog |

The ensemble comparison ran before the spec merge; the vote and the arbiter do not depend on the spec text. The local model's first run (specs v1, before the provider fixes) reached 0.609; the rerun with the fixed provider is in `runs.csv`.

## Apply it on the hosted instance

1. Merge the PR and let Railway redeploy. The start-up seed sync logs `organizer checklist v1 synced from seeds: C7.judge_spec`.
2. `GET /api/settings` shows `"c7_bare_farewell_policy": "na"`. A stored setting wins over the code default; to set it: `PUT /api/settings {"c7_bare_farewell_policy": "na"}`.
3. `local_endpoint` must be empty on the hosted instance: when it is set, the local model joins every ensemble vote.
4. Rehearse (10 DEV chats, no git, about $6): `api/.venv/bin/python scripts/control_run.py --rehearse --provider ensemble`
5. Control hour: `api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> --provider ensemble`

## If the organizers clarify C7 after the run

`PUT /api/settings {"c7_bare_farewell_policy": "yes"}` (or `"no"`), re-judge only C7 for the control dialogs (partial re-evaluation, about $0.03 per dialog with a single judge) and export again. Scoring the alternatives offline: `cd api && uv run python -m cqa.eval.harness --gold eval/gold.json --c7-bare yes`.

## Measure it yourself

```bash
cd api && uv run python -m cqa.eval.harness --gold eval/gold_ai_panel.json --api https://api-production-58ab4.up.railway.app
```
```bash
cd api && uv run python -m cqa.eval.harness --gold eval/gold.json --split holdout --run --provider anthropic
```

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
