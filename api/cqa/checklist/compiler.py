"""Rubric compiler: criterion fields → JudgeSpec (docs/ARCHITECTURE.md §5.2). LLM-backed with a deterministic fallback."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..config import settings
from ..judge.contract import JudgeSpec
from ..judge.packer import load_prompt

log = logging.getLogger("cqa.compiler")

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


async def compile_spec_async(crit: dict[str, Any]) -> dict[str, Any]:
    if not settings.anthropic_api_key:
        return fallback_spec(crit)
    try:
        from ..judge.providers.anthropic_provider import AnthropicProvider

        prompt = load_prompt("v1", "compiler").format(criterion_json=json.dumps({k: crit.get(k) for k in JUDGE_RELEVANT_FIELDS}, ensure_ascii=False, indent=1))
        provider = AnthropicProvider(settings.secondary_model)
        res = await provider.complete("You compile QA criteria into decision procedures. Return only the JudgeSpec JSON.",
                                      [{"role": "user", "content": [{"type": "text", "text": prompt}]}], JudgeSpec, effort="medium", max_tokens=4096)
        spec = JudgeSpec.model_validate(res.parsed).model_dump()
        spec["_compiled_by"] = res.model
        return spec
    except Exception as e:  # never block saving a checklist
        log.warning("judge_spec compilation failed, using fallback: %s", e)
        return fallback_spec(crit)


def compile_spec(crit: dict[str, Any]) -> dict[str, Any]:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # called from inside an event loop (FastAPI sync route runs in a threadpool, so normally not the case)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(lambda: asyncio.run(compile_spec_async(crit))).result()
    return asyncio.run(compile_spec_async(crit))


def judge_relevant_signature(crit: dict[str, Any]) -> str:
    return json.dumps({k: crit.get(k) for k in JUDGE_RELEVANT_FIELDS}, ensure_ascii=False, sort_keys=True)
