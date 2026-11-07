"""Cost estimate of an evaluation run, per dialog, from what the worker will actually send to the judges.

Used by POST /api/evaluations/run (and dry runs), POST /api/jobs/{id}/resume and the Dynamics 365 ingest; the daily
budget guard reserves it while the job runs.

Per dialog:
- which criteria are judged follows the worker exactly (cqa.jobs.runner): a partial run copies rows only from the
  dialog's latest completed evaluation on the base version, and only rows the judge did not fail on; a dialog without
  such an evaluation (or a partial run without a base version) is judged in full;
- each judged criterion costs one call per paid ensemble member (plus a share for the arbiter): uncached criterion
  block + cached system prompt + the transcript (written to the prompt cache by each parallel call, 1.25x input) +
  output tokens, at the member model's rates; calibrated on the stored Claude traces of the DEV set (chat ≈ $0.24,
  call ≈ $0.46 per dialog with Claude Opus 5.5);
- never below the historical flat rate scaled to the judged share ($0.18 per chat, $0.35 per call, ensemble
  $0.38 / $0.75 for three members), and at least MIN_DIALOG_USD when anything is judged by a paid model.

A dialog over MAX_TRANSCRIPT_CHARS / MAX_TRANSCRIPT_TURNS is reported in ``too_long`` and not estimated (it is not run).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import CACHE_WRITE_MULTIPLIER, settings
from ..judge import runner as judge_runner
from ..judge.providers import ProviderError
from ..models import ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion, Turn

FREE_PROVIDERS = judge_runner.FREE_PROVIDERS
SYSTEM_PROMPT_TOKENS = 3_100       # cached system prompt, read at the cache-read rate
CRITERION_OVERHEAD_TOKENS = 300    # criterion template around the instruction and judge spec
CRITERION_CHARS_PER_TOKEN = 2.2
TRANSCRIPT_CHARS_PER_TOKEN = 1.8   # packed Russian/Kazakh transcript lines (measured ≈2.1; kept conservative)
TURN_OVERHEAD_CHARS = 30           # "[12] operator (01:15) <pre>: "
TRANSCRIPT_OVERHEAD_CHARS = 400    # structural facts block
VOICE_EXTRA_TOKENS = 800           # acoustic summary of calls
OUTPUT_TOKENS = {"agent chat": 1_000, "voice call": 1_600}
RETRY_FACTOR = 1.05                # validation retries
ARBITER_SHARE = 0.3                # ensemble disagreements resolved by one more call
MIN_DIALOG_USD = 0.01


@dataclass
class RunEstimate:
    total_usd: float
    paid: bool
    dialogs: int = 0
    judged_dialogs: int = 0
    model_calls: int = 0
    members: list[str] = field(default_factory=list)
    rejudge_codes: list[str] | None = None
    too_long: list[dict[str, Any]] = field(default_factory=list)
    per_dialog: dict[str, float] = field(default_factory=dict)

    @property
    def free(self) -> bool:
        """No paid model call will be made: free judges only, or nothing to judge (a weight-only partial re-evaluation
        copies every row). Only such runs skip the daily budget check."""
        return not self.paid or self.model_calls == 0

    def detail(self) -> dict[str, Any]:
        """Compact explanation for API answers (`estimate`)."""
        top = max(self.per_dialog.values(), default=0.0)
        return {"method": "tokens", "paid": self.paid, "members": self.members, "dialogs": self.dialogs,
                "judged_dialogs": self.judged_dialogs, "model_calls": self.model_calls, "max_dialog_usd": round(top, 4)}


def _criterion_chars(c: Criterion) -> int:
    def size(value: Any) -> int:
        if not value:
            return 0
        return len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False))

    return size(c.instruction) + size(c.na_condition) + size(c.channel_overrides) + size(c.judge_spec) + size(c.title)


def call_cost(model: str, criterion_chars: int, transcript_tokens: float, channel: str) -> float:
    """USD of one judge call with `model` on one criterion."""
    rin, rout, rcache = settings.rate_for(model)
    crit_tokens = CRITERION_OVERHEAD_TOKENS + criterion_chars / CRITERION_CHARS_PER_TOKEN
    out_tokens = OUTPUT_TOKENS.get(channel, OUTPUT_TOKENS["agent chat"])
    return (crit_tokens * rin + SYSTEM_PROMPT_TOKENS * rcache + transcript_tokens * rin * CACHE_WRITE_MULTIPLIER
            + out_tokens * rout) / 1_000_000


def transcript_tokens(chars: int, turns: int, channel: str) -> float:
    tokens = (chars + TURN_OVERHEAD_CHARS * turns + TRANSCRIPT_OVERHEAD_CHARS) / TRANSCRIPT_CHARS_PER_TOKEN
    return tokens + (VOICE_EXTRA_TOKENS if channel == "voice call" else 0)


def _flat_dialog(provider: str, channel: str, paid_members: int) -> float:
    is_call = channel == "voice call"
    base = judge_runner.estimate_cost(0 if is_call else 1, 1 if is_call else 0, "ensemble" if provider == "ensemble" else "anthropic")
    if provider == "ensemble" and paid_members > 3:
        base *= paid_members / 3
    return base


def _sizes(db: Session, ids: list[str]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for cid, chars, n in db.execute(select(Turn.conversation_id, func.coalesce(func.sum(func.length(Turn.text)), 0), func.count(Turn.id))
                                        .where(Turn.conversation_id.in_(chunk)).group_by(Turn.conversation_id)).all():
            out[cid] = (int(chars or 0), int(n or 0))
    return out


def _base_rows(db: Session, ids: list[str], base_version_id: int) -> dict[str, set[str]]:
    """{conversation id: codes reusable from its latest completed evaluation on the base version} (same rule as the worker)."""
    latest: dict[str, int] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for ev_id, cid in db.execute(select(Evaluation.id, Evaluation.conversation_id)
                                     .where(Evaluation.conversation_id.in_(chunk), Evaluation.checklist_version_id == base_version_id,
                                            Evaluation.status == "completed").order_by(Evaluation.id)).all():
            latest[cid] = ev_id  # ascending ids: the last one wins, like base_eval_for()
    out: dict[str, set[str]] = {cid: set() for cid in latest}
    by_eval = {ev_id: cid for cid, ev_id in latest.items()}
    ev_ids = list(by_eval)
    for start in range(0, len(ev_ids), 500):
        chunk = ev_ids[start:start + 500]
        for ev_id, code, applicable, answer, flags in db.execute(
                select(EvaluationCriterion.evaluation_id, EvaluationCriterion.code, EvaluationCriterion.applicable,
                       EvaluationCriterion.answer, EvaluationCriterion.flags).where(EvaluationCriterion.evaluation_id.in_(chunk))).all():
            if "error" in (flags or []) or (applicable and answer is None):
                continue
            out[by_eval[ev_id]].add(code)
    return out


def resolve_rejudge_codes(db: Session, version: ChecklistVersion, mode: str, base_version_id: int | None,
                          only_codes: list[str] | None) -> list[str] | None:
    """The codes a partial run re-judges: given ones, else the diff against the base version (as the run always did)."""
    if mode == "partial" and base_version_id and not only_codes:
        from ..checklist import service as svc

        base = svc.get_version(db, base_version_id)
        if base is not None:
            return svc.diff_versions(base, version)["rejudge_codes"]
    return only_codes


def members_for(db: Session, provider: str, model: str | None, rs: dict[str, Any] | None = None) -> list[Any]:
    """The judge members the worker will build (UnknownModelError for an unpriced Claude model; [] when none resolve)."""
    rs = rs if rs is not None else judge_runner._runtime_settings(db)
    try:
        members = judge_runner._members(rs, provider, model)
    except ProviderError:
        return []
    judge_runner.require_known_models(members)
    return members


def estimate_run(db: Session, convs: list[Conversation], version: ChecklistVersion, *, provider: str, model: str | None,
                 mode: str = "full", base_version_id: int | None = None, only_codes: list[str] | None = None,
                 rs: dict[str, Any] | None = None) -> RunEstimate:
    members = members_for(db, provider, model, rs)
    paid_models = [p.model for p in members if p.name == "anthropic"]
    paid = bool(paid_models) or (not members and provider not in FREE_PROVIDERS)
    if not members and paid:
        paid_models = [settings.default_model]
    rejudge = resolve_rejudge_codes(db, version, mode, base_version_id, only_codes)
    codes_filter = set(rejudge) if rejudge is not None else None
    criteria = list(version.criteria)
    codes = [c.code for c in criteria]
    chars_by_code = {c.code: _criterion_chars(c) for c in criteria}
    ids = [c.id for c in convs]
    sizes = _sizes(db, ids)
    bases = _base_rows(db, ids, base_version_id) if mode == "partial" and base_version_id else {}

    est = RunEstimate(total_usd=0.0, paid=paid, dialogs=len(convs), rejudge_codes=rejudge,
                      members=[f"{p.name}:{p.model}" for p in members])
    total = 0.0
    for conv in convs:
        chars, turns = sizes.get(conv.id, (0, 0))
        try:
            judge_runner.check_transcript_size(conv.id, chars, turns)
        except judge_runner.TranscriptTooLong:
            est.too_long.append({"conversation_id": conv.id, "reason": "transcript_too_long", "chars": chars, "turns": turns})
            continue
        if conv.id in bases:  # partial with a base evaluation: only these codes are judged
            judged = judge_runner.codes_to_judge(codes, dict.fromkeys(bases[conv.id]), codes_filter)
        else:
            judged = list(codes)
        if judged:
            est.judged_dialogs += 1
            est.model_calls += len(judged) * max(len(members), 1)
        if not paid or not judged:
            est.per_dialog[conv.id] = 0.0
            continue
        tokens = transcript_tokens(chars, turns, conv.channel)
        usd = 0.0
        for code in judged:
            per_criterion = sum(call_cost(m, chars_by_code[code], tokens, conv.channel) for m in paid_models)
            if len(members) > 1 and members[0].name == "anthropic":
                per_criterion += ARBITER_SHARE * call_cost(members[0].model, chars_by_code[code], tokens, conv.channel)
            usd += per_criterion * RETRY_FACTOR
        floor = _flat_dialog(provider, conv.channel, len(paid_models)) * len(judged) / max(len(codes), 1)
        usd = max(usd, floor, MIN_DIALOG_USD)
        est.per_dialog[conv.id] = usd
        total += usd
    est.total_usd = round(total, 2) if total <= 0 or round(total, 2) > 0 else MIN_DIALOG_USD
    return est
