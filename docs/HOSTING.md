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
~/.npm-global/bin/railway variables --set ANTHROPIC_API_KEY=... --set ORGANIZER_API_URL='...' --set DATABASE_URL=sqlite:////app/data/cqa.db --set CORS_ORIGINS='["*"]'
```
```bash
~/.npm-global/bin/railway up
```
```bash
~/.npm-global/bin/railway domain
```

`api/railway.json` already sets the Dockerfile build, the start command with `$PORT`, and `/api/health` as the health check. After the first deploy, open the UI, press Import with `DEV`, and run Evaluate for all so the hosted instance is populated.

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
