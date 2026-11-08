"""Evaluation runner: fans out one judge call per criterion, validates, votes, scores, persists with traces."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import SessionLocal, restore_generation
from ..ingest.normalize import NormTurn, pack_transcript
from ..models import (Appeal, AudioAsset, ChecklistVersion, Conversation, Criterion, Evaluation,
                      EvaluationCriterion, FewshotExample, LlmTrace, Setting)
from ..scoring.engine import CriterionInput, answer_points, compute_score
from .contract import Verdict
from .ensemble import arbiter_prompt, vote
from .packer import ConversationPack, CriterionPack, build_bundle
from .pii import mask_turns
from .providers import ProviderError, get_provider
from .providers.base import JudgeProvider
from ..review.service import _settled_review_state, recompute_final
from .reference import reference_facts_for
from .validate import (CONTACT_OUTSIDE_RECORDING_FLAG, apply_c7_bare_farewell_policy, c7_row_follows_policy, credit_unrecorded_opening, effective_na,
                       is_contact_criterion, opening_outside_recording, retry_instruction, unusable_reason, validate_verdict)

log = logging.getLogger("cqa.judge")
PROMPT_VERSION = "v1"
COPIED_FLAG = "copied_from_previous_version"
DECIDED_REVIEW_STATES = ("confirmed", "changed")  # review states of a settled reviewer decision (no open appeal)
LOCAL_PROVIDERS = ("local", "ollama", "openai_compatible", "vllm")
KNOWN_PROVIDERS = ("anthropic", "ensemble", "fake", *LOCAL_PROVIDERS)
EFFORTS = ("low", "medium", "high", "xhigh", "max")  # output_config.effort of the current Claude models
MAX_PARALLEL_CRITERIA = 16


class UnknownModelError(ValueError):
    """A Claude model without a configured price (settings.cost_rates): calling it would book $0 and escape the budget."""

    def __init__(self, model: str | None):
        self.model = model
        allowed = ", ".join(settings.cost_rates)
        super().__init__(f"Модель «{model}» не поддерживается: для неё не задана цена, и её расходы не попали бы в суточный "
                         f"бюджет. Допустимые модели: {allowed}.")


class TranscriptTooLong(ValueError):
    """A dialog over MAX_TRANSCRIPT_CHARS / MAX_TRANSCRIPT_TURNS: never sent to a judge."""

    def __init__(self, conversation_id: str, chars: int, turns: int):
        self.conversation_id, self.chars, self.turns = conversation_id, chars, turns
        super().__init__(f"Диалог «{conversation_id}» слишком длинный для оценки: {chars} символов и {turns} реплик "
                         f"(допускается до {settings.max_transcript_chars} символов и {settings.max_transcript_turns} реплик).")


class EvaluationAborted(RuntimeError):
    """The database changed under a running evaluation (a snapshot restore): none of its results are written."""

EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class CriterionOutcome:
    code: str
    verdict: Verdict
    traces: list[dict[str, Any]] = field(default_factory=list)
    votes: list[dict[str, Any]] | None = None
    issues: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None


def _runtime_settings(db: Session) -> dict[str, Any]:
    base = {
        "explanation_language": settings.explanation_language,
        "pii_masking": settings.pii_masking,
        "fewshot_from_reviews": settings.fewshot_from_reviews,
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
        "secondary_model": settings.secondary_model,
        "local_endpoint": settings.local_endpoint,
        "local_model": settings.local_model,
        "judge_effort": settings.judge_effort,
        "c7_bare_farewell_policy": settings.c7_bare_farewell_policy,
        "ensemble_members": [{"provider": "anthropic", "model": settings.default_model},
                             {"provider": "anthropic", "model": settings.default_model},
                             {"provider": "anthropic", "model": settings.secondary_model}],
    }
    for row in db.execute(select(Setting)).scalars():
        base[row.key] = row.value
    return base


def _to_norm_turns(conv: Conversation) -> list[NormTurn]:
    return [NormTurn(idx=t.idx, timestamp_raw=t.timestamp_raw, offset_seconds=t.offset_seconds, speaker=t.speaker,
                     text=t.text, is_duplicate=t.is_duplicate, segment=t.segment) for t in conv.turns]


def conversation_pack(db: Session, conv: Conversation, provider_name: str, rs: dict[str, Any]) -> ConversationPack:
    """What the judge sees of a conversation: turns (PII-masked unless the provider is local), structural facts and the
    acoustic summary of calls. Shared by evaluate_conversation and the single-criterion trial (checklist service)."""
    turns = _to_norm_turns(conv)
    if rs.get("pii_masking", True) and provider_name != "local":
        masked, _ = mask_turns([t.__dict__ for t in turns])
        for t, m in zip(turns, masked):
            t.text = m["text"]
    audio = db.get(AudioAsset, conv.id)
    meta = conv.meta or {}
    acoustic = audio.features if audio else None
    if acoustic is None and conv.channel == "voice call" and conv.audio_url:
        acoustic = _acoustic_from_cache(conv, audio)
    return ConversationPack(conversation_id=conv.id, channel=conv.channel, language=conv.language, turns=turns,
                            operator_start_idx=meta.get("operator_start_idx"), terminal_event=conv.terminal_event,
                            identity_confirmed_event=bool(meta.get("identity_confirmed_event")),
                            first_response_seconds=conv.first_response_seconds,
                            # a lead's manual attribution (OPR-06) is not a name the operator said: the judge gets the detected one
                            operator_name=(meta.get("operator_name_detected") if meta.get("operator_name_source") == "manual"
                                           else conv.operator_name),
                            acoustic=acoustic,
                            recording_start_idx=meta.get("recording_start_idx"),
                            not_in_recording=meta.get("not_in_recording"))


def _acoustic_from_cache(conv: Conversation, audio: AudioAsset | None) -> dict[str, Any] | None:
    """Acoustic summary of a call whose features were never computed (or were cleared after its timing was re-derived,
    cqa.ingest.renormalize): computed from the recording when it is already on disk, never downloaded here (the judge
    path must not wait on the network). Not stored: GET /api/audio/{id}/features owns the stored copy."""
    from pathlib import Path

    from ..config import settings as cfg

    candidates = [Path(audio.local_path)] if audio is not None and audio.local_path else []
    candidates.append(Path(cfg.data_dir) / "audio" / f"{conv.id}.mp3")
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        return None
    try:
        from ..audio import compute_features

        rows = [{"idx": t.idx, "speaker": t.speaker, "text": t.text, "offset_seconds": t.offset_seconds, "segment": t.segment,
                 "is_duplicate": t.is_duplicate} for t in sorted(conv.turns, key=lambda t: t.idx)]
        return compute_features(path, rows)
    except Exception as e:  # ffmpeg missing or a broken file: judge without acoustic facts, as before
        log.warning("acoustic features for %s not computed: %s", conv.id, e)
        return None


def _criterion_pack(c: Criterion) -> CriterionPack:
    from ..checklist.validation import export_ids_for

    version = getattr(c, "version", None)  # None for a transient draft criterion (single-criterion test)
    export_id = export_ids_for([c.code], getattr(version, "scoring_rules", None))[c.code]
    return CriterionPack(code=c.code, title=c.title, instruction=c.instruction, answer_type=c.answer_type,
                         options=c.options, na_allowed=c.na_allowed, na_condition=c.na_condition,
                         channel_overrides=c.channel_overrides or {}, judge_spec=c.judge_spec, is_critical=c.is_critical,
                         export_id=export_id)


def _members(rs: dict[str, Any], provider_name: str, model: str | None) -> list[JudgeProvider]:
    """The judges of a run. Runtime settings (PUT /api/settings) may choose the local endpoint and model, within the
    rules of cqa.judge.local_endpoint; the process-wide `settings` object is never modified."""
    from .local_endpoint import effective_endpoint

    endpoint = effective_endpoint(rs.get("local_endpoint"))
    local_model = str(rs.get("local_model") or settings.local_model)

    def local(name: str | None = None) -> JudgeProvider:
        extra = {"endpoint": endpoint} if endpoint else {}  # no endpoint: get_provider's own default (LOCAL_ENDPOINT)
        return get_provider("local", name or local_model, **extra)

    if provider_name == "ensemble":
        members = []
        for m in rs.get("ensemble_members") or []:
            try:
                name = str(m.get("provider") or "anthropic")
                if name.split(":", 1)[0] in LOCAL_PROVIDERS:
                    members.append(local(m.get("model")))
                elif settings.claude_enabled:
                    members.append(get_provider(name, m.get("model")))
                else:  # Claude is off on this instance: the ensemble is whatever local members remain
                    log.info("ensemble member %s skipped: Claude is disabled (CLAUDE_ENABLED=false)", name)
            except (ProviderError, AttributeError, TypeError) as e:
                log.warning("ensemble member skipped: %s", e)
        if endpoint and not any(p.name == "local" for p in members):
            try:
                members.append(local())
            except ProviderError:
                pass
        if not members:
            raise ProviderError("No ensemble members available")
        return members
    if provider_name == "fake":  # offline deterministic judge (tests, CI, keyless demos): never routed to Claude
        return [get_provider("fake", model)]
    if provider_name in LOCAL_PROVIDERS:
        return [local(model)]
    if not settings.claude_enabled:
        raise ProviderError(CLAUDE_DISABLED_MESSAGE)
    return [get_provider("anthropic", model or rs.get("default_model"))]


def require_known_models(members: list[JudgeProvider]) -> None:
    """Raise UnknownModelError for a Claude member whose model has no configured price."""
    for p in members:
        if p.name == "anthropic" and not settings.is_known_model(p.model):
            raise UnknownModelError(p.model)


def effort_of(rs: dict[str, Any]) -> str:
    value = str(rs.get("judge_effort") or "high").strip().lower()
    return value if value in EFFORTS else "high"


def parallel_criteria_of(rs: dict[str, Any]) -> int:
    try:
        value = int(rs.get("max_parallel_criteria", settings.max_parallel_criteria))
    except (TypeError, ValueError):
        value = settings.max_parallel_criteria
    return min(max(value, 1), MAX_PARALLEL_CRITERIA)


def transcript_size(conv: Conversation) -> tuple[int, int]:
    """(characters, turns) of a conversation as the judge would receive it."""
    return sum(len(t.text or "") for t in conv.turns), len(conv.turns)


def check_transcript_size(conversation_id: str, chars: int, turns: int) -> None:
    max_chars, max_turns = int(settings.max_transcript_chars or 0), int(settings.max_transcript_turns or 0)
    if (max_chars and chars > max_chars) or (max_turns and turns > max_turns):
        raise TranscriptTooLong(conversation_id, chars, turns)


def reusable_base_rows(base: Evaluation | None) -> dict[str, EvaluationCriterion]:
    """Rows of a base evaluation a partial re-evaluation copies instead of judging again: rows the judge failed on
    (flag "error") and applicable rows without an answer are judged again."""
    if base is None:
        return {}
    return {r.code: r for r in base.criteria if "error" not in (r.flags or []) and not (r.applicable and r.answer is None)}


def codes_to_judge(criteria_codes: list[str], base_rows: dict[str, Any], only_codes: set[str] | None) -> list[str]:
    """The criteria a (partial) evaluation sends to the judge; the others are copied from the base evaluation."""
    return [code for code in criteria_codes if only_codes is None or code in only_codes or code not in base_rows]


def carry_reviews(db: Session, copies: dict[int, EvaluationCriterion], *, move_open_appeals: bool) -> int:
    """Carry the reviewer's work on the base rows of a partial evaluation onto their copies (base row id → copy) and
    return the number of carried decisions. Called in the write transaction, after the judges: the base rows are read
    again, so review work done while the evaluation ran (a decision, an appeal, a withdrawal) is carried as it is now,
    and each row's decision and review state come from one read.

    - A decision (final_answer set) is carried in the settled state of the base row's decisions.
    - An open appeal moves with the verdict it disputes onto the copy when the new evaluation completed (the one the
      card, the lists and the exports show), so it is listed, decided and withdrawn there and its decision reaches
      the card: the copy is "under_review", the base row returns to the settled state of its decisions (as after a
      withdrawal). A failed evaluation leaves it on the base row, which the card keeps showing."""
    base = db.execute(select(EvaluationCriterion).where(EvaluationCriterion.id.in_(list(copies)))
                      .options(selectinload(EvaluationCriterion.appeals).selectinload(Appeal.decision))
                      .with_for_update()  # Postgres: no decision lands on these rows before the commit (SQLite: no-op)
                      .execution_options(populate_existing=True)).scalars().all()
    carried = 0
    for b in base:
        row = copies[b.id]
        if b.final_answer is not None:  # an open appeal alone changes no answer
            row.final_answer, row.final_score = b.final_answer, b.final_score
            carried += 1
        open_appeals = [a for a in b.appeals if a.status == "open"]
        if open_appeals and move_open_appeals:
            for a in open_appeals:
                a.evaluation_criterion = row
            row.review_state = "under_review"
            b.review_state = _settled_review_state(b)  # an open appeal has no decision: the moved ones change nothing
        elif b.final_answer is not None:
            row.review_state = b.review_state if b.review_state in DECIDED_REVIEW_STATES else _settled_review_state(b)
    return carried


def _failed_trace(provider: JudgeProvider, system: str, messages: list[dict[str, Any]], e: ProviderError) -> dict[str, Any]:
    """Trace of a call the provider billed but that gave no usable verdict (refusal, truncated or invalid output)."""
    usage = e.usage
    return {"provider": e.provider or provider.name, "model": e.model or provider.model,
            "request": {"system_chars": len(system), "messages": messages},
            "response": (e.raw or {}) | {"error": str(e)[:500]}, "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0, "cache_read_tokens": usage.cache_read_tokens if usage else 0,
            "cache_write_tokens": usage.cache_write_tokens if usage else 0, "cost_usd": e.cost_usd, "latency_ms": e.latency_ms,
            "failed": True}


async def _call_once(provider: JudgeProvider, system: str, messages: list[dict[str, Any]], effort: str, *,
                     traces: list[dict[str, Any]] | None = None, kind: str = "judge",
                     prompt_version: str | None = None) -> tuple[Verdict, dict[str, Any]]:
    """One provider call with retry on transient failures (connection errors, overload, rate limits).

    With `traces`, every billed attempt is appended to it as it happens, successful or not, so a caller whose call
    later fails still accounts for the cost already spent."""
    delays = (2.0, 6.0, 15.0)
    last: Exception | None = None
    for attempt, delay in enumerate((0.0,) + delays):
        if delay:
            await asyncio.sleep(delay)
        try:
            res = await provider.complete(system, messages, Verdict, effort=effort)
            break
        except ProviderError as e:
            last = e
            if traces is not None and e.billed:
                traces.append(_failed_trace(provider, system, messages, e) | {"kind": kind, "prompt_version": prompt_version})
            msg = str(e).lower()
            transient = any(k in msg for k in ("connection", "overloaded", "rate limit", "429", "529", "500", "502", "503", "504",
                                               "gateway time", "timeout", "timed out", "invalid json"))
            # a local model at temperature 0 repeats a timeout or broken JSON: one retry, not three (each can cost minutes)
            repeat_prone = getattr(provider, "name", "") == "local" and any(k in msg for k in ("timeout", "timed out", "time-out",
                                                                                              "invalid json"))
            if not transient or attempt == len(delays) or (repeat_prone and attempt >= 1):
                raise
            log.warning("%s/%s transient error (attempt %d): %s", provider.name, provider.model, attempt + 1, e)
    else:  # pragma: no cover
        raise last or ProviderError("provider failed")
    cost = provider.cost(res.usage) if hasattr(provider, "cost") else 0.0
    trace = {"provider": res.provider, "model": res.model, "request": {"system_chars": len(system), "messages": messages},
             "response": res.raw | {"parsed": res.parsed}, "input_tokens": res.usage.input_tokens,
             "output_tokens": res.usage.output_tokens, "cache_read_tokens": res.usage.cache_read_tokens,
             "cache_write_tokens": res.usage.cache_write_tokens, "cost_usd": cost, "latency_ms": res.latency_ms, "kind": kind}
    if prompt_version is not None:
        trace["prompt_version"] = prompt_version
    if traces is not None:
        traces.append(trace)
    v = Verdict.model_validate(res.parsed)
    return v, trace


async def judge_one(provider: JudgeProvider, conv: ConversationPack, crit: CriterionPack, *, explanation_language: str,
                    fewshots: list[dict[str, Any]], reference_facts: list[str], effort: str,
                    traces: list[dict[str, Any]] | None = None) -> tuple[Verdict, list[dict[str, Any]], list[str]]:
    """Judge one criterion with one provider (plus one validation retry). `traces` (owned by the caller) receives every
    billed call as it happens, so the cost of a first call survives a retry that fails."""
    bundle = build_bundle(conv, crit, explanation_language=explanation_language, fewshots=fewshots,
                          reference_facts=reference_facts, prompt_version=PROMPT_VERSION)
    traces = traces if traces is not None else []
    pv = f"{PROMPT_VERSION}:{bundle.prompt_hash}"
    v, tr = await _call_once(provider, bundle.system, bundle.messages, effort, traces=traces, kind="judge", prompt_version=pv)
    v.criterion_code = crit.code
    val = validate_verdict(v, conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
    issues = list(val.issues)
    if val.needs_retry:
        messages = bundle.messages + [
            {"role": "assistant", "content": [{"type": "text", "text": json.dumps(v.model_dump(), ensure_ascii=False)}]},
            {"role": "user", "content": [{"type": "text", "text": retry_instruction(val.issues)}]},
        ]
        v2, _tr2 = await _call_once(provider, bundle.system, messages, effort, traces=traces, kind="retry", prompt_version=pv)
        v2.criterion_code = crit.code
        val2 = validate_verdict(v2, conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
        issues += [f"retry: {i}" for i in val2.issues]
        if not val2.needs_retry:
            val = val2
        else:
            # keep a usable verdict with the flag; one that would store an N/A the checklist forbids here or an answer
            # outside the options fails instead: results.json would get a null or an unknown value (an invalid file at
            # the control hour), while a failed criterion is exported as a status=error record (a valid file) and
            # scripts/control_run.py re-runs such dialogs once (not_completed → evaluate_all)
            first_bad = unusable_reason(val.verdict, crit, conv.channel)
            second_bad = unusable_reason(val2.verdict, crit, conv.channel)
            if first_bad and second_bad:
                raise ProviderError(f"verdict still invalid after the retry: {second_bad}")
            if first_bad:
                val = val2
            val.verdict.flags.append("validation_failed")
            issues.append("retry still invalid; keeping the usable verdict with flag")
    return val.verdict, traces, issues


async def judge_criterion(members: list[JudgeProvider], conv: ConversationPack, crit: CriterionPack, *, rs: dict[str, Any],
                          fewshots: list[dict[str, Any]], reference_facts: list[str]) -> CriterionOutcome:
    effort = effort_of(rs)
    lang = rs.get("explanation_language", "ru")
    outcome = CriterionOutcome(code=crit.code, verdict=Verdict(criterion_code=crit.code, applicable=True, answer="", confidence=0, explanation=""))
    if conv.channel == "voice call" and is_contact_criterion(crit):
        gap = opening_outside_recording(conv.turns)
        if gap is not None:  # decided by rule: no model call, no cost, identical on every run (cqa.judge.validate)
            outcome.verdict = credit_unrecorded_opening(outcome.verdict, crit, gap)
            log.debug("%s decided by rule: operator opening (turn %s) %s before the recording start (turn %s)", crit.code,
                      gap.op.idx, "begins" if gap.partial else "ends", gap.start.idx)
            return outcome
    member_traces: list[list[dict[str, Any]]] = [[] for _ in members]
    policy = rs.get("c7_bare_farewell_policy")
    try:
        results = await asyncio.gather(*[
            judge_one(p, conv, crit, explanation_language=lang, fewshots=fewshots, reference_facts=reference_facts, effort=effort,
                      traces=member_traces[i])
            for i, p in enumerate(members)
        ], return_exceptions=True)
        verdicts: list[tuple[str, str, Verdict]] = []
        for p, r, traces in zip(members, results, member_traces):
            outcome.traces += traces  # billed calls count even when this member failed
            if isinstance(r, Exception):
                outcome.issues.append(f"{p.name}/{p.model}: {r}")
                continue
            v, _traces, issues = r
            # the C7 policy decides each member's value before the vote, so the vote, the disagreement check and the
            # arbiter all work on policy values: one member's bare_farewell flag must not decide the dialog
            v = apply_c7_bare_farewell_policy(v, crit, policy, conv.channel, terminal=conv.terminal_event, turns=conv.turns)
            verdicts.append((p.name, p.model, v))
            outcome.issues += [f"{p.name}: {i}" for i in issues]
        if not verdicts:
            raise ProviderError("all judges failed: " + "; ".join(outcome.issues))
        out = vote(verdicts, crit)
        merged = out.merged
        if len(verdicts) > 1:
            outcome.votes = out.votes
        if out.disagreement and len(verdicts) > 1:
            arb = members[0]
            bundle = build_bundle(conv, crit, explanation_language=lang, fewshots=fewshots, reference_facts=reference_facts, prompt_version=PROMPT_VERSION)
            messages = bundle.messages + [{"role": "user", "content": [{"type": "text", "text": arbiter_prompt(crit, verdicts)}]}]
            try:
                av, _atr = await _call_once(arb, bundle.system, messages, effort, traces=outcome.traces, kind="arbiter",
                                            prompt_version=f"{PROMPT_VERSION}:{bundle.prompt_hash}")
                av.criterion_code = crit.code
                aval = validate_verdict(av, conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
                if not aval.needs_retry:
                    merged = aval.verdict
                    merged.flags.append("arbiter_decided")
            except Exception as e:  # keep the vote
                outcome.issues.append(f"arbiter failed: {e}")
        merged = apply_c7_bare_farewell_policy(merged, crit, policy, conv.channel, terminal=conv.terminal_event, turns=conv.turns)
        outcome.verdict = merged
        outcome.cost_usd = sum(t.get("cost_usd", 0.0) for t in outcome.traces)
    except Exception as e:
        outcome.error = str(e)
        outcome.cost_usd = sum(t.get("cost_usd", 0.0) for t in outcome.traces)
    return outcome


def _load_fewshots(db: Session, code: str, language: str, channel: str, enabled: bool) -> list[dict[str, Any]]:
    if not enabled:
        return []
    rows = db.execute(select(FewshotExample).where(FewshotExample.criterion_code == code, FewshotExample.channel == channel)
                      .order_by(FewshotExample.language == language, FewshotExample.id.desc()).limit(3)).scalars().all()
    return [{"transcript_excerpt": r.transcript_excerpt, "verdict": r.verdict} for r in rows]


async def evaluate_conversation(conversation_id: str, checklist_version_id: int, *, provider_name: str = "anthropic",
                                model: str | None = None, mode: str = "full", base_evaluation_id: int | None = None,
                                only_codes: set[str] | None = None, job_id: str | None = None,
                                on_event: EventCallback | None = None) -> int:
    """Run a full or partial evaluation and return the new evaluation id.

    - A dialog over the transcript limits, or a Claude model without a configured price, fails before anything is
      written (TranscriptTooLong / UnknownModelError).
    - No database connection is held while the judges run (minutes with Claude): everything the judges need is loaded
      first and the read transaction is ended before the calls start.
    - Before writing results the evaluation checks that the database was not replaced meanwhile (a snapshot restore in
      this process bumps cqa.db.restore_generation; in any process the row must still be this dialog's running row):
      otherwise nothing is written (EvaluationAborted), so a restored row with the same id is never overwritten.
    - A partial evaluation copies the criteria it does not judge from the base evaluation with the reviewer's work on
      them as it is when the results are written (carry_reviews): decisions, and open appeals, which move with the
      copied verdicts into the new evaluation.
    """
    from ..jobs import ledger

    t0 = time.perf_counter()
    generation = restore_generation()
    ev_id: int | None = None
    db = SessionLocal()
    try:
        rs = _runtime_settings(db)
        conv = db.execute(select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.turns))).scalar_one()
        version = db.execute(select(ChecklistVersion).where(ChecklistVersion.id == checklist_version_id).options(selectinload(ChecklistVersion.criteria))).scalar_one()
        check_transcript_size(conv.id, *transcript_size(conv))
        pack = conversation_pack(db, conv, provider_name, rs)
        turns = pack.turns
        members = _members(rs, provider_name, model)
        require_known_models(members)
        ev = Evaluation(conversation_id=conv.id, checklist_version_id=version.id, status="running", provider=provider_name,
                        mode=("partial" if mode == "partial" else ("ensemble" if provider_name == "ensemble" else "single")),
                        model=",".join(f"{p.name}:{p.model}" for p in members), prompt_version=PROMPT_VERSION, job_id=job_id)
        db.add(ev)
        db.commit()
        db.refresh(ev)
        ev_id = ev.id
        if on_event:
            on_event("evaluation.started", {"conversation_id": conv.id, "evaluation_id": ev.id})

        base_rows: dict[str, EvaluationCriterion] = {}
        if mode == "partial" and base_evaluation_id:
            # rows the judge failed on in the base evaluation are judged again instead of being copied as failures
            base_rows = reusable_base_rows(db.get(Evaluation, base_evaluation_id))

        transcript_text = pack_transcript(turns)
        criteria = list(version.criteria)
        # rows a partial run must decide again instead of copying:
        # - an N/A this version no longer allows on the dialog's channel (C1 on calls since 2026-11-08): results.json
        #   would refuse the copied null;
        # - the contact criterion of a call whose opening the recording missed, unless the row is already the rule's:
        #   an older model verdict («Нет» for a greeting nobody can hear) must not survive; the rule needs no model call
        # - a C7 row flagged bare_farewell whose value no longer follows c7_bare_farewell_policy (switched in Settings)
        gap = opening_outside_recording(turns) if conv.channel == "voice call" else None
        for c in criteria:
            b = base_rows.get(c.code)
            if b is None:
                continue
            cp = _criterion_pack(c)
            stale_na = not b.applicable and not effective_na(cp, conv.channel)[0]
            stale_contact = (gap is not None and is_contact_criterion(cp)
                             and CONTACT_OUTSIDE_RECORDING_FLAG not in (b.flags or []))
            # a bare-farewell C7 row decided under another c7_bare_farewell_policy (the Settings switch)
            stale_c7 = cp.code == "C7" and not c7_row_follows_policy(b.applicable, b.answer, b.flags, rs.get("c7_bare_farewell_policy"),
                                                                     conv.terminal_event,
                                                                     na_allowed=effective_na(cp, conv.channel)[0])
            if stale_na or stale_contact or stale_c7:
                del base_rows[c.code]
        judged = set(codes_to_judge([c.code for c in criteria], base_rows, only_codes))
        to_judge = [c for c in criteria if c.code in judged]
        fewshot_on = rs.get("fewshot_from_reviews", True)
        prepared = {c.code: (_criterion_pack(c), _load_fewshots(db, c.code, conv.language, conv.channel, fewshot_on),
                             reference_facts_for(transcript_text, c.code)) for c in to_judge}
        for r in base_rows.values():  # load what the copy step reads before the connection is released
            _ = (r.answer, r.claims, r.votes, r.final_answer)
        db.commit()  # end the read transaction: the pooled connection goes back while the judges run
        sem = asyncio.Semaphore(parallel_criteria_of(rs))

        async def run(c: Criterion) -> CriterionOutcome:
            async with sem:
                cp, fewshots, reference = prepared[c.code]
                out = await judge_criterion(members, pack, cp, rs=rs, fewshots=fewshots, reference_facts=reference)
                if on_event:
                    on_event("criterion.done", {"conversation_id": conv.id, "code": c.code,
                                                "answer": out.verdict.answer if out.verdict.applicable else "N/A", "error": out.error})
                return out

        with ledger.context(kind="judge", ref=f"evaluation:{ev_id}" + (f" job:{job_id}" if job_id else "")):
            outcomes = await asyncio.gather(*[run(c) for c in to_judge])
        by_code = {o.code: o for o in outcomes}
        _ensure_still_ours(db, ev, generation)

        # persist criteria, traces, score
        inputs: list[CriterionInput] = []
        rows: list[EvaluationCriterion] = []
        total_cost = 0.0
        in_tok = out_tok = 0
        errors = []
        copies: dict[int, EvaluationCriterion] = {}  # base row id → its copy (partial mode)
        for c in criteria:
            o = by_code.get(c.code)
            if o is None and c.code in base_rows:  # copied from base evaluation (partial mode)
                b = base_rows[c.code]
                row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=b.applicable, na_reason=b.na_reason,
                                          answer=b.answer, score=b.score, max_points=c.max_points, weight=c.weight, confidence=b.confidence,
                                          explanation=b.explanation, evidence=b.evidence, checked_span=b.checked_span,
                                          flags=[f for f in (b.flags or []) if f != COPIED_FLAG] + [COPIED_FLAG], claims=b.claims, votes=b.votes)
                copies[b.id] = row  # the reviewer's work on b is carried when the rows are written (carry_reviews)
            else:
                assert o is not None
                if o.error:
                    errors.append(f"{c.code}: {o.error}")
                    row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=True, answer=None, score=None,
                                              max_points=c.max_points, weight=c.weight, explanation=f"Ошибка оценки: {o.error}", flags=["error"])
                else:
                    v = o.verdict
                    pts = answer_points(c.options, v.answer) if v.applicable else None
                    row = EvaluationCriterion(
                        evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=v.applicable, na_reason=(v.na_reason or None),
                        answer=(v.answer if v.applicable else None), score=pts, max_points=c.max_points, weight=c.weight,
                        confidence=v.confidence, explanation=v.explanation,
                        evidence=[e.model_dump() | {"offset_seconds": next((t.offset_seconds for t in turns if t.idx == e.turn_idx), None)} for e in v.evidence],
                        checked_span=(v.checked_span.model_dump() if v.checked_span else None),
                        flags=v.flags + ([f"issues:{len(o.issues)}"] if o.issues else []), claims=[cl.model_dump() for cl in v.claims], votes=o.votes)
                for t in o.traces:
                    total_cost += t.get("cost_usd", 0.0)
                    in_tok += t.get("input_tokens", 0)
                    out_tok += t.get("output_tokens", 0)
                    db.add(LlmTrace(evaluation_id=ev.id, criterion_code=c.code, kind=t.get("kind", "judge"), provider=t["provider"], model=t["model"],
                                    prompt_version=t.get("prompt_version"), request=t["request"], response=t["response"],
                                    input_tokens=t.get("input_tokens", 0), output_tokens=t.get("output_tokens", 0),
                                    cache_read_tokens=t.get("cache_read_tokens", 0), cost_usd=t.get("cost_usd", 0.0), latency_ms=t.get("latency_ms", 0)))
            rows.append(row)
            critical_hit = bool(c.is_critical and row.applicable and row.answer is not None and str(row.answer).lower() == str(c.critical_value or "yes").lower())
            inputs.append(CriterionInput(code=c.code, weight=c.weight, max_points=c.max_points, points=row.score,
                                         applicable=bool(row.applicable and row.score is not None), section=c.section,
                                         is_critical=c.is_critical, critical_hit=critical_hit))

        score = compute_score(version.scoring_rules or {}, inputs, version.sections or [])
        for row in rows:
            cr = score.criteria.get(row.code)
            row.weight_applied = cr.weight_applied if cr else 0.0
            row.earned = cr.earned if cr else 0.0
            db.add(row)
        titles = {s["code"]: s.get("title") for s in (version.sections or [])}
        ev.sections = [{"code": s.code, "title": titles.get(s.code), "score": s.score, "max": s.max} for s in score.sections]
        ev.total_score = score.total
        ev.final_total_score = score.total
        ev.critical_error = score.critical
        ev.cost_usd = round(total_cost, 6)
        ev.input_tokens = in_tok
        ev.output_tokens = out_tok
        ev.latency_ms = int((time.perf_counter() - t0) * 1000)
        ev.completed_at = datetime.now(timezone.utc)
        ev.status = "failed" if errors and len(errors) == len(to_judge) else "completed"
        ev.error = "; ".join(errors) if errors else None
        if copies:
            db.flush()  # the first write takes SQLite's write lock: no request can change the base rows until the commit
            if carry_reviews(db, copies, move_open_appeals=(ev.status == "completed")):
                recompute_final(db, ev)  # final_total_score / final_earned / sections follow the carried decisions
        if restore_generation() != generation:  # a restore started while the rows were being prepared
            raise EvaluationAborted("database restored from a snapshot while the evaluation ran")
        db.commit()
        if on_event:
            on_event("evaluation.done", {"conversation_id": conv.id, "evaluation_id": ev.id, "total_score": ev.total_score,
                                         "critical_error": ev.critical_error, "status": ev.status, "cost_usd": ev.cost_usd})
        return ev.id
    except EvaluationAborted as e:
        db.rollback()
        log.warning("evaluation of %s not written: %s", conversation_id, e)
        if on_event:
            on_event("evaluation.failed", {"conversation_id": conversation_id, "error": str(e)})
        raise
    except Exception as e:
        db.rollback()
        log.exception("evaluation failed for %s", conversation_id)
        _mark_failed(db, ev_id, conversation_id, generation, e)
        if on_event:
            on_event("evaluation.failed", {"conversation_id": conversation_id, "error": str(e)})
        raise
    finally:
        db.close()


def _ensure_still_ours(db: Session, ev: Evaluation, generation: int) -> None:
    """Raise EvaluationAborted unless the database is the one the evaluation started on and its row is still this
    dialog's running evaluation (a restore reuses row ids: the id may now belong to another dialog's evaluation)."""
    if restore_generation() != generation:
        raise EvaluationAborted("database restored from a snapshot while the evaluation ran")
    row = db.execute(select(Evaluation.conversation_id, Evaluation.status, Evaluation.job_id).where(Evaluation.id == ev.id)).first()
    if row is None or row[0] != ev.conversation_id or row[1] != "running" or row[2] != ev.job_id:
        raise EvaluationAborted(f"evaluation {ev.id} is no longer this dialog's running evaluation (database replaced or recovered)")


def _mark_failed(db: Session, ev_id: int | None, conversation_id: str, generation: int, error: Exception) -> None:
    """Mark the evaluation's own row failed (never another running evaluation of the dialog, never after a restore)."""
    if ev_id is None or restore_generation() != generation:
        return
    try:
        ev = db.get(Evaluation, ev_id)
        if ev is not None and ev.conversation_id == conversation_id and ev.status == "running":
            ev.status = "failed"
            ev.error = str(error)[:2000]
            ev.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


FREE_PROVIDERS = ("local", "ollama", "openai_compatible", "vllm", "fake")
CLAUDE_DISABLED_MESSAGE = "Claude недоступен на этом сервере (CLAUDE_ENABLED=false): выберите локальную модель, provider local."


def claude_available() -> bool:
    """A Claude call can be made here: a key is configured and Claude is not switched off (CLAUDE_ENABLED)."""
    return bool(settings.anthropic_api_key) and bool(settings.claude_enabled)


def estimate_cost(n_chats: int, n_calls: int, provider_name: str, members: int = 1) -> float:
    """Historical flat rate in USD ($0.18 per chat, $0.35 per call; ensemble of three: $0.38 / $0.75). It is only the
    floor of the real estimate now: runs, resumes and the Dynamics ingest are priced per dialog by cqa.jobs.estimate
    (criteria, transcript length, ensemble members, model rates). Local models and the offline fake judge cost 0."""
    if provider_name in FREE_PROVIDERS:
        return 0.0
    per_chat = {"anthropic": 0.18, "ensemble": 0.38}.get(provider_name, 0.18)
    per_call = {"anthropic": 0.35, "ensemble": 0.75}.get(provider_name, 0.35)
    return round(n_chats * per_chat + n_calls * per_call, 2)
