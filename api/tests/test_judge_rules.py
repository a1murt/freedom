"""Deterministic judge rules applied after the model verdict (cqa/judge/validate.py)."""
from __future__ import annotations

import json

import pytest

from cqa.config import settings
from cqa.judge.contract import Evidence, Verdict
from cqa.judge.packer import CriterionPack
from cqa.judge.validate import apply_c7_bare_farewell_policy


def _c7() -> CriterionPack:
    seed = json.loads((settings.seeds_dir / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
    c = next(c for c in seed["criteria"] if c["code"] == "C7")
    return CriterionPack(code="C7", title=c["title"], instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"],
                         na_allowed=c["na_allowed"], na_condition=c["na_condition"], channel_overrides=c["channel_overrides"],
                         judge_spec=None, is_critical=False)


def _verdict(answer: str = "yes", flags: list[str] | None = None) -> Verdict:
    return Verdict(criterion_code="C7", applicable=True, answer=answer, confidence=0.9,
                   evidence=[Evidence(turn_idx=11, speaker="operator", quote="Пожалуйста. Хорошего вечера!")],
                   explanation="Завершение — Да. Оператор попрощался после подтверждения клиента.", flags=flags or [])


@pytest.mark.parametrize("policy", [None, "yes", "na", "N/A", "no"])
def test_policy_ignores_verdicts_without_the_flag(policy):
    v = apply_c7_bare_farewell_policy(_verdict(), _c7(), policy, "agent chat")
    assert v.applicable and v.answer == "yes"


def test_policy_na_turns_a_bare_farewell_into_na():
    v = apply_c7_bare_farewell_policy(_verdict(flags=["bare_farewell"]), _c7(), "N/A", "agent chat")
    assert not v.applicable and v.answer == "" and v.na_reason
    assert v.explanation.startswith("Завершение — N/A") and "[11]" in v.explanation
    assert "rule_c7_bare_farewell_na" in v.flags


def test_policy_no_turns_a_bare_farewell_into_no():
    v = apply_c7_bare_farewell_policy(_verdict(flags=["bare_farewell"]), _c7(), "no", "agent chat")
    assert v.applicable and v.answer == "no" and v.explanation.startswith("Завершение — Нет")


def test_policy_yes_keeps_or_restores_yes():
    v = apply_c7_bare_farewell_policy(_verdict(answer="no", flags=["bare_farewell"]), _c7(), "yes", "voice call")
    assert v.applicable and v.answer == "yes"


def test_policy_only_touches_c7():
    crit = _c7()
    crit.code = "C5"
    v = _verdict(flags=["bare_farewell"])
    v.criterion_code = "C5"
    assert apply_c7_bare_farewell_policy(v, crit, "na", "agent chat").applicable


def test_default_policy_is_na():
    v = apply_c7_bare_farewell_policy(_verdict(flags=["bare_farewell"]), _c7(), None, "agent chat")
    assert not v.applicable


def _na_verdict() -> Verdict:
    return Verdict(criterion_code="C7", applicable=False, answer="", confidence=0.9, na_reason="Ответ организаторов «N/a».",
                   evidence=[Evidence(turn_idx=11, speaker="operator", quote="Пожалуйста. Хорошего вечера!")],
                   explanation="Завершение — N/A. Вежливое прощание без итога.", flags=["bare_farewell"])


def test_model_na_with_flag_follows_the_policy():
    keep = apply_c7_bare_farewell_policy(_na_verdict(), _c7(), "na", "agent chat")
    assert not keep.applicable and keep.na_reason == "Ответ организаторов «N/a»."
    yes = apply_c7_bare_farewell_policy(_na_verdict(), _c7(), "yes", "agent chat")
    assert yes.applicable and yes.answer == "yes" and not yes.na_reason and yes.explanation.startswith("Завершение — Да")
    no = apply_c7_bare_farewell_policy(_na_verdict(), _c7(), "no", "agent chat")
    assert no.applicable and no.answer == "no"


def test_evidence_is_taken_from_the_explanation_when_missing():
    from cqa.ingest.normalize import NormTurn
    from cqa.judge.validate import EVIDENCE_FROM_TEXT_FLAG, evidence_from_text, validate_verdict

    turns = [NormTurn(idx=i, timestamp_raw=None, offset_seconds=None, speaker=s, text=f"turn {i}", is_duplicate=False, segment=None)
             for i, s in enumerate(["client", "bot", "operator", "client", "operator"])]
    v = Verdict(criterion_code="C7", applicable=True, answer="no", confidence=0.8, evidence=[],
                explanation="Завершение — Нет. Основание — реплика [4] и [99] (нет такой).", step_results=["[2] приветствие"])
    assert [e.turn_idx for e in evidence_from_text(v, turns)] == [4]
    res = validate_verdict(v, turns, _c7(), channel="agent chat")
    assert [e.turn_idx for e in res.verdict.evidence] == [4] and res.verdict.evidence[0].speaker == "operator"
    assert EVIDENCE_FROM_TEXT_FLAG in res.verdict.flags and not res.needs_retry
    cited = Verdict(criterion_code="C7", applicable=True, answer="yes", confidence=0.9,
                    evidence=[Evidence(turn_idx=2, speaker="operator", quote="turn 2")], explanation="… [4]")
    assert [e.turn_idx for e in validate_verdict(cited, turns, _c7(), channel="agent chat").verdict.evidence] == [2]


# ------------------------------------------------------------------------------------------------ N/A reason from the explanation

def test_na_reason_is_taken_from_the_explanation_instead_of_a_retry():
    from cqa.judge.contract import Verdict
    from cqa.judge.validate import NA_REASON_FROM_TEXT_FLAG, na_reason_from_text, validate_verdict

    crit = _crit("C3") if "_crit" in globals() else None
    if crit is None:
        import json as _json

        from cqa.config import settings as _s
        from cqa.judge.packer import CriterionPack
        seed = _json.loads((_s.seeds_dir / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
        c = next(c for c in seed["criteria"] if c["code"] == "C3")
        crit = CriterionPack(code="C3", title=c["title"], instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"],
                             na_allowed=c["na_allowed"], na_condition=c["na_condition"], channel_overrides=c["channel_overrides"] or {},
                             judge_spec=None)
    from cqa.ingest.normalize import NormTurn
    turns = [NormTurn(idx=0, timestamp_raw="", offset_seconds=None, speaker="client", text="Где посмотреть мой тариф?"),
             NormTurn(idx=1, timestamp_raw="", offset_seconds=None, speaker="operator", text="В приложении: Меню → Тарифы.")]
    v = Verdict(criterion_code="C3", applicable=False, answer="", confidence=0.9, na_reason="",
                explanation="Идентификация — N/A. Клиент задал общий вопрос о навигации, данные счёта не раскрывались. Основание — [0].")
    res = validate_verdict(v, turns, crit, channel="agent chat")
    assert not res.needs_retry and "N/A without na_reason" not in res.issues
    assert res.verdict.na_reason.startswith("Клиент задал общий вопрос") and NA_REASON_FROM_TEXT_FLAG in res.verdict.flags
    # nothing to take it from: the re-ask stays
    bare = Verdict(criterion_code="C3", applicable=False, answer="", confidence=0.9, na_reason="", explanation="N/A")
    assert na_reason_from_text(bare) == ""
    assert validate_verdict(bare, turns, crit, channel="agent chat").needs_retry
