# The CQA judge model on the university GPU server

Everything here runs as a normal user (no sudo) on the GPU server inside the university network: 2 × NVIDIA A10 (24 GB each),
shared with other people's research processes. Nothing of theirs is touched: own Ollama binary, own port, own model store.

## Layout (`~/cqa-ollama/`)

| Path | What |
|---|---|
| `bin/ollama` | Ollama 0.40.15 from the GitHub release tarball (`ollama-linux-amd64.tar.zst`), user space |
| `serve.sh` | starts the server: `OLLAMA_HOST=127.0.0.1:11436`, `OLLAMA_MODELS=~/cqa-ollama/models`, `OLLAMA_NUM_PARALLEL=3`, `OLLAMA_CONTEXT_LENGTH=12288`, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` |
| `models/` | `qwen3.8:27b-q4_K_M` (17 GB). Loaded it takes about 19.5 GB across the two GPUs (3 slots × 12k context, q8 KV) and loads in about 75 s |
| `token_proxy.py` | the Bearer-token gate (`api/local/token_proxy.py` in the repo), `127.0.0.1:11437 → 11436` |
| `gate.sh` | restarts the gate and a cloudflared quick tunnel in front of it; writes the public URL to `.tunnel_url` |
| `.token` | the Bearer token the gate expects (mode 600; the hosted API gets it as `LOCAL_API_KEY`) |
| `log/` | `serve.log`, `pull.log`, `proxy.log`, `cloudflared.log` |

Start or restart the model server: `setsid nohup ~/cqa-ollama/serve.sh > ~/cqa-ollama/log/serve.log 2>&1 < /dev/null &`.
Check: `curl -s 127.0.0.1:11436/api/ps` (the model stays resident) and `nvidia-smi`.

Why these settings: the judge prompt is 3–8k tokens (system prompt, transcript, criterion procedure, up to three few-shot
examples), so a 4k default context would silently truncate it; the model thinks before answering (`reasoning` in the reply,
`think: false` is ignored), which costs 20–60 s per criterion; three slots give about 2.5× the throughput of one on the
memory-bound decode. More slots need about 1.5 GB of KV cache each and the GPUs have 3–5 GB left, so 3 is the safe number
while other people's jobs run.

## Reaching it

- **From a laptop in the university network** (the harness, `scripts/control_local.py`, a local API for a demo):
  `ssh -f -N -L 11436:127.0.0.1:11436 <ssh host>` then `LOCAL_ENDPOINT=http://localhost:11436/v1 LOCAL_MODEL=qwen3.8:27b-q4_K_M LOCAL_MAX_PARALLEL=3`.
- **From the internet (the hosted API on Railway):** `http://89.218.178.215:11437/v1`, through the token gate only. Ollama has
  no authentication, so the model server itself is never exposed. The path: Railway → `bestsrv:11437` (public, ufw rule
  "CQA judge relay") → reverse SSH tunnel (`~/cqa-ollama/relay.sh` on the GPU server, a dedicated key authorized on bestsrv with
  `restrict,port-forwarding,permitlisten="11437"`, restarts itself on drops) → the gate on the GPU server (`127.0.0.1:11437`)
  → Ollama (`127.0.0.1:11436`). The gate accepts `GET /v1/models` and `POST /v1/chat/completions` with
  `Authorization: Bearer <token>` and keeps long replies alive with a whitespace heartbeat. Plain HTTP: the token travels
  unencrypted, so it is rotated after the hackathon (`~/cqa-ollama/.token`, then `restart_proxy.sh`).
  The hosted API needs `LOCAL_ENDPOINT=http://89.218.178.215:11437/v1`, `LOCAL_API_KEY=<token>`,
  `LOCAL_MODEL=qwen3.8:27b-q4_K_M`, `LOCAL_MAX_PARALLEL=3`, `LOCAL_TIMEOUT_S=600`, `LOCAL_REASONING_EFFORT=none`. With
  `LOCAL_ENDPOINT` set, the local model also joins every ensemble vote.
- **Thinking:** the model thinks before answering by default (`reasoning` in the reply; `think: false` is ignored on the
  OpenAI route), which took minutes per criterion and left no evaluation finished in 15 min. `reasoning_effort: "none"`
  switches it off (the provider sends it from `LOCAL_REASONING_EFFORT`, default `none`); the earlier measurement on the shared
  Ollama 0.32 ran without thinking as well.
- **Cloudflare quick tunnel** (`cloudflared tunnel --url`, quic and http2) registers a connection but the edge answers every
  request with an empty 404; parked in favour of the SSH relay.

## Measured

Gold `holdout_chats` (24 DEV chats never used for spec changes), C7 policy N/A, deployed specs:

| Judge | Mean per-criterion agreement | Misses / 192 | Time |
|---|---|---|---|
| Claude Opus 5.5 | 0.984 | 3 | ~20 s per chat |
| qwen3.8:27b, thinking off, no few-shots | 0.969 | 6 | 34 s per criterion; 24 chats in 49 min with 3 in flight |
| qwen3.8:27b + gold few-shots (3 per criterion, channel, language) | 0.963 | 7 | 24 chats in 65 min |

The qwen misses are the soft C4 cells (15/20 on the D-account twins and an unanswered follow-up, 10/20 on p1-kz-012), one C2 and one
C6; few-shots added a C7 miss where the Kazakh examples had no N/A case. Details and the control configuration:
`api/judge_config_control.md`; every run: `api/eval/runs.csv`.
