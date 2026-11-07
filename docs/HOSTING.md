# Hosting

Two targets: a hosted instance for the jury (they should not need to run anything) and `docker compose up` for reproducibility from the tagged commit.

## API on Railway (persistent process, SQLite on a volume)

The Railway CLI is installed at `~/.npm-global/bin/railway` (add that folder to PATH or call it by full path).

```bash
~/.npm-global/bin/railway login
```
```bash
cd api && ~/.npm-global/bin/railway init
```
```bash
~/.npm-global/bin/railway volume add --mount-path /app/data
```
```bash
~/.npm-global/bin/railway variables --set ANTHROPIC_API_KEY=... --set ORGANIZER_API_URL='...' --set DATABASE_URL=sqlite:////app/data/cqa.db --set CORS_ORIGINS='["*"]' --set CQA_ADMIN_TOKEN=$(openssl rand -hex 16)
```
```bash
~/.npm-global/bin/railway up
```
```bash
~/.npm-global/bin/railway domain
```

`api/railway.json` already sets the Dockerfile build, the start command with `$PORT`, and `/api/health` as the health check. After the first deploy, open the UI, press Import with `DEV`, and run Evaluate for all so the hosted instance is populated.

### Before and after each deploy (architecture phase)

- Version: set it before uploading, so `GET /api/version` names the build (CLI uploads carry no git SHA):
  ```bash
  ~/.npm-global/bin/railway variables --set APP_VERSION=$(git rev-parse --short HEAD)
  ```
- Budget: `GET /api/system/health` → `budget.remaining`. The default `DAILY_BUDGET_USD` is 75 per rolling 24 h; a full DEV run is about $13, the control set about $2–4. Raise it (or set `0`) before the control hour if needed; `MAX_CONCURRENT_JOBS` (2) and `MAX_DIALOGS_PER_RUN` (200) are the other guards.
- Schema: the first start of a new build adds missing columns and indexes by itself (`init_db`), e.g. `conversations.agent_name`.
- Snapshot: once the hosted instance is populated, `POST /api/admin/snapshots {"name": "baseline", "note": "демо-данные для жюри"}` with the header `X-CQA-Admin-Token: $CQA_ADMIN_TOKEN`; restore it after the jury's hands-on session (`POST /api/admin/snapshots/baseline/restore {"confirm": "baseline"}`, same header). On the hosted instance snapshot writes and downloads are refused without `CQA_ADMIN_TOKEN` (403 `admin_token_not_configured`; `ALLOW_UNPROTECTED_SNAPSHOTS=1` opts out). A restore does not reset the daily budget: spend is kept in `/app/data/spend_ledger.db`. `scripts/control_run.py` sends the token from `CQA_ADMIN_TOKEN` in the environment or the repo's `.env`; without it the pre-control snapshot is skipped with a warning; by hand (`curl -X POST -H "X-CQA-Admin-Token: …" -H 'content-type: application/json' -d '{"name":"pre-control"}' https://<api>/api/admin/snapshots`). Runbook: `docs/arch/admin.md`.
- Safety settings (all optional, `docs/arch/fixes.md` §4): `LOCAL_ENDPOINT_ALLOWLIST` (local-model URLs the settings page may select on the hosted instance), `MAX_TRANSCRIPT_CHARS` / `MAX_TRANSCRIPT_TURNS`, `MAX_CONCURRENT_TRIALS`, `SNAPSHOT_MAX_MANUAL`, `CQA_PUBLIC_INSTANCE=1` on any internet-facing host that is not Railway.
- Do not redeploy during the control run: a restart leaves an `interrupted` job that `scripts/control_run.py` resumes, but the spend of evaluations in flight is lost.
- Optional: `--no-access-log` in the start command (the `cqa.access` log line already has duration, role and request id).
- Restricted CORS: `CORS_ORIGINS` must list the web origin and, for the D365 web resource, `https://freedomholdingcorporation.crm11.dynamics.com`.
- Header-less requests: `CQA_HEADERLESS_ROLE` (default `demo`, every permission) is the role of a request without `X-CQA-Role`. Set it to `visitor` only after the deployed web app sends role headers: then direct API calls without headers can only read. `scripts/control_run.py` and `scripts/seed_demo.py` always send role headers.
- Start-up: besides the schema migration, the API re-derives the audio timing of stored calls (`cqa/ingest/renormalize.py`, idempotent) and recomputes their acoustic features from cached recordings.
- After each deploy: `api/.venv/bin/python scripts/smoke.py --version $(git rev-parse --short HEAD)` (read-only, 18 checks, prints the readiness summary).

Full environment list and deployment notes: `docs/ARCHITECTURE_V2.md` §11.

Alternative with a Railway token instead of an interactive login: create a token in the Railway dashboard (Account → Tokens) and export `RAILWAY_TOKEN=...` before the commands above.

## Web on Vercel (static bundle)

Logged in as `skassymzhomart-4086`. The web app calls the API through `VITE_API_URL` at build time (or `window.CQA_CONFIG.apiUrl` / `?api=` at runtime).

```bash
cd web && vercel --prod --yes -e VITE_API_URL=https://<railway-domain>
```

If the API domain changes, redeploy with the new value or set `VITE_API_URL` in the Vercel project settings.

## Fallback: tunnel from the laptop

If the hosted API is not up in time, run the API locally and expose it:

```bash
cloudflared tunnel --url http://localhost:8000
```

Copy the printed `https://....trycloudflare.com` URL into the web deploy as `VITE_API_URL`. The URL changes on every restart, so this is a demo fallback, not the submission target.

## Local

```bash
docker compose up --build
```

API on 8000, UI on 5173 (nginx proxies `/api` to the API container). Data lives in the `cqa-data` volume.
