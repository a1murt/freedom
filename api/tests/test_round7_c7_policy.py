"""C7 bare-farewell policy hardening after the review of PR #1 (2026-11-08): the checklist's terminal-event N/A wins over
the policy, an ensemble applies the policy to every member before the vote, unknown policy values fall back to the default
"na", the rule is idempotent, a policy "no" cites the closing turn, and a partial re-run does not copy a C7 row decided
under another policy. No network: stub judges."""
from __future__ import annotations

import asyncio
import json

import pytest

from cqa.config import settings
from cqa.ingest.normalize import NormTurn
from cqa.judge import runner
from cqa.judge.contract import ProviderResult, Usage, Verdict
from cqa.judge.packer import ConversationPack, CriterionPack
from cqa.judge.providers.base import ProviderError
from cqa.judge.validate import apply_c7_bare_farewell_policy, c7_row_follows_policy, normalize_c7_policy

SEED = json.loads((settings.seeds_dir / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
SPECS = json.loads((settings.seeds_dir / "judge_specs_v1.json").read_text(encoding="utf-8"))


def c7() -> CriterionPack:
    c = next(c for c in SEED["criteria"] if c["code"] == "C7")
    return CriterionPack(code="C7", title=c["title"], instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"],
                         na_allowed=c["na_allowed"], na_condition=c["na_condition"], channel_overrides=c["channel_overrides"] or {},
                         judge_spec=SPECS.get("C7"))


def turns(terminal_text: str | None = None) -> list[NormTurn]:
    rows = [("client", "Где посмотреть тариф?"), ("operator", "Здравствуйте! Тариф указан в приложении, раздел «Тарифы»."),
            ("client", "Нашла, спасибо"), ("operator", "Пожалуйста. Хорошего вечера!")]
    if terminal_text:
        rows.append(("system", terminal_text))
    return [NormTurn(idx=i, timestamp_raw="", offset_seconds=None, speaker=s, text=t) for i, (s, t) in enumerate(rows)]


def pack(terminal: str | None = None, terminal_text: str | None = None) -> ConversationPack:
    return ConversationPack(conversation_id="x", channel="agent chat", language="ru", turns=turns(terminal_text), operator_start_idx=1,
                            terminal_event=terminal, identity_confirmed_event=False, first_response_seconds=None, operator_name=None)


def V(applicable: bool, answer: str, conf: float, flags: list[str], na_reason: str = "", evidence: bool = True) -> dict:
    ev = [{"turn_idx": 3, "speaker": "operator", "quote": "Пожалуйста. Хорошего вечера!", "role": "supports"}] if evidence else []
    return {"criterion_code": "C7", "applicable": applicable, "answer": answer, "confidence": conf, "na_reason": na_reason,
            "evidence": ev, "explanation": "stub", "flags": flags}


class Stub:
    def __init__(self, model: str, verdict: dict):
        self.name, self.model, self.verdict = "anthropic", model, verdict

    async def complete(self, system, messages, schema, *, effort="high", max_tokens=4096):
        if "Independent judges disagree" in json.dumps(messages[-1], ensure_ascii=False):
            raise ProviderError("arbiter unavailable (stub)")  # the vote stands
        return ProviderResult(parsed=dict(self.verdict), usage=Usage(), raw={}, model=self.model, provider=self.name, latency_ms=1)

    def cost(self, usage):
        return 0.0


def judge(members: list[Stub], conv: ConversationPack, policy) -> Verdict:
    out = asyncio.run(runner.judge_criterion(members, conv, c7(), rs={"c7_bare_farewell_policy": policy, "explanation_language": "ru"},
                                             fewshots=[], reference_facts=[]))
    assert out.error is None, out.error
    return out.verdict


@pytest.mark.parametrize("policy", ["na", "yes", "no"])
def test_terminal_event_na_wins_over_the_policy(policy):
    v = judge([Stub("opus", V(True, "yes", 0.9, ["bare_farewell"]))], pack("connection_lost", "Соединение с клиентом прервано"), policy)
    assert not v.applicable and "rule_na_terminal_event" in v.flags and not any(f.startswith("rule_c7_bare_farewell") for f in v.flags)
    # a model N/A on such a chat is kept too (the hard rule only fires on an applicable verdict)
    v = judge([Stub("opus", V(False, "", 0.9, ["bare_farewell"], na_reason="Соединение прервано."))],
              pack("client_left", "Клиент покинул чат."), policy)
    assert not v.applicable and v.na_reason == "Соединение прервано."


def test_ensemble_applies_the_policy_to_every_member_before_the_vote():
    # two members see a bare farewell, the most confident one does not: the policy value of the majority wins
    members = [Stub("opus", V(True, "yes", 0.6, ["bare_farewell"])), Stub("opus", V(True, "yes", 0.6, ["bare_farewell"])),
               Stub("sonnet", V(True, "yes", 0.95, []))]
    assert not judge(members, pack(), "na").applicable
    # all three answer N/A, two flag the bare farewell, the most confident does not: under "yes" the majority is Да
    members = [Stub("opus", V(False, "", 0.8, ["bare_farewell"], na_reason="прощание без итога")),
               Stub("opus", V(False, "", 0.8, ["bare_farewell"], na_reason="прощание без итога")),
               Stub("sonnet", V(False, "", 0.9, [], na_reason="прощание без итога"))]
    assert (judge(members, pack(), "yes").answer, judge(members, pack(), "no").answer) == ("yes", "no")
    # one flagged member among three applicable ones no longer turns a Да majority into N/A
    members = [Stub("opus", V(True, "yes", 0.9, ["bare_farewell"])), Stub("opus", V(True, "yes", 0.5, [])),
               Stub("sonnet", V(True, "yes", 0.5, []))]
    v = judge(members, pack(), "na")
    assert v.applicable and v.answer == "yes"


@pytest.mark.parametrize("raw,want", [("na", "na"), ("N/A", "na"), (" n/a ", "na"), ("NA", "na"), ("", "na"), (None, "na"),
                                      ("yes", "yes"), ("Yes", "yes"), ("no", "no"), (" No ", "no"),
                                      ("maybe", "na"), ("нет", "na"), ("n.a.", "na"), (True, "na"), (1, "na"), (["na"], "na")])
def test_unknown_policy_values_fall_back_to_the_default(raw, want):
    assert normalize_c7_policy(raw) == want
    v = judge([Stub("opus", V(False, "", 0.9, ["bare_farewell"], na_reason="прощание без итога"))], pack(), raw)
    assert (v.applicable, v.answer) == ((False, "") if want == "na" else (True, want))


@pytest.mark.parametrize("policy", ["na", "yes", "no"])
def test_the_rule_is_idempotent(policy):
    v = Verdict(**V(True, "yes", 0.9, ["bare_farewell"]))
    once = apply_c7_bare_farewell_policy(v.model_copy(deep=True), c7(), policy, "agent chat", turns=turns())
    twice = apply_c7_bare_farewell_policy(once.model_copy(deep=True), c7(), policy, "agent chat", turns=turns())
    assert twice.model_dump() == once.model_dump()
    assert len(twice.flags) == len(set(twice.flags))


def test_policy_no_cites_the_closing_turn_when_the_model_gave_no_evidence():
    v = judge([Stub("opus", V(False, "", 0.9, ["bare_farewell"], na_reason="прощание без итога", evidence=False))], pack(), "no")
    assert v.answer == "no" and [e.turn_idx for e in v.evidence] == [3] and "[3]" in v.explanation


@pytest.mark.parametrize("applicable,answer,flags,policy,terminal,follows", [
    (False, None, ["bare_farewell"], "na", None, True),
    (False, None, ["bare_farewell"], "yes", None, False),          # switched to yes: the copied N/A is stale
    (True, "yes", ["bare_farewell", "rule_c7_bare_farewell_yes"], "yes", None, True),
    (True, "yes", ["bare_farewell", "rule_c7_bare_farewell_yes"], "no", None, False),
    (True, "yes", ["bare_farewell", "rule_c7_bare_farewell_yes"], "na", None, False),
    (True, "no", [], "na", None, True),                           # not a bare farewell: the policy has no say
    (False, None, ["bare_farewell", "rule_na_terminal_event"], "yes", None, True),
    (False, None, ["bare_farewell"], "yes", "client_left", True),
])
def test_partial_runs_re_judge_c7_rows_decided_under_another_policy(applicable, answer, flags, policy, terminal, follows):
    assert c7_row_follows_policy(applicable, answer, flags, policy, terminal) is follows
