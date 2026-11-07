#!/usr/bin/env python3
"""Control-stage runner: import → evaluate → retry → export results.json → validate → commit → push.

Designed for the one-hour window before the stop-code (docs/ORGANIZER_UPDATES.md §1). Run from the repo root:

  api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID>
  api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> --provider ensemble
  api/.venv/bin/python scripts/control_run.py --rehearse            # 10 DEV chats, writes api/data/rehearsal/, no git

Defaults: hosted API, organizer checklist (version 1), expect 10 records, out results/prod/results.json, git push on.
Every step prints what it did; any failure stops before git with a clear message, and the partial file is still written.

Platform behaviour it relies on (docs/ARCHITECTURE_V2.md §8, §10):
- a database snapshot "pre-control-<UTC time>" is taken before the import (skip with --no-snapshot; a failure only warns);
- a dry run comes first: if the platform guards would refuse the run (daily budget, dialogs per run), the script stops
  before spending anything, prints the guard's Russian message and the --skip-import --ids line to continue;
- a job left "interrupted" by an API restart or redeploy is resumed (POST /api/jobs/{id}/resume) and followed;
- 429 too_many_jobs is retried every 30 s for up to 10 minutes.
`--provider fake` runs the offline judge for a free rehearsal of the whole flow against a local or scratch API (refused
for the hosted URL); it never commits or pushes.

Hardening for the deadline (docs/USER_STORIES.md DEMO-06, DEMO-11):
- every request is sent as role admin ("control-run"), so it keeps working when CQA_HEADERLESS_ROLE makes header-less
  calls read-only; snapshots send X-CQA-Admin-Token from the environment or the repo's .env (never printed);
- 0 resolved dialogs stop the run before anything is evaluated;
- an uploaded control file replaces changed records of the same ids (on_conflict=replace) and keeps identical ones;
- the results file is published from a temporary worktree at origin/main: your checkout, index and local branch are
  never touched (no stash, no rebase of your work, no unrelated local commit pushed); a rejected push is rebased
  and retried (3 attempts); afterwards origin must hold exactly the exported file, else the script fails loudly;
- --export-only re-exports, validates and pushes without evaluating again (with --skip-import --ids or --group; refused
  with --source-url/--source-file, whose upload may change dialogs that then need a new evaluation); --tag adds a
  "control-<UTC>" tag once origin is verified.
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
ACTOR_HEADERS = {"X-CQA-Role": "admin", "X-CQA-User": "control-run"}
PUSH_ATTEMPTS = 3


def admin_token() -> str | None:
    """CQA_ADMIN_TOKEN from the environment, else from the repo's .env (git-ignored). Never printed."""
    import os

    if os.environ.get("CQA_ADMIN_TOKEN"):
        return os.environ["CQA_ADMIN_TOKEN"].strip()
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("CQA_ADMIN_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None
TERMINAL = ("completed", "failed", "cancelled", "interrupted")
MAX_RESUMES = 3
BUSY_RETRY_S, BUSY_RETRY_MAX_S = 30, 600


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def error_of(r: httpx.Response) -> dict:
    """The API's error envelope {"code", "message", ...} (or a synthetic one for non-JSON answers)."""
    try:
        err = r.json().get("error")
        if isinstance(err, dict):
            return err
    except ValueError:
        pass
    return {"code": f"http_{r.status_code}", "message": r.text[:300]}


def snapshot_before_control(http: httpx.Client, base: str) -> None:
    """Back up the API database before the control run (admin snapshot); never blocks the run."""
    name = "pre-control-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    token = admin_token()
    headers = {"X-CQA-Admin-Token": token} if token else {}
    try:
        r = http.post(f"{base}/api/admin/snapshots", json={"name": name, "note": "перед контрольным прогоном"}, headers=headers)
    except httpx.HTTPError as e:
        log(f"WARNING: snapshot {name} not taken ({type(e).__name__}: {e}); continuing")
        return
    if r.status_code in (200, 201):
        info = r.json()
        log(f"snapshot {info.get('name')} taken: {info.get('counts')}")
    else:
        err = error_of(r)
        log(f"WARNING: snapshot {name} not taken: HTTP {r.status_code} {err.get('code')}: {err.get('message')}; continuing")


def preflight(http: httpx.Client, base: str, ids: list[str], version_id: int, provider: str) -> dict:
    """Dry run: the estimate and whether the platform guards would refuse the real run right now."""
    r = http.post(f"{base}/api/evaluations/run", json={"conversation_ids": ids, "checklist_version_id": version_id,
                                                       "provider": provider, "dry_run": True})
    if r.status_code != 200:
        err = error_of(r)
        raise SystemExit(f"dry run failed: HTTP {r.status_code} {err.get('code')}: {err.get('message')}")
    info = r.json()
    budget = info.get("budget") or {}
    log(f"dry run: {info.get('queued')} dialogs, estimate ${info.get('estimated_cost_usd')}, "
        f"budget remaining {'unlimited' if budget.get('unlimited') else '$' + str(budget.get('remaining'))}")
    guard = info.get("guard")
    if guard and guard.get("code") != "too_many_jobs":  # busy is transient: start() waits for it
        raise SystemExit(f"the API would refuse this run ({guard.get('code')}): {guard.get('message')}\n"
                         f"Fix it (e.g. raise DAILY_BUDGET_USD on the API), then continue without re-importing:\n"
                         f"  --skip-import --ids {','.join(ids)}")
    return info


def start(http: httpx.Client, base: str, url: str, body: dict | None = None) -> dict:
    """POST a run or resume; wait while the API answers 429 too_many_jobs; stop with the API's message otherwise."""
    waited = 0
    while True:
        r = http.post(url, json=body) if body is not None else http.post(url)
        if r.status_code == 200:
            return r.json()
        err = error_of(r)
        if r.status_code == 429 and err.get("code") == "too_many_jobs" and waited < BUSY_RETRY_MAX_S:
            log(f"  API busy ({err.get('message')}); retrying in {BUSY_RETRY_S}s")
            time.sleep(BUSY_RETRY_S)
            waited += BUSY_RETRY_S
            continue
        raise SystemExit(f"{url.rsplit('/api/', 1)[-1]} failed: HTTP {r.status_code} {err.get('code')}: {err.get('message')}")


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
    log(f"imported {info.get('imported')} records, languages {info.get('by_language')}, channels {info.get('by_channel')}, dataset {info.get('dataset_id')}"
        + (f"; created {info.get('created')}, updated {info.get('updated')}, unchanged {info.get('unchanged')}, busy {info.get('busy')}"
           if "created" in info else ""))
    if info.get("conversation_ids"):  # current API: the import names its dialogs
        return list(info["conversation_ids"])
    ds_id = info.get("dataset_id")
    convs = http.get(f"{base}/api/conversations", params={"page_size": 500, "dataset_id": ds_id}).json()["items"]
    ids = [c["id"] for c in convs]
    if not ids:  # very old API without dataset filter: fall back to the diff
        after = {c["id"] for c in http.get(f"{base}/api/conversations", params={"page_size": 500}).json()["items"]}
        ids = sorted(after - before)
    if len(ids) != info.get("imported"):
        log(f"WARNING: {len(ids)} conversations resolved for the dataset, import reported {info.get('imported')}")
    return ids


def upload_payload(http: httpx.Client, base: str, payload: dict | list, label: str) -> list[str]:
    """Upload organizer-format JSON (full API response, a record list, or one record) through POST /api/conversations/upload."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # the control file is the source of truth for its ids: identical records stay as they are, changed ones are replaced
    r = http.post(f"{base}/api/conversations/upload", params={"on_conflict": "replace"},
                  files={"file": (f"{label}.json", body, "application/json")})
    if r.status_code != 200:
        raise SystemExit(f"upload failed: HTTP {r.status_code} {r.text[:300]}")
    info = r.json()
    ids = info.get("conversation_ids") or []
    log(f"uploaded {info.get('imported')} records, languages {info.get('by_language')}, channels {info.get('by_channel')}"
        + (f"; created {info.get('created')}, updated {info.get('updated')}, unchanged {info.get('unchanged')}, busy {info.get('busy')}"
           if "created" in info else ""))
    return ids


def fetch_source(url: str) -> dict | list:
    """GET the control set from the URL the organizers announce (any host), on this laptop."""
    with httpx.Client(timeout=120, follow_redirects=True) as h:
        r = h.get(url)
    if r.status_code != 200:
        raise SystemExit(f"source URL returned HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if isinstance(data, dict) and data.get("isSuccess") is False:
        raise SystemExit(f"source API error: {data.get('error')}")
    return data


def run_job(http: httpx.Client, base: str, ids: list[str], version_id: int, provider: str, timeout_s: int = 1800,
            poll_s: float = 4.0) -> dict:
    job = start(http, base, f"{base}/api/evaluations/run", {"conversation_ids": ids, "checklist_version_id": version_id, "provider": provider})
    job_id = job["job_id"]
    log(f"job {job_id}: {job['queued']} dialogs, estimate ${job.get('estimated_cost_usd')}, provider {provider}")
    t0 = time.time()
    last = None
    resumes = 0
    while True:
        try:
            r = http.get(f"{base}/api/jobs/{job_id}")
        except httpx.HTTPError as e:  # the API is restarting or redeploying: keep polling until the timeout
            log(f"  job {job_id}: API unreachable ({type(e).__name__}); retrying")
            r = None
        if r is None or r.status_code != 200:
            if r is not None:
                log(f"  job {job_id}: HTTP {r.status_code} while polling; retrying")
            j = None
        else:
            j = r.json()
            state = (j["status"], j["done"], j["failed"])
            if state != last:
                log(f"  {j['status']}: done {j['done']}/{j['total']}, failed {j['failed']}, cost ${j['cost_usd']:.2f}")
                last = state
            if j["status"] == "interrupted" and resumes < MAX_RESUMES:
                try:
                    rr = http.post(f"{base}/api/jobs/{job_id}/resume")
                except httpx.HTTPError as e:
                    log(f"  resume of {job_id}: API unreachable ({type(e).__name__}); retrying")
                    time.sleep(poll_s)
                    continue
                err = error_of(rr) if rr.status_code != 200 else {}
                if rr.status_code == 200:
                    resumes += 1
                    job_id = rr.json()["job_id"]
                    log(f"  job interrupted by an API restart: resumed as {job_id} ({rr.json().get('queued')} dialogs re-queued)")
                    last = None
                    continue
                if err.get("code") == "nothing_to_resume":
                    log("  job interrupted, but every dialog already has a completed evaluation")
                    return j
                if err.get("code") == "already_resumed" and err.get("job_id"):
                    job_id = err["job_id"]
                    log(f"  job already resumed as {job_id}; following it")
                    last = None
                    continue
                if rr.status_code == 429 and err.get("code") == "too_many_jobs":
                    log(f"  resume refused for now ({err.get('message')}); retrying")
                else:
                    log(f"  resume failed: HTTP {rr.status_code} {err.get('code')}: {err.get('message')}")
                    return j
            elif j["status"] in TERMINAL:
                return j
        if time.time() - t0 > timeout_s:
            raise SystemExit("evaluation job timed out")
        time.sleep(poll_s)


def not_completed(http: httpx.Client, base: str, ids: list[str], version_id: int) -> list[str]:
    bad = []
    for cid in ids:
        r = http.get(f"{base}/api/conversations/{cid}/evaluation", params={"checklist_version_id": version_id})
        if (r.status_code != 200 or r.json().get("status") != "completed" or r.json().get("last_attempt_failed")
                or any("error" in (c.get("flags") or []) for c in r.json().get("criteria", []))):
            bad.append(cid)
    return bad


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=HOSTED)
    ap.add_argument("--group", help="control group_id announced by the organizers")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "ensemble", "local", "fake"],
                    help="fake = offline judge for a free rehearsal of the flow (never committed or pushed)")
    ap.add_argument("--version-id", type=int, help="checklist version (default: organizer version 1)")
    ap.add_argument("--team", default=None, help="team_name for the file (default: env TEAM_NAME or repo name)")
    ap.add_argument("--expect", type=int, default=10)
    ap.add_argument("--out", default=str(ROOT / "results" / "prod" / "results.json"))
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--skip-import", action="store_true", help="use already imported conversations given by --ids")
    ap.add_argument("--ids", help="comma-separated ids (with --skip-import)")
    ap.add_argument("--rehearse", action="store_true", help="dry run on 10 DEV chats: no import, writes api/data/rehearsal/results.json, no git")
    ap.add_argument("--source-url", help="full GET URL of the control set (if the organizers give a URL instead of a group_id); fetched on this laptop and uploaded")
    ap.add_argument("--source-file", help="path to a downloaded control-set JSON (organizer format); uploaded to the API")
    ap.add_argument("--no-snapshot", action="store_true", help="do not back up the API database before the control run")
    ap.add_argument("--export-only", action="store_true",
                    help="with --skip-import --ids or --group (not with --source-url/--source-file): export, validate and "
                         "push without evaluating again")
    ap.add_argument("--tag", action="store_true", help="also create and push a tag control-<UTC time> on the results commit")
    args = ap.parse_args()
    if args.export_only and args.ids and not args.skip_import:
        args.skip_import = True  # --export-only --ids X: the dialogs are named, nothing is imported
    if args.export_only and (args.source_url or args.source_file):
        # the upload may replace changed dialogs (on_conflict=replace): exporting them without a new evaluation would
        # write the verdict of the old text and still report the file VALID
        raise SystemExit("--export-only cannot be combined with --source-url/--source-file: an uploaded file can change "
                         "dialogs, and exporting without evaluating them again would mark a stale verdict VALID.\n"
                         "To re-export dialogs that are already evaluated, use --export-only with --skip-import --ids <ids> "
                         "or with --group <group>; to upload and evaluate the file, drop --export-only.")
    if args.provider == "fake":
        if args.api.rstrip("/") == HOSTED:  # fake verdicts would become the latest evaluations of the jury instance
            raise SystemExit("--provider fake is for rehearsals against a local or scratch API (--api http://127.0.0.1:...), "
                             "never the hosted instance")
        if not args.no_git:
            log("provider fake is a rehearsal judge: --no-git is implied (results are never committed)")
            args.no_git = True

    import os

    team = args.team or os.environ.get("TEAM_NAME") or "Я Тебя Могну"
    base = args.api.rstrip("/")
    t_start = time.time()
    with httpx.Client(timeout=120, follow_redirects=True, headers=ACTOR_HEADERS) as http:
        health = http.get(f"{base}/api/health").json()
        log(f"API {base} health: {health}")
        if not health.get("providers", {}).get("anthropic") and args.provider not in ("local", "fake"):
            raise SystemExit("Claude provider is not configured on this API")
        version_id = resolve_version(http, base, args.version_id)
        log(f"checklist version {version_id} (organizer checklist)")

        if args.rehearse:
            convs = http.get(f"{base}/api/conversations", params={"page_size": 500, "channel": "agent chat"}).json()["items"]
            ids = [c["id"] for c in convs if c["id"].startswith("p1-")][:: max(1, len(convs) // 10)][:10]
            out = ROOT / "api" / "data" / "rehearsal" / "results.json"  # git-ignored, never mixed with results/
            args.no_git = True
        elif args.skip_import:
            if not args.ids:
                raise SystemExit("--skip-import needs --ids")
            ids = [x.strip() for x in args.ids.split(",") if x.strip()]
            out = Path(args.out)
        elif args.source_url or args.source_file:
            payload = fetch_source(args.source_url) if args.source_url else json.loads(Path(args.source_file).read_text(encoding="utf-8"))
            raw = (args.source_file or args.source_url).rstrip("/").split("/")[-1].split("?")[0] or "control"
            if not args.no_snapshot:
                snapshot_before_control(http, base)
            ids = upload_payload(http, base, payload, label=f"control-{raw[:40]}")
            out = Path(args.out)
        else:
            if not args.group:
                raise SystemExit("--group, --source-url or --source-file is required")
            if args.export_only:  # the dialogs are already there: no snapshot, no re-import
                ids = dataset_ids(http, base, args.group)
            else:
                if not args.no_snapshot:
                    snapshot_before_control(http, base)
                ids = import_group(http, base, args.group)
            out = Path(args.out)
        if not ids:
            raise SystemExit("0 dialogs resolved for the control set: nothing was evaluated — check --group / --source-url / --source-file / --ids")
        log(f"{len(ids)} dialogs: {', '.join(ids)}")

        if args.export_only:
            log("--export-only: no evaluation, exporting what the API has")
        else:
            preflight(http, base, ids, version_id, args.provider)
            run_job(http, base, ids, version_id, args.provider)
            bad = not_completed(http, base, ids, version_id)
            if bad:
                log(f"retrying {len(bad)} dialogs that did not complete: {bad}")
                run_job(http, base, bad, version_id, args.provider if args.provider in ("local", "fake") else "anthropic")
                bad = not_completed(http, base, bad, version_id)
                if bad:
                    log(f"still not completed after retry (will be exported as status=error): {bad}")

    doc, turn_counts, channels = from_api(base, conversation_ids=ids, checklist_version_id=version_id, team_name=team)
    errors, warnings = validate_document(doc, expected_ids=ids, expected_count=args.expect if not args.rehearse else len(ids), turn_counts=turn_counts, channels=channels)
    write_document(doc, out)
    shown = out.resolve().relative_to(ROOT) if out.resolve().is_relative_to(ROOT) else out
    log(f"written {shown}: {summarize(doc)}")
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
    if not out.resolve().is_relative_to(ROOT):
        log(f"{out} is outside the repository: not committed (use the default --out or --no-git)")
        return 1
    rel = str(out.resolve().relative_to(ROOT))
    return commit_and_push(rel, len(ids), tag=args.tag)


def git_in(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)


def publish_branch() -> str:
    """The branch the results go to: the current branch when it has one, else main."""
    b = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return b if b and b != "HEAD" else "main"


def commit_and_push(rel: str, n: int, *, tag: bool = False, branch: str | None = None) -> int:
    """Publish exactly the exported results file to origin/<branch>, from a temporary worktree.

    Your working tree, index and local branch are never touched: no stash, no rebase of your work, and no unrelated
    local commit is pushed along (the redesign may be half-done in the checkout during the control hour).
    1. fetch origin/<branch> and add a detached worktree at it (outside the repository folder);
    2. copy the exported file in and commit only it; an identical file means origin already has it (nothing to commit);
    3. push; when rejected (someone pushed meanwhile), rebase the single commit onto the new origin/<branch> and retry
       (3 attempts). A conflict, or a concurrent change of the same file that would make the pushed content differ from
       the export, stops the run before anything unvalidated reaches origin;
    4. prove origin/<branch>:<rel> is the exported blob; optionally tag that commit;
    5. remove the worktree. Your local branch gets the results commit with the next `git pull`.
    """
    import shutil
    import tempfile

    rel = Path(rel).as_posix()
    branch = branch or publish_branch()
    h = git("hash-object", "--", rel)
    exported_blob = h.stdout.strip()
    if h.returncode != 0 or not exported_blob:
        log(f"git hash-object failed: {h.stderr.strip()[-300:]}")
        return 1
    f = git("fetch", "origin", branch)
    if f.returncode != 0:
        log(f"git fetch origin {branch} failed: {f.stderr.strip()[-300:]}")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="cqa-results-"))
    wt = tmp / "wt"
    add = git("worktree", "add", "--detach", str(wt), f"origin/{branch}")
    if add.returncode != 0:
        log(f"git worktree add failed: {add.stderr.strip()[-300:]}")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    try:
        target = wt / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
        git_in(wt, "add", "--", rel)
        unchanged = git_in(wt, "diff", "--cached", "--quiet", "--", rel).returncode == 0
        if unchanged:
            log(f"origin/{branch} already has this exact file: nothing to commit")
        else:
            c = git_in(wt, "-c", "commit.gpgsign=false", "commit", "-m",
                       f"Control results: {rel} ({n} dialogs)\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", "--", rel)
            if c.returncode != 0:
                log(f"git commit failed: {(c.stdout + c.stderr).strip()[-300:]}")
                return 1
            log("git commit: ok (in a temporary worktree; your checkout is untouched)")
            for attempt in range(1, PUSH_ATTEMPTS + 1):
                ps = git_in(wt, "push", "origin", f"HEAD:refs/heads/{branch}")
                if ps.returncode == 0:
                    log(f"git push: ok (attempt {attempt})")
                    break
                log(f"git push rejected (attempt {attempt}): {ps.stderr.strip()[-300:]}")
                if attempt == PUSH_ATTEMPTS:
                    log("git push failed after retries")
                    return 1
                if git_in(wt, "fetch", "origin", branch).returncode != 0:
                    log("git fetch failed; retrying")
                    continue
                rb = git_in(wt, "rebase", f"origin/{branch}")
                if rb.returncode != 0:
                    git_in(wt, "rebase", "--abort")
                    log(f"git rebase onto origin/{branch} failed (someone changed {rel} at the same time?): "
                        f"{(rb.stdout + rb.stderr).strip()[-300:]}; nothing was pushed. Re-run with --export-only.")
                    return 1
                after = git_in(wt, "rev-parse", f"HEAD:{rel}").stdout.strip()
                if after != exported_blob:
                    log(f"STOP: after the rebase {rel} is not the exported file (someone else's change was merged into it); "
                        f"nothing unvalidated was pushed. Check git log -p origin/{branch} -- {rel}, then re-run with --export-only.")
                    return 1
        git("fetch", "origin", branch)
        remote_blob = git("rev-parse", "--verify", "-q", f"origin/{branch}:{rel}").stdout.strip()
        if remote_blob != exported_blob:
            log(f"VERIFY FAILED: origin/{branch}:{rel} is {remote_blob[:12] or 'missing'}, the exported file is {exported_blob[:12]}"
                f" (someone pushed {rel} after us?). Re-run with --export-only.")
            return 1
        remote = git("remote", "get-url", "origin").stdout.strip().removesuffix(".git")
        log(f"verified on origin/{branch} (blob {exported_blob[:12]}): {remote}/blob/{branch}/{rel}")
        if tag:
            name = "control-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            commit = git("rev-parse", f"origin/{branch}").stdout.strip()
            tr = git("tag", "-a", name, commit, "-m", f"Control results ({n} dialogs)")
            tp = git("push", "origin", name) if tr.returncode == 0 else tr
            log(f"git tag {name} on {commit[:12]}: {'ok' if tp.returncode == 0 else 'FAILED ' + tp.stderr.strip()[-200:]}")
        log(f"your local branch is unchanged; run `git pull` later to get the results commit")
        return 0
    finally:
        git("worktree", "remove", "--force", str(wt))
        git("worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def dataset_ids(http: httpx.Client, base: str, group: str) -> list[str]:
    """Ids of the dialogs already imported for `group` (for --export-only)."""
    sets = [d for d in http.get(f"{base}/api/datasets").json() if d.get("group_id") == group]
    ids: list[str] = []
    for d in sets:
        items = http.get(f"{base}/api/conversations", params={"page_size": 500, "dataset_id": d["id"]}).json()["items"]
        ids += [c["id"] for c in items if c["id"] not in ids]
    return ids


if __name__ == "__main__":
    sys.exit(main())
