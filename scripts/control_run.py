#!/usr/bin/env python3
"""Control-stage runner: import → evaluate → retry → export results.json → validate → commit → push.

Designed for the one-hour window before the stop-code (docs/ORGANIZER_UPDATES.md §1). Run from the repo root:

  api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID>
  api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> --provider ensemble
  api/.venv/bin/python scripts/control_run.py --rehearse            # 10 DEV chats, writes results/rehearsal/, no git

Defaults: hosted API, organizer checklist (version 1), expect 10 records, out results/prod/results.json, git push on.
Every step prints what it did; any failure stops before git with a clear message, and the partial file is still written.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

import httpx  # noqa: E402

from cqa.export.results_json import from_api, summarize, validate_document, write_document  # noqa: E402

HOSTED = "https://api-production-58ab4.up.railway.app"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def resolve_version(http: httpx.Client, base: str, version_id: int | None) -> int:
    if version_id:
        return version_id
    best = None
    for cl in http.get(f"{base}/api/checklists").json():
        for v in cl.get("versions", []):
            if v.get("version_no") == 1 and (best is None or v["id"] < best):
                best = v["id"]
    if best is None:
        raise SystemExit("no organizer checklist (version 1) found on the API")
    return best


def import_group(http: httpx.Client, base: str, group: str) -> list[str]:
    before = {c["id"] for c in http.get(f"{base}/api/conversations", params={"page_size": 500}).json()["items"]}
    r = http.post(f"{base}/api/datasets/import", json={"group_id": group})
    if r.status_code != 200:
        raise SystemExit(f"import failed: HTTP {r.status_code} {r.text[:300]}")
    info = r.json()
    log(f"imported {info.get('imported')} records, languages {info.get('by_language')}, channels {info.get('by_channel')}, dataset {info.get('dataset_id')}")
    ds_id = info.get("dataset_id")
    convs = http.get(f"{base}/api/conversations", params={"page_size": 500, "dataset_id": ds_id}).json()["items"]
    ids = [c["id"] for c in convs]
    if not ids:  # very old API without dataset filter: fall back to the diff
        after = {c["id"] for c in http.get(f"{base}/api/conversations", params={"page_size": 500}).json()["items"]}
        ids = sorted(after - before)
    if len(ids) != info.get("imported"):
        log(f"WARNING: {len(ids)} conversations resolved for the dataset, import reported {info.get('imported')}")
    return ids


def run_job(http: httpx.Client, base: str, ids: list[str], version_id: int, provider: str, timeout_s: int = 1800) -> dict:
    r = http.post(f"{base}/api/evaluations/run", json={"conversation_ids": ids, "checklist_version_id": version_id, "provider": provider})
    if r.status_code != 200:
        raise SystemExit(f"evaluation start failed: HTTP {r.status_code} {r.text[:300]}")
    job = r.json()
    log(f"job {job['job_id']}: {job['queued']} dialogs, estimate ${job.get('estimated_cost_usd')}, provider {provider}")
    t0 = time.time()
    last = None
    while True:
        j = http.get(f"{base}/api/jobs/{job['job_id']}").json()
        state = (j["status"], j["done"], j["failed"])
        if state != last:
            log(f"  {j['status']}: done {j['done']}/{j['total']}, failed {j['failed']}, cost ${j['cost_usd']:.2f}")
            last = state
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        if time.time() - t0 > timeout_s:
            raise SystemExit("evaluation job timed out")
        time.sleep(4)


def not_completed(http: httpx.Client, base: str, ids: list[str], version_id: int) -> list[str]:
    bad = []
    for cid in ids:
        r = http.get(f"{base}/api/conversations/{cid}/evaluation", params={"checklist_version_id": version_id})
        if r.status_code != 200 or r.json().get("status") != "completed" or any("error" in (c.get("flags") or []) for c in r.json().get("criteria", [])):
            bad.append(cid)
    return bad


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=HOSTED)
    ap.add_argument("--group", help="control group_id announced by the organizers")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "ensemble", "local"])
    ap.add_argument("--version-id", type=int, help="checklist version (default: organizer version 1)")
    ap.add_argument("--team", default=None, help="team_name for the file (default: env TEAM_NAME or repo name)")
    ap.add_argument("--expect", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "results" / "prod" / "results.json"))
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--skip-import", action="store_true", help="use already imported conversations given by --ids")
    ap.add_argument("--ids", help="comma-separated ids (with --skip-import)")
    ap.add_argument("--rehearse", action="store_true", help="dry run on 10 DEV chats: no import, writes results/rehearsal/results.json, no git")
    args = ap.parse_args()

    import os

    team = args.team or os.environ.get("TEAM_NAME") or "k4ssymzhomart/freedom"
    base = args.api.rstrip("/")
    t_start = time.time()
    with httpx.Client(timeout=120, follow_redirects=True) as http:
        health = http.get(f"{base}/api/health").json()
        log(f"API {base} health: {health}")
        if not health.get("providers", {}).get("anthropic") and args.provider != "local":
            raise SystemExit("Claude provider is not configured on this API")
        version_id = resolve_version(http, base, args.version_id)
        log(f"checklist version {version_id} (organizer checklist)")

        if args.rehearse:
            convs = http.get(f"{base}/api/conversations", params={"page_size": 500, "channel": "agent chat"}).json()["items"]
            ids = [c["id"] for c in convs if c["id"].startswith("p1-")][:: max(1, len(convs) // 10)][:10]
            out = ROOT / "results" / "rehearsal" / "results.json"
            args.no_git = True
        elif args.skip_import:
            if not args.ids:
                raise SystemExit("--skip-import needs --ids")
            ids = [x.strip() for x in args.ids.split(",") if x.strip()]
            out = Path(args.out)
        else:
            if not args.group:
                raise SystemExit("--group is required (the control group_id)")
            ids = import_group(http, base, args.group)
            out = Path(args.out)
        log(f"{len(ids)} dialogs: {', '.join(ids)}")

        job = run_job(http, base, ids, version_id, args.provider)
        bad = not_completed(http, base, ids, version_id)
        if bad:
            log(f"retrying {len(bad)} dialogs that did not complete: {bad}")
            run_job(http, base, bad, version_id, "anthropic")
            bad = not_completed(http, base, bad, version_id)
            if bad:
                log(f"still not completed after retry (will be exported as status=error): {bad}")

    doc, turn_counts, channels = from_api(base, conversation_ids=ids, checklist_version_id=version_id, team_name=team)
    errors, warnings = validate_document(doc, expected_ids=ids, expected_count=args.expect if not args.rehearse else len(ids), turn_counts=turn_counts, channels=channels)
    write_document(doc, out)
    log(f"written {out.relative_to(ROOT)}: {summarize(doc)}")
    for w in warnings:
        log(f"WARN  {w}")
    for e in errors:
        log(f"ERROR {e}")
    if errors:
        log("results file is INVALID; fix before pushing (the file was still written)")
        return 1
    log(f"VALID in {time.time() - t_start:.0f}s")

    if args.no_git:
        return 0
    rel = str(out.relative_to(ROOT))
    for cmd in (["add", rel], ["commit", "-m", f"Control results: {rel} ({len(ids)} dialogs)\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"], ["push"]):
        r = git(*cmd)
        log(f"git {' '.join(cmd[:1])}: {'ok' if r.returncode == 0 else 'FAILED'} {r.stdout.strip()[-200:]} {r.stderr.strip()[-300:]}")
        if r.returncode != 0 and cmd[0] != "commit":
            return 1
    remote = git("remote", "get-url", "origin").stdout.strip().removesuffix(".git")
    log(f"pushed: {remote}/blob/main/{rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
