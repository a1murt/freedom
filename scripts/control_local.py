#!/usr/bin/env python3
"""On-prem judge for the control set: the same pipeline with a local open model, next to the official cloud run.

Runs on a laptop inside the university network. It never touches the hosted instance, git or the official results:
- its own database `api/data/control_local.db` and outputs in `api/data/control_local/` (both git-ignored);
- the judge is the OpenAI-compatible local provider (Ollama on the GPU server, reached through an SSH tunnel);
- the official file stays `results/prod/results.json` from `scripts/control_run.py` (ensemble on the hosted API).

Usage, from the repository root:
  api/.venv/bin/python scripts/control_local.py --rehearse 2 --tunnel univer-gpu6      # 2 short DEV chats, end to end
  api/.venv/bin/python scripts/control_local.py --group <CONTROL_GROUP_ID> --tunnel univer-gpu6
  api/.venv/bin/python scripts/control_local.py --source-url <URL>   |   --source-file <PATH>
  api/.venv/bin/python scripts/control_local.py --compare-only        # agreement with the cloud file, no judging

Resumable: run the same command again after an interruption; dialogs with a clean evaluation are skipped and failed ones
are judged again. The cloud file compared against is results/prod/results.json (control) or results/dev/results.json
(rehearsal) unless --compare names another.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "api"
DATA = API / "data"
OUT = DATA / "control_local"
CODES = ("contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical")


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--group", help="control group_id announced by the organizers (fetched with ORGANIZER_API_URL from .env)")
    src.add_argument("--source-url", help="full GET URL of the control set, if the organizers give a URL")
    src.add_argument("--source-file", help="downloaded control-set JSON in the organizer format")
    src.add_argument("--rehearse", type=int, metavar="N", help="rehearse on the N shortest DEV chats")
    src.add_argument("--compare-only", action="store_true", help="only compare the existing local file with the cloud file")
    ap.add_argument("--endpoint", default="http://localhost:11435/v1", help="OpenAI-compatible base URL of the local model")
    ap.add_argument("--model", default="qwen3.8:27b-q4_K_M", help="model name served by the endpoint")
    ap.add_argument("--tunnel", metavar="SSH_HOST", help="open ssh -L <port>:127.0.0.1:11434 to this host when the endpoint is down")
    ap.add_argument("--parallel", type=int, default=1, help="concurrent requests to the endpoint (the shared Ollama serves one at a time)")
    ap.add_argument("--compare", help="cloud results.json to compare with")
    ap.add_argument("--db", default=str(DATA / "control_local.db"))
    ap.add_argument("--team", default=os.environ.get("TEAM_NAME", "Я Тебя Могну"))
    return ap.parse_args()


ARGS = parse()
# configure the app before it is imported: own database, local judge, no cloud keys needed
os.environ["DATABASE_URL"] = f"sqlite:///{Path(ARGS.db).resolve().as_posix()}"
os.environ["LOCAL_ENDPOINT"] = ARGS.endpoint
os.environ["LOCAL_MODEL"] = ARGS.model
os.environ["LOCAL_MAX_PARALLEL"] = str(max(1, ARGS.parallel))
os.environ.setdefault("SEED_SYNC", "1")
sys.path.insert(0, str(API))


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def endpoint_models(endpoint: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/models", timeout=8) as r:
            return [m.get("id") for m in json.loads(r.read().decode("utf-8")).get("data", [])]
    except Exception:
        return None


def ensure_endpoint(fatal: bool = True) -> bool:
    """Endpoint reachable and the model loaded; reopens the SSH tunnel (with --tunnel) when the endpoint is down.
    With fatal=False it returns False instead of exiting, so a long run can wait for the network to come back."""
    try:
        _ensure_endpoint()
        return True
    except SystemExit as e:
        if fatal:
            raise
        log(f"endpoint problem: {e}")
        return False


def _ensure_endpoint() -> None:
    models = endpoint_models(ARGS.endpoint)
    if models is None and ARGS.tunnel:
        port = ARGS.endpoint.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[-1]
        log(f"endpoint down; opening the SSH tunnel to {ARGS.tunnel} on port {port}")
        subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
                        "-f", "-N", "-L", f"{port}:127.0.0.1:11434", ARGS.tunnel], check=False, timeout=30)
        time.sleep(2)
        models = endpoint_models(ARGS.endpoint)
    if models is None:
        raise SystemExit(f"local endpoint {ARGS.endpoint} is not reachable (pass --tunnel <ssh host> or start Ollama/vLLM)")
    if ARGS.model not in models:
        raise SystemExit(f"model {ARGS.model!r} is not served by {ARGS.endpoint}; available: {', '.join(models)}")
    t0 = time.time()
    body = json.dumps({"model": ARGS.model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}).encode()
    req = urllib.request.Request(ARGS.endpoint.rstrip("/") + "/chat/completions", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        r.read()
    log(f"endpoint ok, model {ARGS.model} loaded ({time.time() - t0:.0f} s warm-up)")


def load_source() -> tuple[list[dict], str]:
    from cqa.ingest.adapter import fetch_organizer, records_from_payload

    if ARGS.group:
        return records_from_payload(fetch_organizer(ARGS.group.strip())), ARGS.group.strip()
    if ARGS.source_url:
        with urllib.request.urlopen(ARGS.source_url, timeout=120) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return records_from_payload(payload), "local:url"
    if ARGS.source_file:
        return records_from_payload(json.loads(Path(ARGS.source_file).read_text(encoding="utf-8"))), "local:file"
    raise SystemExit("no source")


def prepare(db) -> tuple[list[str], int, str]:
    from sqlalchemy import select

    from cqa.checklist.service import active_version, ensure_seed
    from cqa.ingest.adapter import fetch_organizer, records_from_payload
    from cqa.ingest.service import import_records
    from cqa.models import Conversation, Evaluation

    ensure_seed(db)
    version = organizer_version(db) or active_version(db)
    # a private DB: rows left 'running' by an interrupted run would block a re-import of the same dialog
    for ev in db.execute(select(Evaluation).where(Evaluation.status.in_(("queued", "running")))).scalars():
        ev.status, ev.error = "failed", "interrupted run (control_local restart)"
    db.commit()
    if ARGS.rehearse:
        if not db.execute(select(Conversation.id).where(Conversation.id.like("p1-%"))).first():
            import_records(db, records_from_payload(fetch_organizer("DEV")), "DEV")
        rows = db.execute(select(Conversation).where(Conversation.id.like("p1-%"))).scalars().all()
        ids = [c.id for c in sorted(rows, key=lambda c: (len(c.turns), c.id))[: ARGS.rehearse]]
        return ids, version.id, "rehearsal"
    records, group = load_source()
    res = import_records(db, records, group, source="control_local", on_conflict="replace")  # like control_run.py
    ids = [str(x) for x in (res.get("conversation_ids") or [r.get("conversation_id") for r in records])]
    log(f"imported {len(ids)} dialogs ({group}): {', '.join(ids)}")
    return ids, version.id, "control"


def organizer_version(db):
    """The checklist the cloud run judges with (control_run.resolve_version): the lowest-id version_no 1."""
    from sqlalchemy import select

    from cqa.models import ChecklistVersion

    return db.execute(select(ChecklistVersion).where(ChecklistVersion.version_no == 1).order_by(ChecklistVersion.id)).scalars().first()


def latest_local(db, cid: str, version_id: int):
    from sqlalchemy import select

    from cqa.models import Evaluation

    return db.execute(select(Evaluation).where(Evaluation.conversation_id == cid, Evaluation.checklist_version_id == version_id,
                                               Evaluation.provider == "local").order_by(Evaluation.id.desc())).scalars().first()


def clean_done(db, cid: str, version_id: int) -> bool:
    from cqa.models import Conversation

    ev = latest_local(db, cid, version_id)
    if not (ev and ev.status == "completed" and not (ev.error or "").strip()):
        return False
    changed = ((db.get(Conversation, cid).meta or {}) if db.get(Conversation, cid) else {}).get("transcript_changed_at")
    created = ev.created_at.isoformat() if ev.created_at else ""
    return not (changed and created and str(changed) > created)  # a replaced transcript is judged again


CONNECTION_WORDS = ("connection", "timeout", "timed out", "refused", "unreachable", "reset by peer")


def judge(ids: list[str], version_id: int, passes: int = 3) -> None:
    from cqa.db import SessionLocal
    from cqa.judge.runner import evaluate_conversation

    for attempt in range(1, passes + 1):
        db = SessionLocal()
        todo = [cid for cid in ids if not clean_done(db, cid, version_id)]
        db.close()
        if not todo:
            return
        if not wait_for_endpoint():
            log("endpoint still down: stopping; run the same command again when the network is back")
            return
        log(f"pass {attempt}: judging {len(todo)} dialog(s) with {ARGS.model}")
        t0 = time.time()
        for n, cid in enumerate(todo, 1):
            t1 = time.time()
            db = SessionLocal()
            prev = latest_local(db, cid, version_id)
            partial = bool(prev and prev.status == "completed" and (prev.error or "").strip())
            base_id = prev.id if partial else None
            db.close()
            try:
                if partial:  # only the criteria that failed are judged again; the clean rows are copied
                    asyncio.run(evaluate_conversation(cid, version_id, provider_name="local", model=ARGS.model, mode="partial",
                                                      base_evaluation_id=base_id))
                else:
                    asyncio.run(evaluate_conversation(cid, version_id, provider_name="local", model=ARGS.model))
            except Exception as e:  # keep going; the next pass retries it
                log(f"  {n}/{len(todo)} {cid}: raised {type(e).__name__}: {str(e)[:200]}")
            db = SessionLocal()
            ok = clean_done(db, cid, version_id)
            ev = latest_local(db, cid, version_id)
            err = (ev.error or ev.status) if ev and not ok else ""
            db.close()
            done = time.time() - t0
            eta = done / n * (len(todo) - n)
            if ok:
                log(f"  {n}/{len(todo)} {cid} ok in {time.time() - t1:.0f} s{' (partial)' if partial else ''}; "
                    f"elapsed {done / 60:.1f} min, ETA {eta / 60:.1f} min")
            else:
                log(f"  {n}/{len(todo)} {cid} FAILED in {time.time() - t1:.0f} s: {str(err)[:200]}")
                if any(w in str(err).lower() for w in CONNECTION_WORDS) and not wait_for_endpoint():
                    log("endpoint still down: stopping; run the same command again when the network is back")
                    return


def wait_for_endpoint(tries: int = 10, pause_s: int = 30) -> bool:
    for k in range(tries):
        if ensure_endpoint(fatal=False):
            return True
        log(f"  waiting for the endpoint ({k + 1}/{tries}) …")
        time.sleep(pause_s)
    return False


def export(ids: list[str], label: str, version_id: int) -> tuple[Path, int]:
    from cqa.export import results_json

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"results_{label}.json"
    code = results_json.main(["--db", "--ids", ",".join(ids), "--out", str(out), "--expect", str(len(ids)), "--team", ARGS.team,
                              "--version-id", str(version_id)])
    if code not in (0, None):
        log(f"export finished with code {code} (see the messages above)")
    doc = json.loads(out.read_text(encoding="utf-8"))
    if doc.get("model") != {"mode": "local", "name": ARGS.model}:  # no completed dialog leaves 'external_api/unknown'
        doc["model"] = {"mode": "local", "name": ARGS.model}
        results_json.write_document(doc, out)
    errors = [r["conversation_id"] for r in doc["results"] if r.get("status") != "ok"]
    if errors:
        log(f"{len(errors)} dialog(s) without a complete local verdict: {', '.join(errors)}")
    return out, len(errors)


def compare(local_path: Path, cloud_path: Path, rehearsal: bool = False) -> None:
    if not cloud_path.exists():
        log(f"no cloud file at {cloud_path}; skipping the comparison")
        return
    loc = {r["conversation_id"]: r for r in json.loads(local_path.read_text(encoding="utf-8"))["results"]}
    cld = {r["conversation_id"]: r for r in json.loads(cloud_path.read_text(encoding="utf-8"))["results"]}
    every = sorted(loc) if rehearsal else sorted(set(loc) | set(cld))  # a rehearsal covers a few DEV dialogs only
    ids = [i for i in every if i in loc and i in cld and loc[i]["status"] == "ok" and cld[i]["status"] == "ok"]
    excluded = [(i, loc.get(i, {}).get("status", "missing"), cld.get(i, {}).get("status", "missing"),
                 loc.get(i, {}).get("error") or cld.get(i, {}).get("error") or "") for i in every if i not in ids]
    if not ids:
        log("no dialogs judged by both; nothing to compare")
        return
    per, diffs = {}, []
    for code in CODES:
        hits = 0
        for i in ids:
            a = {c["criterion_id"]: c["value"] for c in loc[i]["criteria"]}.get(code)
            b = {c["criterion_id"]: c["value"] for c in cld[i]["criteria"]}.get(code)
            hits += a == b
            if a != b:
                diffs.append((i, code, a, b))
        per[code] = hits / len(ids)
    total = sum(per.values()) / len(per)
    missing_local = sum(1 for i, ls, cs, _ in excluded if cs == "ok" and ls != "ok")
    n_cloud_ok = len(ids) + missing_local
    strict = total * len(ids) / n_cloud_ok if n_cloud_ok else total
    lines = [f"# Local model vs cloud run: compared {len(ids)} of {len(every)} dialogs", "",
             f"Local: `{local_path.name}` ({ARGS.model}). Cloud: `{cloud_path}`.", "",
             "| Criterion | Agreement |", "|---|---|"]
    lines += [f"| {c} | {v:.2f} |" for c, v in per.items()]
    lines += [f"| **Mean** | **{total:.3f}** |", ""]
    if excluded:
        lines += [f"Strict mean, counting a dialog the local model did not finish as a miss on all criteria: {strict:.3f}.", "",
                  "| Excluded dialog | Local status | Cloud status | Error |", "|---|---|---|---|"]
        lines += [f"| {i} | {ls} | {cs} | {str(err)[:120]} |" for i, ls, cs, err in excluded]
        lines += [""]
    lines += ["| Dialog | Criterion | Local | Cloud |", "|---|---|---|---|"]
    lines += [f"| {i} | {c} | {a} | {b} |" for i, c, a, b in diffs]
    score_l = {i: (loc[i].get("score") or {}).get("final_score") for i in ids}
    score_c = {i: (cld[i].get("score") or {}).get("final_score") for i in ids}
    mae = [abs(score_l[i] - score_c[i]) for i in ids if score_l[i] is not None and score_c[i] is not None]
    if mae:
        lines += ["", f"Mean absolute difference of totals: {sum(mae) / len(mae):.2f} points."]
    (OUT / "agreement.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"local vs cloud: mean per-criterion agreement {total:.3f} on {len(ids)} of {len(every)} dialogs"
        + (f" (strict {strict:.3f}; excluded: {', '.join(i for i, *_ in excluded)})" if excluded else "") + f" -> {OUT / 'agreement.md'}")
    for c, v in per.items():
        print(f"    {c:15} {v:.2f}")


def main() -> int:
    t0 = time.time()
    label = "rehearsal" if ARGS.rehearse else "control"
    cloud = Path(ARGS.compare) if ARGS.compare else ROOT / "results" / ("dev" if ARGS.rehearse else "prod") / "results.json"
    if ARGS.compare_only:
        local = OUT / "results_control.json"
        if not local.exists():
            local = OUT / "results_rehearsal.json"
        compare(local, Path(ARGS.compare) if ARGS.compare else ROOT / "results" / ("dev" if "rehearsal" in local.name else "prod") / "results.json",
                rehearsal="rehearsal" in local.name)
        return 0
    if not (ARGS.group or ARGS.source_url or ARGS.source_file or ARGS.rehearse):
        raise SystemExit("give --group, --source-url, --source-file, --rehearse N or --compare-only")
    ensure_endpoint()
    from cqa.db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        ids, version_id, label = prepare(db)
    finally:
        db.close()
    log(f"{label}: {len(ids)} dialogs, checklist version {version_id}, database {ARGS.db}")
    judge(ids, version_id)
    out, n_errors = export(ids, label, version_id)
    log(f"local results: {out} ({(time.time() - t0) / 60:.1f} min in total)")
    compare(out, cloud, rehearsal=label == "rehearsal")
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main())
