# Judge configuration for the control run

Frozen by the ML owner on top of `main` at 876c1de. Evidence: two independent AI-panel gold sets (`api/eval/gold.json`, 65 dialogs; `api/eval/gold_ai_panel.json`, 60 chats), the decision log `api/eval/decisions.md`, the harness runs in `api/eval/runs.csv` and `docs/eval/ACCURACY.md`.

> **Update 2026-11-08, about 04:40 +05 (architecture side).**
> - **Specs:** `main` now also carries three narrow spec fixes (commit `ce25bbe`: C3 guesses, C4 caveats nobody asked for, C6 vs C8 offers). They were measured on the 60 DEV chats against both gold sets: +6 cells on `gold.json`, +5 on `gold_ai_panel.json`, 0 broken. See `docs/HANDOFF_ML.md` «Round 7».
> - **C7 policy:** the hardening is in commit `1141be2`. The policy now runs on every ensemble member before the vote.
> - **Unchanged:** the provider, effort, C7 policy and the commands below.

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
| Local model | qwen3.8:27b (Q4_K_M) through Ollama on the university GPU server (2×A10, closed network), outside the control vote | On par with single Claude on the 8 validation dialogs, but 6–17 min per dialog: an on-prem option and a demo, not the control run. |

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
| local qwen3.8:27b Q4_K_M (Ollama, university server, 2×A10; 8 validation dialogs) | 0.859 (lenient 0.906) | 9 / 64 | $0, 6–17 min per dialog, one request at a time |
| single Opus 5.5, deployed specs, the same 8 dialogs | 0.844 (lenient 0.859) | 10 / 64 | $0.26 |
| local qwen3:8b (Ollama, MacBook Pro M4, 16 GB; 8 validation dialogs) | 0.609 | 25 / 64 | $0, 15–20 min per dialog |

The ensemble comparison ran before the spec merge; the vote and the arbiter do not depend on the spec text. The 8 validation dialogs hold the hardest disputed cases, hence the lower numbers for every judge; with 8 dialogs the 27B model is "on par" with Claude, not better. The 8B model fails on C2, C4 and C7 (0.12, 0.25, 0.25). Sending the eight criteria in parallel did not speed up the server (369 s vs 371 s for one dialog): its Ollama serves one request at a time.

## Apply it on the hosted instance

1. Merge the PR and let Railway redeploy. The start-up seed sync logs `organizer checklist v1 synced from seeds: C7.judge_spec`.
2. `GET /api/settings` shows `"c7_bare_farewell_policy": "na"`. A stored setting wins over the code default; to set it: `PUT /api/settings {"c7_bare_farewell_policy": "na"}`.
3. `local_endpoint` must be empty on the hosted instance: when it is set, the local model joins every ensemble vote.
4. Rehearse (10 DEV chats, no git, about $6): `api/.venv/bin/python scripts/control_run.py --rehearse --provider ensemble`
5. Control hour: `api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> --provider ensemble`

## Local model (open weights, university GPU server): current numbers

Own Ollama 0.40 on the university GPU server (2 × A10, user space, 3 slots, 12k context, thinking off), `qwen3.8:27b-q4_K_M`.
Setup, the public endpoint for the hosted API and the operations notes: `api/local/GPU_SERVER.md`.

| Judge, same 24 holdout chats (gold `holdout_chats`, C7 policy N/A) | Mean agreement | Misses / 192 | Time | Cost |
|---|---|---|---|---|
| Claude Opus 5.5, deployed specs | 0.984 | 3 | ~20 s per chat | $0.22 per chat |
| qwen3.8:27b, deployed specs, no few-shots | 0.969 | 6 | 34 s per criterion, ~5.5 min per chat with 3 chats in flight (24 chats in 49 min) | $0 |
| qwen3.8:27b + three gold few-shots per criterion (from the other split) | 0.963 | 7 | 65 min for 24 chats (longer prompts) | $0 |

The six qwen misses are the soft cells of the gold itself: C4 15/20 on the D-account twins (p1-kz-011, p1-ru-024) and on an
unanswered follow-up (p1-en-008), C4 10/20 on p1-kz-012, one C2 (p1-ru-030) and one C6 (p1-en-008). The same cells cost Claude
its three misses. With run-to-run noise of about 3 cells per 192, the two judges are at parity on this split.

Few-shots from the gold set were measured and **not adopted**: the same six misses plus one (C7 on p1-kz-002 went to Да, because the
Kazakh C7 examples of the training split contained no N/A case), and the longer prompts cost a third more time. The local judge
runs with the deployed specs alone (`--fewshots none`, the default of `control_local.py`). Thinking is off
(`LOCAL_REASONING_EFFORT=none`): on the three dialogs both modes finished, both scored 100%, and thinking doubled the time
(64 s vs 34 s per criterion).

Control hour on the local model: 10 chats take 15–20 min end to end (`scripts/control_local.py`, 3 chats in flight); the hosted
API reaches the same server through `LOCAL_ENDPOINT` (see `GPU_SERVER.md`), so `--provider local` works on Railway too, and
with `LOCAL_ENDPOINT` set the ensemble degrades to the local vote when the Anthropic key has no credit.

## On-prem run of the control set (optional, next to the official run)

The official file stays the ensemble on the hosted API. In parallel, the ML teammate judges the same control chats with the open model on the university GPU server, to show the on-prem architecture on new data (selection criterion: diversity of approaches, local LLM vs cloud). Nothing here touches the hosted instance, git or `results/prod/results.json`.

- **Where:** a laptop inside the university network (the GPU server is in a closed network) with this repository, `api/.venv` and `.env` (`ORGANIZER_API_URL` for `--group`).
- **Before 13:00, warm up and check (≈ 5 min, 2 short DEV chats):** `api/.venv/bin/python scripts/control_local.py --rehearse 2 --tunnel <ssh host of the GPU server>`. The script talks to the CQA Ollama on the GPU server (port 11436, 3 slots, thinking off; `api/local/GPU_SERVER.md`). Rehearsed 2026-11-08 on the shared single-slot Ollama: 2 chats in 12.3 min, valid file (`mode: local`), 100% agreement with the cloud verdicts; on the own server 24 chats took 49 min with 3 in flight; the control set itself ran at about 2–2.5 min per chat (8 criteria, 3 at a time).
- **At 13:00:** `caffeinate -i api/.venv/bin/python scripts/control_local.py --group <CONTROL_GROUP_ID> --tunnel <ssh host>` (or `--source-url` / `--source-file`, the same inputs as `control_run.py`; `caffeinate` keeps the laptop awake). About 2 min per chat with 3 chats in flight on the own server (34 s per criterion): 15–20 min for 10 chats, shortest chats first.
- **If it stops** (network, Ctrl-C, laptop moved): the file is exported after every dialog, so `api/data/control_local/results_control.json` is always current. Run the same command again to continue: the control set is cached, finished dialogs are skipped, a dialog with a failed criterion re-judges only that criterion, and a dropped tunnel is reopened (it waits up to 10 min for the network). `--export-only` rebuilds the file without judging. Only one run per database at a time (a lock file enforces it).
- **When the official run is pushed:** `git pull`, then `api/.venv/bin/python scripts/control_local.py --compare-only` writes `api/data/control_local/agreement.md`: per-criterion agreement between the local model and the cloud ensemble on the new chats, and the mean difference of totals. That is the slide line.
- Outputs stay in `api/data/control_local/` (git-ignored). Do not commit them as results.

## If the organizers clarify C7 after the run

`PUT /api/settings {"c7_bare_farewell_policy": "yes"}` (or `"no"`), re-judge only C7 for the control dialogs (partial re-evaluation, about $0.03 per dialog with a single judge) and export again. Re-judge C7 explicitly (`only_codes: ["C7"]`). Other partial runs do re-judge bare-farewell C7 rows decided under the old policy, but two cases are missed:
- the cost estimate does not count those extra calls;
- an ensemble row whose most confident member did not flag the bare farewell keeps no flag, so it is copied as it was. Scoring the alternatives offline: `cd api && uv run python -m cqa.eval.harness --gold eval/gold.json --c7-bare yes`.

## Measure it yourself

```bash
cd api && uv run python -m cqa.eval.harness --gold eval/gold_ai_panel.json --api https://api-production-58ab4.up.railway.app
```
```bash
cd api && uv run python -m cqa.eval.harness --gold eval/gold.json --split holdout --run --provider anthropic
```

## Run the local model

On the university GPU server (Ollama already serves `qwen3.8:27b-q4_K_M` on its localhost; the server is in a closed network, so reach it through an SSH tunnel and run the API or the harness on a laptop inside that network):

```bash
ssh -f -N -L 11435:127.0.0.1:11434 <user>@<gpu-server>
```
```bash
cd api && LOCAL_ENDPOINT=http://localhost:11435/v1 LOCAL_MODEL=qwen3.8:27b-q4_K_M uv run python -m cqa.eval.harness --gold eval/gold.json --split validation --run --provider local --model qwen3.8:27b-q4_K_M --concurrency 1
```

The hosted Railway instance cannot reach the closed network; do not expose Ollama publicly (it has no authentication), and keep `local_endpoint` empty on the hosted instance during the control run (a set endpoint joins every ensemble vote).

On a laptop:

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
