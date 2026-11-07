#!/usr/bin/env bash
# Free end-to-end rehearsal of the control run (import guard → evaluate → export → validate, no git) on a scratch copy
# of the database, with the offline "fake" judge by default. It never touches the hosted jury instance; with the
# default judge it never spends money.
#
#   scripts/rehearse_local.sh                 # newest backup in api/data/offsite/*.db (download one first, see below)
#   scripts/rehearse_local.sh path/to/cqa.db  # any SQLite copy of the API database
#   PROVIDER=anthropic scripts/rehearse_local.sh   # the real Claude judge on the scratch copy (about $2–4 for 10 chats;
#                                                  # uses ANTHROPIC_API_KEY from .env; still never touches the hosted API)
#
# Get a fresh copy of the hosted data (read-only for the hosted instance; needs CQA_ADMIN_TOKEN from .env):
#   curl -H "X-CQA-Admin-Token: $CQA_ADMIN_TOKEN" -o api/data/offsite/baseline.db \
#        https://api-production-58ab4.up.railway.app/api/admin/snapshots/baseline/download
# Output: api/data/rehearsal/results.json (git-ignored) and the control_run report with step timings.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8019}"
SRC="${1:-$(ls -t "$ROOT"/api/data/offsite/*.db 2>/dev/null | head -1 || true)}"
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
  echo "No database copy given and none in api/data/offsite/ (see the header of this script)." >&2
  exit 2
fi
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cqa-rehearsal.XXXXXX")"
cp "$SRC" "$WORK/cqa.db"
echo "Rehearsal on a copy of $SRC in $WORK (port $PORT)"

PROVIDER="${PROVIDER:-fake}"
cd "$ROOT/api"
if [[ "$PROVIDER" == "fake" ]]; then
  # empty keys: the scratch API can never call Claude or the organizer API, whatever the repo's .env says
  DATABASE_URL="sqlite:///$WORK/cqa.db" DATA_DIR="$WORK" ANTHROPIC_API_KEY="" ORGANIZER_API_URL="" CQA_ADMIN_TOKEN="" \
    .venv/bin/python -m uvicorn cqa.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
else
  # the Claude key comes from the repo's .env; the organizer API stays unreachable
  DATABASE_URL="sqlite:///$WORK/cqa.db" DATA_DIR="$WORK" ORGANIZER_API_URL="" CQA_ADMIN_TOKEN="" \
    .venv/bin/python -m uvicorn cqa.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
fi
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$PID" 2>/dev/null; then echo "The scratch API did not start (see the log above)." >&2; exit 1; fi
  sleep 1
done

cd "$ROOT"
api/.venv/bin/python scripts/control_run.py --api "http://127.0.0.1:$PORT" --rehearse --provider "$PROVIDER" --no-snapshot
