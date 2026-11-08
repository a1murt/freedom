"""scripts/control_run.py rehearsal guards: no paid rehearsal on the jury instance by accident, and a rehearsal never
re-evaluates the live-demo dialogs (docs/DEMO.md) or dialogs with appeals or decisions (the seeded ones)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cr():
    spec = importlib.util.spec_from_file_location("control_run_round3", ROOT / "scripts" / "control_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_live_demo_list_matches_readiness(cr):
    from cqa.platform.readiness import LIVE_DEMO

    assert tuple(cr.LIVE_DEMO) == tuple(LIVE_DEMO)


def test_hosted_rehearsal_needs_the_explicit_flag_and_sends_nothing(cr, monkeypatch):
    def no_requests(*a, **k):
        raise AssertionError("no request may be sent before the guard")

    monkeypatch.setattr(cr.httpx, "Client", no_requests)
    monkeypatch.setattr(sys, "argv", ["control_run.py", "--rehearse"])
    with pytest.raises(SystemExit) as e:
        cr.main()
    assert "--allow-hosted-rehearsal" in str(e.value)


def test_rehearsal_picks_skip_the_live_demo_and_reviewed_dialogs(cr):
    convs = [{"id": f"p1-ru-{i:03d}", "review_state": "none"} for i in range(1, 31)]
    convs += [{"id": f"p1-kz-{i:03d}", "review_state": "none"} for i in range(1, 16)]
    convs += [{"id": f"p1-en-{i:03d}", "review_state": "none"} for i in range(1, 16)]
    convs += [{"id": "p4-ru-001", "review_state": "none"}]  # calls are not rehearsal material
    for c in convs:
        if c["id"] == "p1-kz-005":
            c["review_state"] = "under_review"
        if c["id"] == "p1-en-007":
            c["review_state"] = "changed"
    ids = cr.rehearsal_ids(convs)
    assert len(ids) == 10 and len(set(ids)) == 10
    assert not set(ids) & {"p1-ru-029", "p1-ru-005", "p1-kz-005", "p1-en-007", "p4-ru-001"}
    assert {i[3:5] for i in ids} == {"ru", "kz", "en"}  # spread over the three languages
    # whatever the order, a protected dialog is never picked
    assert not set(cr.rehearsal_ids(list(reversed(convs)))) & {"p1-ru-029", "p1-ru-005", "p1-kz-005", "p1-en-007"}


def test_a_timed_out_or_refused_run_never_stops_the_export(cr, monkeypatch):
    calls: list[list[str]] = []

    def run_job(http, base, ids, version_id, provider, **kw):
        calls.append(list(ids))
        raise SystemExit("evaluation job timed out" if len(calls) == 1 else "evaluations/run failed: HTTP 429 budget_exceeded")

    monkeypatch.setattr(cr, "run_job", run_job)
    monkeypatch.setattr(cr, "not_completed", lambda http, base, ids, vid: [i for i in ids if i in ("c2", "c7")])
    bad = cr.evaluate_all(None, "http://api.test", ["c1", "c2", "c7"], 1, "anthropic")
    assert bad == ["c2", "c7"]  # returned for the status=error export instead of SystemExit
    assert calls == [["c1", "c2", "c7"], ["c2", "c7"]]  # the full run, then one retry of the unfinished dialogs


def test_only_incomplete_evaluates_just_the_unfinished_dialogs(cr, monkeypatch):
    calls: list[list[str]] = []
    done: set[str] = {"c1", "c3"}

    def run_job(http, base, ids, version_id, provider, **kw):
        calls.append(list(ids))
        done.update(ids)

    monkeypatch.setattr(cr, "run_job", run_job)
    monkeypatch.setattr(cr, "not_completed", lambda http, base, ids, vid: [i for i in ids if i not in done])
    assert cr.evaluate_all(None, "http://api.test", ["c1", "c2", "c3", "c4"], 1, "anthropic", only_incomplete=True) == []
    assert calls == [["c2", "c4"]]
    calls.clear()
    assert cr.evaluate_all(None, "http://api.test", ["c1", "c2"], 1, "anthropic", only_incomplete=True) == [] and calls == []



def test_job_timeout_is_longer_for_the_local_model():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    try:
        import control_run as cr
    finally:
        sys.path.pop(0)
    assert cr.job_timeout_for("local") == 3000 and cr.job_timeout_for("anthropic") == 1800
    assert cr.job_timeout_for("ensemble") == 1800 and cr.job_timeout_for("local", 900) == 900


def test_the_retry_uses_the_given_provider_when_claude_is_off(cr, monkeypatch):
    seen: list[str] = []

    def run_job(http, base, ids, version_id, provider, **kw):
        seen.append(provider)

    monkeypatch.setattr(cr, "run_job", run_job)
    monkeypatch.setattr(cr, "not_completed", lambda http, base, ids, vid: ["c2"])
    cr.evaluate_all(None, "http://api.test", ["c1", "c2"], 1, "local", retry_provider="local")
    assert seen == ["local", "local"]
    seen.clear()
    cr.evaluate_all(None, "http://api.test", ["c1", "c2"], 1, "ensemble")  # Claude available: single Opus retry as before
    assert seen == ["ensemble", "anthropic"]


def test_local_repeat_prone_errors_get_one_retry(monkeypatch):
    import asyncio

    from cqa.judge import runner as jr
    from cqa.judge.providers.base import ProviderError

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(jr.asyncio, "sleep", no_sleep)

    class P:
        def __init__(self, name, msg):
            self.name, self.model, self.msg, self.calls = name, "m", msg, 0

        async def complete(self, *a, **k):
            self.calls += 1
            raise ProviderError(self.msg)

    for name, msg, want in (("local", "Local model returned invalid JSON: x", 2), ("local", "Local provider connection error: ReadTimeout", 2),
                            ("local", "Local provider connection error: ConnectError refused", 4), ("anthropic", "Anthropic API error 503", 4),
                            ("local", "Local provider HTTP 504: 504 Gateway Time-out", 2)):
        p = P(name, msg)
        try:
            asyncio.run(jr._call_once(p, "sys", [], "high"))
        except ProviderError:
            pass
        assert p.calls == want, (name, msg, p.calls)


def test_deadline_cuts_the_retry_when_no_time_is_left(cr, monkeypatch):
    import time as _t

    calls: list[tuple[list[str], int | None]] = []

    def run_job(http, base, ids, version_id, provider, timeout_s=None, **kw):
        calls.append((list(ids), timeout_s))
        raise SystemExit("evaluation job timed out")

    monkeypatch.setattr(cr, "run_job", run_job)
    monkeypatch.setattr(cr, "not_completed", lambda http, base, ids, vid: ["c2"])
    deadline = _t.time() + cr.EXPORT_MARGIN_S + 120  # 2 minutes of job time left: the first job is bounded, no retry
    bad = cr.evaluate_all(None, "http://api.test", ["c1", "c2"], 1, "local", deadline=deadline)
    assert bad == ["c2"] and len(calls) == 1 and calls[0][1] <= 125
    assert cr.deadline_epoch(None) is None and cr.deadline_epoch("13:57") > 0
