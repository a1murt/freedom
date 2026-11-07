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
from ..db import SessionLocal
from ..ingest.normalize import NormTurn, pack_transcript
from ..models import (AudioAsset, ChecklistVersion, Conversation, Criterion, Evaluation, EvaluationCriterion,
                      FewshotExample, LlmTrace, Setting)
from ..scoring.engine import CriterionInput, answer_points, compute_score
from .contract import Verdict
from .ensemble import arbiter_prompt, vote
from .packer import ConversationPack, CriterionPack, build_bundle
from .pii import mask_turns
from .providers import ProviderError, get_provider
from .providers.base import JudgeProvider
from .reference import reference_facts_for
from .validate import retry_instruction, validate_verdict

log = logging.getLogger("cqa.judge")
PROMPT_VERSION = "v1"

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


def _criterion_pack(c: Criterion) -> CriterionPack:
    return CriterionPack(code=c.code, title=c.title, instruction=c.instruction, answer_type=c.answer_type,
                         options=c.options, na_allowed=c.na_allowed, na_condition=c.na_condition,
                         channel_overrides=c.channel_overrides or {}, judge_spec=c.judge_spec, is_critical=c.is_critical)


def _members(rs: dict[str, Any], provider_name: str, model: str | None) -> list[JudgeProvider]:
    # runtime settings (DB) may override the local endpoint/model from the environment
    if rs.get("local_endpoint"):
        settings.local_endpoint = rs["local_endpoint"]
    if rs.get("local_model"):
        settings.local_model = rs["local_model"]
    if provider_name == "ensemble":
        members = []
        for m in rs.get("ensemble_members", []):
            try:
                members.append(get_provider(m["provider"], m.get("model")))
            except ProviderError as e:
                log.warning("ensemble member skipped: %s", e)
        if rs.get("local_endpoint") and not any(p.name == "local" for p in members):
            try:
                members.append(get_provider("local", rs.get("local_model")))
            except ProviderError:
                pass
        if not members:
            raise ProviderError("No ensemble members available")
        return members
    if provider_name in ("local", "ollama", "openai_compatible", "vllm"):
        return [get_provider("local", model or rs.get("local_model"))]
    return [get_provider("anthropic", model or rs.get("default_model"))]


async def _call_once(provider: JudgeProvider, system: str, messages: list[dict[str, Any]], effort: str) -> tuple[Verdict, dict[str, Any]]:
    """One provider call with retry on transient failures (connection errors, overload, rate limits)."""
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
            msg = str(e).lower()
            transient = any(k in msg for k in ("connection", "overloaded", "rate limit", "429", "529", "500", "502", "503", "timeout", "timed out"))
            if not transient or attempt == len(delays):
                raise
            log.warning("%s/%s transient error (attempt %d): %s", provider.name, provider.model, attempt + 1, e)
    else:  # pragma: no cover
        raise last or ProviderError("provider failed")
    v = Verdict.model_validate(res.parsed)
    cost = provider.cost(res.usage) if hasattr(provider, "cost") else 0.0
    trace = {"provider": res.provider, "model": res.model, "request": {"system_chars": len(system), "messages": messages},
             "response": res.raw | {"parsed": res.parsed}, "input_tokens": res.usage.input_tokens,
             "output_tokens": res.usage.output_tokens, "cache_read_tokens": res.usage.cache_read_tokens,
             "cost_usd": cost, "latency_ms": res.latency_ms}
    return v, trace


async def judge_one(provider: JudgeProvider, conv: ConversationPack, crit: CriterionPack, *, explanation_language: str,
                    fewshots: list[dict[str, Any]], reference_facts: list[str], effort: str) -> tuple[Verdict, list[dict[str, Any]], list[str]]:
    bundle = build_bundle(conv, crit, explanation_language=explanation_language, fewshots=fewshots,
                          reference_facts=reference_facts, prompt_version=PROMPT_VERSION)
    traces: list[dict[str, Any]] = []
    v, tr = await _call_once(provider, bundle.system, bundle.messages, effort)
    tr["prompt_version"] = f"{PROMPT_VERSION}:{bundle.prompt_hash}"
    tr["kind"] = "judge"
    traces.append(tr)
    v.criterion_code = crit.code
    val = validate_verdict(v, conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
    issues = list(val.issues)
    if val.needs_retry:
        messages = bundle.messages + [
            {"role": "assistant", "content": [{"type": "text", "text": json.dumps(v.model_dump(), ensure_ascii=False)}]},
            {"role": "user", "content": [{"type": "text", "text": retry_instruction(val.issues)}]},
        ]
        v2, tr2 = await _call_once(provider, bundle.system, messages, effort)
        tr2["prompt_version"] = tr["prompt_version"]
        tr2["kind"] = "retry"
        traces.append(tr2)
        v2.criterion_code = crit.code
        val2 = validate_verdict(v2, conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
        issues += [f"retry: {i}" for i in val2.issues]
        if not val2.needs_retry:
            val = val2
        else:
            val.verdict.flags.append("validation_failed")
            issues.append("retry still invalid; keeping first verdict with flag")
    return val.verdict, traces, issues


async def judge_criterion(members: list[JudgeProvider], conv: ConversationPack, crit: CriterionPack, *, rs: dict[str, Any],
                          fewshots: list[dict[str, Any]], reference_facts: list[str]) -> CriterionOutcome:
    effort = rs.get("judge_effort", "high")
    lang = rs.get("explanation_language", "ru")
    outcome = CriterionOutcome(code=crit.code, verdict=Verdict(criterion_code=crit.code, applicable=True, answer="", confidence=0, explanation=""))
    try:
        results = await asyncio.gather(*[
            judge_one(p, conv, crit, explanation_language=lang, fewshots=fewshots, reference_facts=reference_facts, effort=effort)
            for p in members
        ], return_exceptions=True)
        verdicts: list[tuple[str, str, Verdict]] = []
        for p, r in zip(members, results):
            if isinstance(r, Exception):
                outcome.issues.append(f"{p.name}/{p.model}: {r}")
                continue
            v, traces, issues = r
            verdicts.append((p.name, p.model, v))
            outcome.traces += traces
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
                av, atr = await _call_once(arb, bundle.system, messages, effort)
                atr["kind"] = "arbiter"
                atr["prompt_version"] = f"{PROMPT_VERSION}:{bundle.prompt_hash}"
                outcome.traces.append(atr)
                av.criterion_code = crit.code
                aval = validate_verdict(av, conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
                if not aval.needs_retry:
                    merged = aval.verdict
                    merged.flags.append("arbiter_decided")
            except Exception as e:  # keep the vote
                outcome.issues.append(f"arbiter failed: {e}")
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
    """Run a full or partial evaluation and return the new evaluation id."""
    t0 = time.perf_counter()
    db = SessionLocal()
    try:
        rs = _runtime_settings(db)
        conv = db.execute(select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.turns))).scalar_one()
        version = db.execute(select(ChecklistVersion).where(ChecklistVersion.id == checklist_version_id).options(selectinload(ChecklistVersion.criteria))).scalar_one()
        audio = db.get(AudioAsset, conversation_id)
        turns = _to_norm_turns(conv)
        if rs.get("pii_masking", True) and provider_name != "local":
            masked, _ = mask_turns([t.__dict__ for t in turns])
            for t, m in zip(turns, masked):
                t.text = m["text"]
        pack = ConversationPack(conversation_id=conv.id, channel=conv.channel, language=conv.language, turns=turns,
                                operator_start_idx=(conv.meta or {}).get("operator_start_idx"), terminal_event=conv.terminal_event,
                                identity_confirmed_event=bool((conv.meta or {}).get("identity_confirmed_event")),
                                first_response_seconds=conv.first_response_seconds, operator_name=conv.operator_name,
                                acoustic=(audio.features if audio else None))
        members = _members(rs, provider_name, model)
        ev = Evaluation(conversation_id=conv.id, checklist_version_id=version.id, status="running", provider=provider_name,
                        mode=("partial" if mode == "partial" else ("ensemble" if provider_name == "ensemble" else "single")),
                        model=",".join(f"{p.name}:{p.model}" for p in members), prompt_version=PROMPT_VERSION, job_id=job_id)
        db.add(ev)
        db.commit()
        db.refresh(ev)
        if on_event:
            on_event("evaluation.started", {"conversation_id": conv.id, "evaluation_id": ev.id})

        base_rows: dict[str, EvaluationCriterion] = {}
        if mode == "partial" and base_evaluation_id:
            base = db.get(Evaluation, base_evaluation_id)
            if base:
                base_rows = {r.code: r for r in base.criteria}

        transcript_text = pack_transcript(turns)
        criteria = list(version.criteria)
        to_judge = [c for c in criteria if (only_codes is None or c.code in only_codes or c.code not in base_rows)]
        sem = asyncio.Semaphore(int(rs.get("max_parallel_criteria", settings.max_parallel_criteria)))

        async def run(c: Criterion) -> CriterionOutcome:
            async with sem:
                cp = _criterion_pack(c)
                out = await judge_criterion(members, pack, cp, rs=rs,
                                            fewshots=_load_fewshots(db, c.code, conv.language, conv.channel, rs.get("fewshot_from_reviews", True)),
                                            reference_facts=reference_facts_for(transcript_text, c.code))
                if on_event:
                    on_event("criterion.done", {"conversation_id": conv.id, "code": c.code,
                                                "answer": out.verdict.answer if out.verdict.applicable else "N/A", "error": out.error})
                return out

        outcomes = await asyncio.gather(*[run(c) for c in to_judge])
        by_code = {o.code: o for o in outcomes}

        # persist criteria, traces, score
        inputs: list[CriterionInput] = []
        rows: list[EvaluationCriterion] = []
        total_cost = 0.0
        in_tok = out_tok = 0
        errors = []
        for c in criteria:
            o = by_code.get(c.code)
            if o is None and c.code in base_rows:  # copied from base evaluation (partial mode)
                b = base_rows[c.code]
                row = EvaluationCriterion(evaluation_id=ev.id, criterion_id=c.id, code=c.code, applicable=b.applicable, na_reason=b.na_reason,
                                          answer=b.answer, score=b.score, max_points=c.max_points, weight=c.weight, confidence=b.confidence,
                                          explanation=b.explanation, evidence=b.evidence, checked_span=b.checked_span,
                                          flags=list(b.flags or []) + ["copied_from_previous_version"], claims=b.claims, votes=b.votes)
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
        db.commit()
        if on_event:
            on_event("evaluation.done", {"conversation_id": conv.id, "evaluation_id": ev.id, "total_score": ev.total_score,
                                         "critical_error": ev.critical_error, "status": ev.status, "cost_usd": ev.cost_usd})
        return ev.id
    except Exception as e:
        db.rollback()
        log.exception("evaluation failed for %s", conversation_id)
        try:
            ev_fail = db.execute(select(Evaluation).where(Evaluation.conversation_id == conversation_id, Evaluation.status == "running").order_by(Evaluation.id.desc())).scalars().first()
            if ev_fail:
                ev_fail.status = "failed"
                ev_fail.error = str(e)
                ev_fail.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
        if on_event:
            on_event("evaluation.failed", {"conversation_id": conversation_id, "error": str(e)})
        raise
    finally:
        db.close()


def estimate_cost(n_chats: int, n_calls: int, provider_name: str, members: int = 1) -> float:
    per_chat = {"anthropic": 0.18, "local": 0.0, "ensemble": 0.38}.get(provider_name, 0.18)
    per_call = {"anthropic": 0.35, "local": 0.0, "ensemble": 0.75}.get(provider_name, 0.35)
    return round(n_chats * per_chat + n_calls * per_call, 2)
