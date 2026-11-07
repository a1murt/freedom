"""Rubric compiler: criterion fields → JudgeSpec (docs/ARCHITECTURE.md §5.2). LLM-backed with a deterministic fallback.

Each LLM compilation is a paid Claude call (secondary model): it is booked in the spend ledger (kind "compile"), and the
callers check the daily budget first and reserve COMPILE_ESTIMATE_USD per criterion while it runs (checklist create /
version / import fall back to the deterministic spec when the budget is exhausted; the explicit regenerate answers 429).
At most COMPILE_CONCURRENCY compilations run at once.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..config import settings
from ..judge.contract import JudgeSpec
from ..judge.packer import load_prompt

log = logging.getLogger("cqa.compiler")

COMPILE_ESTIMATE_USD = 0.03  # one compilation with the secondary model (≈1.5k input, ≈2.5k output tokens incl. thinking)
COMPILE_CONCURRENCY = 4

JUDGE_RELEVANT_FIELDS = ("title", "instruction", "answer_type", "options", "na_allowed", "na_condition", "channel_overrides", "is_critical", "critical_value")


def fallback_spec(crit: dict[str, Any]) -> dict[str, Any]:
    opts = crit.get("options", [])
    if crit.get("answer_type") == "scale":
        mapping = "; ".join(f'{o["value"]} — {o.get("anchor") or o.get("label")}' for o in opts)
        last = f"Сопоставьте находки со шкалой: {mapping}."
    else:
        last = "Да — условие из инструкции выполнено полностью; Нет — действие требовалось, но пропущено или выполнено неверно."
    steps = [
        "Найдите реплики оператора, относящиеся к этому критерию (начиная с первой реплики оператора).",
        f"Проверьте буквально: {crit.get('instruction', '')}",
    ]
    if crit.get("na_allowed"):
        steps.append(f"Проверьте условие N/A: {crit.get('na_condition') or 'критерий неприменим по условию чек-листа'}. Если выполнено — N/A с обоснованием.")
    steps.append(last)
    return JudgeSpec(
        definition=crit.get("instruction", ""),
        decision_steps=steps,
        na_conditions=[crit["na_condition"]] if crit.get("na_allowed") and crit.get("na_condition") else [],
        evidence_requirement="Процитируйте реплику оператора, подтверждающую ответ; для пропущенного действия укажите диапазон реплик, где оно ожидалось; для N/A — что подтверждает условие.",
        channel_notes={k: v.get("instruction", "") for k, v in (crit.get("channel_overrides") or {}).items() if v.get("instruction")},
        pitfalls=["Не смешивать с другими критериями.", "Не считать русские названия пунктов меню в KZ/EN диалогах ошибкой.", "Не путать «Нет» с N/A."],
    ).model_dump()


def llm_compiles() -> bool:
    """True when compile_spec_async makes a paid LLM call (an API key is configured)."""
    return bool(settings.anthropic_api_key)


async def compile_spec_async(crit: dict[str, Any]) -> dict[str, Any]:
    """Compile one criterion. The caller is responsible for the budget check (see the module docstring)."""
    if not llm_compiles():
        return fallback_spec(crit)
    try:
        from ..jobs import ledger
        from ..judge.providers.anthropic_provider import AnthropicProvider

        prompt = load_prompt("v1", "compiler").format(criterion_json=json.dumps({k: crit.get(k) for k in JUDGE_RELEVANT_FIELDS}, ensure_ascii=False, indent=1))
        provider = AnthropicProvider(settings.secondary_model)
        with ledger.context(kind="compile", ref=f"criterion:{crit.get('code')}"):
            res = await provider.complete("You compile QA criteria into decision procedures. Return only the JudgeSpec JSON.",
                                          [{"role": "user", "content": [{"type": "text", "text": prompt}]}], JudgeSpec, effort="medium", max_tokens=4096)
        spec = JudgeSpec.model_validate(res.parsed).model_dump()
        spec["_compiled_by"] = res.model
        return spec
    except Exception as e:  # never block saving a checklist
        log.warning("judge_spec compilation failed, using fallback: %s", e)
        return fallback_spec(crit)


async def compile_many_async(crits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compile several criteria with at most COMPILE_CONCURRENCY calls in flight."""
    sem = asyncio.Semaphore(COMPILE_CONCURRENCY)

    async def one(c: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await compile_spec_async(c)

    return list(await asyncio.gather(*(one(c) for c in crits)))


def run_in_fresh_loop(make_coro: Any) -> Any:
    """asyncio.run(make_coro()) in this thread, or in a helper thread when an event loop already runs here. The loop's
    Claude client is closed before the loop ends (its pooled connections must never be reused from another loop)."""
    async def wrapped() -> Any:
        try:
            return await make_coro()
        finally:
            from ..judge.providers.anthropic_provider import aclose_loop_client

            await aclose_loop_client()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(wrapped())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as ex:  # called from inside an event loop
        return ex.submit(lambda: asyncio.run(wrapped())).result()


def compile_spec(crit: dict[str, Any], db: Any = None) -> dict[str, Any]:
    """Compile one criterion now (POST …/judge-spec/{code}/regenerate). With an API key this is a paid call: the daily
    budget is checked first and the estimate reserved while it runs; cqa.jobs.guards.GuardError (429) when exhausted."""
    if not llm_compiles():
        return fallback_spec(crit)
    from ..jobs import guards

    own = db is None
    if own:
        from ..db import SessionLocal

        db = SessionLocal()
    try:
        with guards.budget_reservation(db, COMPILE_ESTIMATE_USD):
            return run_in_fresh_loop(lambda: compile_spec_async(crit))
    finally:
        if own:
            db.close()


def judge_relevant_signature(crit: dict[str, Any]) -> str:
    return json.dumps({k: crit.get(k) for k in JUDGE_RELEVANT_FIELDS}, ensure_ascii=False, sort_keys=True)
