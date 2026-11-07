"""Builds the system prompt and messages for one criterion judgment (docs/ARCHITECTURE.md §7.2)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..ingest.normalize import NormTurn, pack_transcript

LANG_NAMES = {"ru": "Russian", "en": "English", "kk": "Kazakh", "kz": "Kazakh"}


@dataclass
class ConversationPack:
    conversation_id: str
    channel: str
    language: str
    turns: list[NormTurn]
    operator_start_idx: int | None
    terminal_event: str | None
    identity_confirmed_event: bool
    first_response_seconds: float | None
    operator_name: str | None
    acoustic: dict[str, Any] | None = None


@dataclass
class CriterionPack:
    code: str
    title: str
    instruction: str
    answer_type: str
    options: list[dict[str, Any]]
    na_allowed: bool
    na_condition: str | None
    channel_overrides: dict[str, Any]
    judge_spec: dict[str, Any] | None
    is_critical: bool = False


@dataclass
class PromptBundle:
    system: str
    messages: list[dict[str, Any]]
    prompt_version: str
    prompt_hash: str
    meta: dict[str, Any] = field(default_factory=dict)


def load_prompt(version: str, name: str) -> str:
    return (Path(settings.prompts_dir) / version / f"{name}.md").read_text(encoding="utf-8")


def _allowed_answers(c: CriterionPack) -> str:
    if c.answer_type == "binary":
        labels = ", ".join(f'"{o["value"]}" ({o.get("label", "")})' for o in c.options)
        return f"binary: {labels}"
    steps = ", ".join(f'"{o["value"]}"' for o in c.options)
    return f"scale, one of: {steps}"


def _anchors_block(c: CriterionPack) -> str:
    if c.answer_type != "scale":
        return ""
    lines = ["## Scale anchors"]
    for o in c.options:
        lines.append(f'- "{o["value"]}": {o.get("anchor") or o.get("label", "")}')
    return "\n".join(lines) + "\n"


def _structural_facts(conv: ConversationPack) -> str:
    facts = {
        "conversation_id": conv.conversation_id,
        "channel": conv.channel,
        "language": conv.language,
        "operator_start_idx": conv.operator_start_idx,
        "operator_name_detected": conv.operator_name,
        "terminal_event": conv.terminal_event,
        "identity_confirmed_event": conv.identity_confirmed_event,
        "first_response_seconds": conv.first_response_seconds,
    }
    if conv.acoustic:
        facts["acoustic"] = {k: conv.acoustic.get(k) for k in ("duration_s", "operator_wpm", "long_pauses_count", "talk_ratio_operator", "hold_segments", "summary_ru") if k in conv.acoustic}
    return json.dumps(facts, ensure_ascii=False, indent=0)


def _fewshot_block(fewshots: list[dict[str, Any]]) -> str:
    if not fewshots:
        return ""
    lines = ["## Reference judgments (same criterion, similar dialogs)"]
    for i, fs in enumerate(fewshots[:3], 1):
        v = fs.get("verdict", {})
        lines.append(f"{i}. Excerpt: {fs.get('transcript_excerpt', '')[:600]}")
        lines.append(f"   Verdict: applicable={v.get('applicable', True)}, answer={v.get('answer')}; {v.get('explanation', '')}")
    return "\n".join(lines) + "\n"


def _reference_block(reference_facts: list[str]) -> str:
    if not reference_facts:
        return ""
    lines = ["## Reference facts (company knowledge for checking operator claims; DEV-ledger facts are authoritative, public facts are supplementary)"]
    lines += [f"- {f}" for f in reference_facts[:25]]
    return "\n".join(lines) + "\n"


def build_bundle(conv: ConversationPack, crit: CriterionPack, *, explanation_language: str = "ru",
                 fewshots: list[dict[str, Any]] | None = None, reference_facts: list[str] | None = None,
                 prompt_version: str = "v1") -> PromptBundle:
    system = load_prompt(prompt_version, "system")
    template = load_prompt(prompt_version, "criterion")
    spec = crit.judge_spec or {}

    override = (crit.channel_overrides or {}).get(conv.channel, {})
    channel_instruction = f"Channel adaptation ({conv.channel}): {override['instruction']}\n" if override.get("instruction") else ""
    ov_na = bool(override.get("na_allowed"))
    na_allowed = crit.na_allowed or ov_na
    na_condition = (override.get("na_condition") if ov_na else None) or crit.na_condition
    na_rule = (f"allowed. Condition: {na_condition}" if na_allowed else "not allowed for this criterion; answer must be yes/no or a scale step")
    steps = spec.get("decision_steps") or [
        "Locate the relevant operator turns.",
        "Check the instruction literally against them.",
        "Map the finding to the allowed answers.",
    ]
    steps_block = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    na_block = ""
    if spec.get("na_conditions"):
        na_block = "N/A conditions:\n" + "\n".join(f"- {s}" for s in spec["na_conditions"]) + "\n"
    pitfalls_block = ""
    if spec.get("pitfalls"):
        pitfalls_block = "Pitfalls to avoid:\n" + "\n".join(f"- {s}" for s in spec["pitfalls"]) + "\n"
    channel_notes_block = ""
    note = (spec.get("channel_notes") or {}).get(conv.channel)
    if note:
        channel_notes_block = f"Channel note ({conv.channel}): {note}\n"
    examples = ""
    if spec.get("pass_examples") or spec.get("fail_examples"):
        examples = "Examples of pass: " + " | ".join(spec.get("pass_examples", [])) + "\nExamples of fail: " + " | ".join(spec.get("fail_examples", [])) + "\n"

    criterion_text = template.format(
        code=crit.code,
        title=crit.title,
        instruction=crit.instruction,
        channel_instruction=channel_instruction,
        allowed_answers=_allowed_answers(crit),
        na_rule=na_rule,
        anchors_block=_anchors_block(crit),
        definition=spec.get("definition") or crit.instruction,
        steps_block=steps_block,
        na_block=na_block,
        evidence_requirement=spec.get("evidence_requirement") or "Quote the turn(s) that justify the answer.",
        pitfalls_block=pitfalls_block + examples,
        channel_notes_block=channel_notes_block,
        reference_block=_reference_block(reference_facts or []),
        fewshot_block=_fewshot_block(fewshots or []),
        explanation_language=LANG_NAMES.get(explanation_language, explanation_language),
    )

    transcript_text = pack_transcript(conv.turns)
    user0 = ("## Structural facts\n" + _structural_facts(conv) + "\n\n## Transcript\n" + transcript_text)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": user0, "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Transcript received. Which criterion should I judge?"}]},
        {"role": "user", "content": [{"type": "text", "text": criterion_text}]},
    ]
    h = hashlib.sha256((system + criterion_text).encode("utf-8")).hexdigest()[:12]
    return PromptBundle(system=system, messages=messages, prompt_version=prompt_version, prompt_hash=h,
                        meta={"criterion_chars": len(criterion_text), "transcript_chars": len(transcript_text)})
