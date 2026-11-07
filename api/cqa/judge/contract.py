"""Judge contract: the structured verdict every provider must return. See docs/ML_BRIEF.md."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    turn_idx: int = Field(description="Index of the transcript turn, as shown in square brackets")
    speaker: Literal["client", "operator", "bot", "system"]
    quote: str = Field(description="Verbatim fragment of that turn in its original language, at most 300 characters")
    role: Literal["supports", "contradicts", "context"] = "supports"


class Claim(BaseModel):
    turn_idx: int
    claim: str = Field(description="A factual statement the operator made about products, fees, menu paths, timelines or policy")
    status: Literal["consistent", "contradicted", "unverified"] = "unverified"
    note: str = ""


class CheckedSpan(BaseModel):
    from_turn: int
    to_turn: int


class Verdict(BaseModel):
    """Structured output of one criterion judgment."""

    criterion_code: str
    applicable: bool = Field(description="false only when N/A is allowed for this criterion and its stated N/A condition holds")
    na_reason: str = Field(default="", description="Required when applicable is false: which N/A condition is met and why")
    answer: str = Field(description='For binary criteria exactly "yes" or "no"; for scale criteria the chosen step as a number string, e.g. "15"; empty string when not applicable')
    confidence: float = Field(ge=0, le=1, description="How certain the judgment is, 0..1")
    evidence: list[Evidence] = Field(default_factory=list, description="Turns that justify the answer; at least one for a negative or reduced answer unless checked_span is given")
    checked_span: CheckedSpan | None = Field(default=None, description="For a missed action: the range of turns where it was expected and not found")
    explanation: str = Field(description="1-3 sentences in the requested explanation language, in the organizer's justification style")
    step_results: list[str] = Field(default_factory=list, description="One short line per decision step: what was checked and what was found")
    flags: list[str] = Field(default_factory=list, description="Machine-readable notes such as possible_factual_claim, late_turn, asr_noise, hold_etiquette_ok")
    claims: list[Claim] = Field(default_factory=list)


class JudgeSpec(BaseModel):
    """Compiled decision procedure for one criterion (docs/ARCHITECTURE.md §5.2)."""

    definition: str
    decision_steps: list[str]
    na_conditions: list[str] = Field(default_factory=list)
    evidence_requirement: str = ""
    pass_examples: list[str] = Field(default_factory=list)
    fail_examples: list[str] = Field(default_factory=list)
    channel_notes: dict[str, str] = Field(default_factory=dict)
    pitfalls: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ProviderResult(BaseModel):
    parsed: dict[str, Any]
    usage: Usage
    raw: dict[str, Any]
    model: str
    provider: str
    latency_ms: int
    stop_reason: str | None = None
