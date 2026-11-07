"""The contact criterion (C1 «Установление контакта») on calls whose opening the recording missed (organizer answer
2026-11-08, docs/ORGANIZER_UPDATES.md §5).

JSON 1.0 allows only true/false for contact. The organizers left the unrecorded-greeting case of DEV calls to the team
(no calls in the control set). Our rule: a deduction needs a fragment one can listen to and contest, so an operator
opening said wholly or in part before «Запись и транскрибирование запущены» is credited by rule (no model call), quoted
from the transcript, and the call is listed as a recording gap. Everything else about C1 is unchanged.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from cqa import db as cqa_db
from cqa.config import settings
from cqa.db import Base
from cqa.export.results_json import from_db, validate_document
from cqa.ingest.normalize import normalize
from cqa.ingest.service import import_records
from cqa.judge import runner as judge_runner
from cqa.judge.contract import Claim, Evidence, Verdict
from cqa.judge.packer import ConversationPack, CriterionPack
from cqa.judge.validate import (CONTACT_OUTSIDE_RECORDING_FLAG, credit_unrecorded_opening, effective_na, is_contact_criterion,
                                opening_outside_recording, unusable_reason, validate_verdict)
from cqa.models import ChecklistVersion, EvaluationCriterion, FewshotExample
from cqa.turnrefs import display_turn_refs

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
T0 = datetime(2026, 11, 3, 13, 49, 20, tzinfo=UTC)
OPENING = "Да компания федеброкер меня зовут [Имя скрыто], [Имя скрыто], мы с вами ранее разговаривали."
OPENING_SHOWN = OPENING[:-1]  # quoted without the closing full stop


def _call(rows, cid: str = "call-gap") -> dict:
    return {"conversation_id": cid, "channel": "voice call", "language": "RU",
            "transcript": [{"timestamp": (T0 + timedelta(seconds=s)).isoformat(), "speaker": sp, "text": tx} for s, sp, tx in rows]}


# the shape of DEV p4-ru-002: the agent joins, the client says «Алло», the operator's opening, then the recording starts
LATE = _call([(0, "system", "Агент присоединился к разговору с клиентом."), (23, "client", "Алло."), (27, "operator", OPENING),
              (28.5, "client", "Да."), (30.2, "system", "Запись и транскрибирование запущены."),
              (32.8, "operator", "Да, можете сейчас попробовать выставить лимит на приказ."), (40, "client", "Хорошо, спасибо."),
              (45, "operator", "Всего доброго, до свидания!")])
ON_TIME = _call([(0, "system", "Агент присоединился к разговору с клиентом."), (0.5, "system", "Запись и транскрибирование запущены."),
                 (4, "client", "Алло."), (9, "operator", "Компания Фридом Брокер, слушаю вас."), (12, "client", "Хочу выставить лимит.")],
                cid="call-ok")
# the opening ends 2 s after the start, but its 13 words take about 3,7 s (3,5 words/s): it began about 1,7 s before the recording
PARTIAL = _call([(0, "system", "Агент присоединился к разговору с клиентом."), (10, "client", "Алло."),
                 (16, "system", "Запись и транскрибирование запущены."),
                 (18, "operator", "Здравствуйте, компания Фридом Брокер, меня зовут Нина, чем я могу вам сегодня помочь?"),
                 (25, "client", "Хочу выставить лимит.")], cid="call-partial")

C1 = CriterionPack(code="C1", title="Установление контакта", instruction="", answer_type="binary",
                   options=[{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}],
                   na_allowed=False, na_condition=None, channel_overrides={}, judge_spec=None)


def _pack(**kw) -> CriterionPack:
    return CriterionPack(**{**C1.__dict__, **kw})


def _verdict(answer: str, applicable: bool = True, evidence=(), flags=(), claims=()) -> Verdict:
    return Verdict(criterion_code="C1", applicable=applicable, na_reason="" if applicable else "Приветствие не попало в запись.",
                   answer=answer, confidence=0.55, explanation="Ответ модели.", evidence=list(evidence), flags=list(flags),
                   claims=list(claims))


# ------------------------------------------------------------------------------------------------ the gap


def test_the_gap_is_read_from_the_audio_clock():
    gap = opening_outside_recording(normalize(LATE).turns)
    assert (gap.op.idx, gap.start.idx, gap.lost_s, gap.partial) == (2, 4, 30.2, False)
    assert opening_outside_recording(normalize(ON_TIME).turns) is None  # the opening is in the recording


def test_an_opening_that_began_before_the_recording_counts_as_well():
    gap = opening_outside_recording(normalize(PARTIAL).turns)
    assert (gap.op.idx, gap.start.idx, gap.partial) == (3, 2, True) and gap.missing_s == pytest.approx(1.7, abs=0.1)
    # bounded by the previous spoken turn: had the client spoken 1 s before the stamp, the opening could not start earlier
    tight = _call([(0, "system", "Агент присоединился к разговору с клиентом."), (16, "system", "Запись и транскрибирование запущены."),
                   (17, "client", "Алло."), (18, "operator", PARTIAL["transcript"][3]["text"])], cid="c")
    assert opening_outside_recording(normalize(tight).turns) is None


GREETING = "Здравствуйте, компания Фридом Брокер, меня зовут Нина, чем могу вам помочь сегодня по вашему счёту?"


@pytest.mark.parametrize("rows,why", [
    # the reviewer's shapes: the operator speaks first, right after joining, the recording started just before the join
    ([(0, "system", "Агент присоединился к разговору с клиентом."), (0.5, "system", "Запись и транскрибирование запущены."),
      (2.5, "operator", GREETING)], "at most 0,5 s between the join and the start"),
    ([(0, "system", "Запись и транскрибирование запущены."), (1, "system", "Агент присоединился к разговору с клиентом."),
      (3, "operator", GREETING)], "the agent joined after the recording started"),
    ([(0, "system", "Агент присоединился к разговору с клиентом."), (1, "bot", "Здравствуйте!"),
      (10, "system", "Запись и транскрибирование запущены."), ("bad", "client", "Алло."), (11, "operator", GREETING)],
     "the previous spoken turn has no stamp"),
    ([(0, "system", "Агент присоединился к разговору с клиентом."), (3, "operator", "Алло?"),
      (5, "system", "Запись и транскрибирование запущены."), (12, "operator", GREETING)], "«Алло?» is not the opening; the greeting is recorded"),
])
def test_no_phantom_gaps(rows, why):
    t0 = T0
    record = {"conversation_id": "c", "channel": "voice call", "language": "RU",
              "transcript": [{"timestamp": ("2026-11-03Tnot-a-time" if s == "bad" else (t0 + timedelta(seconds=s)).isoformat()),
                              "speaker": sp, "text": tx} for s, sp, tx in rows]}
    assert opening_outside_recording(normalize(record).turns) is None, why


def test_an_opening_stamped_before_the_start_is_a_full_gap_whatever_its_position():
    record = _call([(0, "system", "Агент присоединился к разговору с клиентом."), (8, "system", "Запись и транскрибирование запущены."),
                    (6, "operator", GREETING), (12, "client", "Да.")])  # listed after the start, stamped 2 s before it
    gap = opening_outside_recording(normalize(record).turns)
    assert gap is not None and not gap.partial and (gap.op.idx, gap.start.idx) == (2, 1)


def test_no_rule_without_a_recording_start_or_without_a_clock():
    no_start = _call([(0, "system", "Агент присоединился к разговору с клиентом."), (5, "operator", "Компания Фридом, слушаю.")])
    assert opening_outside_recording(normalize(no_start).turns) is None
    turns = normalize(LATE).turns
    for t in turns:
        t.offset_seconds = None  # timestamps that could not be parsed: no audio clock at all
    assert opening_outside_recording(turns) is None


# ------------------------------------------------------------------------------------------------ which criterion


def test_only_the_contact_criterion_gets_the_rule():
    assert is_contact_criterion(C1)
    assert is_contact_criterion(_pack(code="C1b", export_id="contact"))      # mapped by scoring_rules.export_ids
    assert not is_contact_criterion(_pack(export_id="greeting"))             # C1 exported as something else
    assert not is_contact_criterion(_pack(is_critical=True))                 # a gross-error criterion coded C1
    assert not is_contact_criterion(_pack(options=[{"value": "10", "points": 10}, {"value": "0", "points": 0}]))
    v = validate_verdict(_verdict("no"), normalize(LATE).turns, _pack(is_critical=True), channel="voice call").verdict
    assert v.answer == "no" and CONTACT_OUTSIDE_RECORDING_FLAG not in v.flags


def test_the_organizer_checklist_has_no_na_for_contact_on_any_channel():
    c1 = next(c for c in SEED["criteria"] if c["code"] == "C1")
    pack = CriterionPack(code="C1", title=c1["title"], instruction=c1["instruction"], answer_type=c1["answer_type"],
                         options=c1["options"], na_allowed=c1["na_allowed"], na_condition=c1["na_condition"],
                         channel_overrides=c1["channel_overrides"], judge_spec=None)
    assert [effective_na(pack, ch)[0] for ch in ("agent chat", "voice call", None)] == [False, False, False]
    assert "Запись и транскрибирование запущены" in c1["channel_overrides"]["voice call"]["instruction"]


# ------------------------------------------------------------------------------------------------ the verdict


@pytest.mark.parametrize("answer,applicable,shown", [("", False, "N/A"), ("no", True, "no"), ("yes", True, "yes")])
def test_any_model_answer_becomes_a_credited_contact_without_a_retry(answer, applicable, shown):
    turns = normalize(LATE).turns
    bad_quote = [Evidence(turn_idx=2, speaker="operator", quote="совсем другой текст")]  # would cost a penalty
    v0 = _verdict(answer, applicable, bad_quote, flags=["missing_greeting", "asr_noise"],
                  claims=[Claim(turn_idx=2, claim="Компания называется Фридом", status="contradicted")])
    r = validate_verdict(v0, turns, C1, channel="voice call")
    v = r.verdict
    assert (v.applicable, v.answer, v.na_reason, v.confidence, r.needs_retry) == (True, "yes", "", 1.0, False)
    assert v.flags == [CONTACT_OUTSIDE_RECORDING_FLAG] and v.claims == []  # nothing of the replaced verdict stays
    assert [(e.turn_idx, e.speaker, e.role) for e in v.evidence] == [(2, "operator", "context")] and v.evidence[0].quote == OPENING_SHOWN
    assert v.step_results[-1] == f"Ответ модели, заменённый правилом: {shown}."
    shown_text = display_turn_refs(v.explanation, len(turns) - 1)  # people read 1-based turn numbers
    assert "реплике [5] через 30,2 с от начала разговора" in shown_text and "первая реплика оператора [3]" in shown_text
    assert f"«{OPENING_SHOWN}»" in shown_text


def test_the_quote_is_safe_for_the_turn_number_shifter():
    turns = normalize(_call([(0, "system", "Агент присоединился к разговору с клиентом."),
                             (5, "operator", "Компания «Фридом», реплика 3 из скрипта."),
                             (9, "system", "Запись и транскрибирование запущены."), (12, "client", "Да")])).turns
    v = credit_unrecorded_opening(_verdict("no"), C1, opening_outside_recording(turns))
    shown = display_turn_refs(v.explanation, len(turns) - 1)
    assert "«Компания “Фридом”, реплика 3 из скрипта»" in shown  # inner guillemets curled, «реплика 3» not renumbered


def test_partial_recording_explains_itself():
    turns = normalize(PARTIAL).turns
    v = credit_unrecorded_opening(_verdict("no"), C1, opening_outside_recording(turns))
    shown = display_turn_refs(v.explanation, len(turns) - 1)
    assert v.answer == "yes" and "закончилась уже после старта записи (реплика [3]" in shown and "примерно 1,7 с" in shown


@pytest.mark.parametrize("record,channel", [(ON_TIME, "voice call"), (LATE, "agent chat")])
def test_the_rule_stays_out_of_ordinary_dialogs(record, channel):
    r = validate_verdict(_verdict("no", evidence=[Evidence(turn_idx=3, speaker="operator", quote="Компания Фридом Брокер")]),
                         normalize(record).turns, C1, channel=channel)
    assert r.verdict.answer == "no" and CONTACT_OUTSIDE_RECORDING_FLAG not in r.verdict.flags


def test_unusable_verdicts_are_named():
    assert unusable_reason(_verdict("", applicable=False), C1, "voice call") == "N/A is not allowed for this criterion on this channel"
    assert unusable_reason(_verdict("maybe"), C1, "agent chat") == "answer 'maybe' is not an allowed option"
    assert unusable_reason(_verdict("Да"), C1, "agent chat") is None  # a label normalizes to its value


# ------------------------------------------------------------------------------------------------ the judge loop


class _Refuses:
    """A provider that must not be called."""
    name, model = "anthropic", "claude-opus-5-5"

    async def complete(self, *a, **k):
        raise AssertionError("the contact rule needs no model call")


class _StubbornNA:
    """A provider that answers N/A twice for a criterion that forbids it."""
    name, model = "anthropic", "claude-opus-5-5"

    def __init__(self):
        self.calls = 0

    async def complete(self, system, messages, schema, effort=None):
        self.calls += 1
        verdict = {"criterion_code": "C5", "applicable": False, "na_reason": "нет данных", "answer": "", "confidence": 0.4,
                   "explanation": "Не применимо."}
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0)
        return SimpleNamespace(provider="anthropic", model=self.model, raw={}, parsed=verdict, usage=usage, latency_ms=1)

    def cost(self, usage):
        return 0.001


def _conv_pack(record) -> ConversationPack:
    n = normalize(record)
    return ConversationPack(conversation_id=record["conversation_id"], channel=record["channel"], language="RU", turns=n.turns,
                            operator_start_idx=1, terminal_event=None, identity_confirmed_event=None,
                            first_response_seconds=None, operator_name=None)


def test_the_runner_decides_the_rule_without_calling_the_model():
    out = asyncio.run(judge_runner.judge_criterion([_Refuses(), _Refuses()], _conv_pack(LATE), C1, rs={}, fewshots=[], reference_facts=[]))
    assert out.error is None and out.verdict.answer == "yes" and out.traces == [] and out.cost_usd == 0.0
    assert out.verdict.flags == [CONTACT_OUTSIDE_RECORDING_FLAG] and out.votes is None


def test_a_judge_that_insists_on_a_forbidden_na_fails_the_criterion_instead_of_storing_a_null():
    c5 = _pack(code="C5", title="Эмпатия", export_id="empathy")
    provider = _StubbornNA()
    chat = {**_call([(0, "client", "Добрый день"), (5, "operator", "Здравствуйте, меня зовут Нина.")], cid="chat-x"),
            "channel": "agent chat"}
    out = asyncio.run(judge_runner.judge_criterion([provider], _conv_pack(chat), c5, rs={}, fewshots=[], reference_facts=[]))
    assert provider.calls == 2 and out.error and "N/A is not allowed" in out.error  # first call + validation retry
    assert len(out.traces) == 2 and out.cost_usd == pytest.approx(0.002)  # both billed calls are kept


# ------------------------------------------------------------------------------------------------ end to end


@pytest.fixture()
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'contact.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    for module in (judge_runner, cqa_db):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    from cqa.checklist.service import ensure_seed

    with factory() as db:
        ensure_seed(db)
        import_records(db, [LATE, ON_TIME], group_id="TEST")
        db.commit()
    yield factory
    engine.dispose()


def _v1(store) -> int:
    with store() as db:
        return db.execute(select(ChecklistVersion).where(ChecklistVersion.version_no == 1)).scalars().first().id


def _row(db, eid: int, code: str) -> EvaluationCriterion:
    return db.execute(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id == eid, EvaluationCriterion.code == code)).scalar_one()


def test_end_to_end_the_call_gets_contact_true_in_the_results_file(store):
    vid = _v1(store)
    eid = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake"))
    with store() as db:
        row = _row(db, eid, "C1")
        assert (row.applicable, row.answer, row.score, row.flags) == (True, "yes", 1.0, [CONTACT_OUTSIDE_RECORDING_FLAG])
        doc, turn_counts, channels = from_db(db, conversation_ids=["call-gap"], checklist_version_id=vid, team_name="T")
    rec = doc["results"][0]
    contact = next(c for c in rec["criteria"] if c["criterion_id"] == "contact")
    assert contact["value"] is True and contact["evidence_turns"] == [3]
    assert "[5]" in contact["rationale"] and "по правилу для звонков" in contact["rationale"]
    errors, _ = validate_document(doc, expected_ids=["call-gap"], turn_counts=turn_counts, channels=channels)
    assert errors == []


def test_a_partial_run_judges_a_stale_na_again(store):
    vid = _v1(store)
    eid = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake"))
    with store() as db:  # the state before 2026-11-08: C1 stored as N/A on this call
        row = _row(db, eid, "C1")
        row.applicable, row.answer, row.score, row.flags, row.na_reason = False, None, None, [], "Приветствие не попало в запись."
        db.commit()
    new = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake", mode="partial",
                                                         base_evaluation_id=eid, only_codes={"C7"}))
    with store() as db:
        c1 = _row(db, new, "C1")
        assert (c1.applicable, c1.answer) == (True, "yes") and CONTACT_OUTSIDE_RECORDING_FLAG in c1.flags


def test_reviewers_cannot_keep_or_preview_a_forbidden_na_and_rule_rows_teach_nothing(store):
    from cqa.review import service as review

    vid = _v1(store)
    eid = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake"))
    with store() as db:
        c1 = _row(db, eid, "C1")
        with pytest.raises(ValueError, match="N/A is not allowed"):
            review.preview_total(db, c1, "N/A")
        ap = review.create_appeal(db, c1, "Не согласна", "Оператор")
        review.decide(db, ap, action="confirm", new_answer=None, reason="Верно", reviewer_label="QA", add_to_fewshot=True)
        assert db.execute(select(FewshotExample)).scalars().all() == []  # the rule's «Да» never becomes an example
        c1.applicable, c1.answer, c1.final_answer, c1.review_state = False, None, None, "none"  # a stale N/A row
        db.commit()
        ap2 = review.create_appeal(db, c1, "Ещё раз", "Оператор")
        with pytest.raises(ValueError, match="N/A is not allowed"):
            review.decide(db, ap2, action="confirm", new_answer=None, reason="Верно", reviewer_label="QA", add_to_fewshot=False)


def test_recording_gaps_list_the_call_for_the_telephony_owner(store):
    from cqa.analytics.recording import recording_gaps
    from cqa.routers.analytics import router

    assert "/api/analytics/recording-gaps" in {r.path for r in router.routes}
    vid = _v1(store)
    with store() as db:
        before = recording_gaps(db)
    assert (before["calls"], before["with_gap"], before["share"], before["opening_not_recorded"]) == (2, 1, 0.5, 1)
    item = before["items"][0]
    assert (item["conversation_id"], item["recording_start_turn"], item["seconds_not_recorded"], item["opening_turn"]) == ("call-gap", 5, 30.2, 3)
    assert (item["turns_not_recorded"], item["operator_turns_not_recorded"], item["credited_by_rule"]) == (4, [3], [])
    assert item["opening_partially_recorded"] is False
    asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake"))
    with store() as db:
        after = recording_gaps(db)
    assert after["items"][0]["credited_by_rule"] == ["C1"] and after["seconds_not_recorded"] == {"total": 30.2, "mean": 30.2, "max": 30.2}
    # compliance only where the opening can be heard: the rule's credit never counts as a greeting
    assert after["contact_compliance_verifiable"] == {"calls": 0, "greeted": 0, "share": None}
    asyncio.run(judge_runner.evaluate_conversation("call-ok", vid, provider_name="fake"))  # «Компания …, слушаю вас»: no greeting
    with store() as db:
        both = recording_gaps(db)
    assert both["contact_compliance_verifiable"] == {"calls": 1, "greeted": 0, "share": 0.0}


def test_a_partial_run_replaces_an_older_model_verdict_on_a_gap_call(store):
    vid = _v1(store)
    eid = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake"))
    with store() as db:  # a model «Нет» stored before the rule existed (hosted p4-ru-002 had eval 62/63 like this)
        row = _row(db, eid, "C1")
        row.answer, row.score, row.flags, row.explanation = "no", 0.0, ["missing_greeting"], "Не поздоровался."
        db.commit()
    new = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake", mode="partial",
                                                         base_evaluation_id=eid, only_codes={"C7"}))
    with store() as db:
        c1 = _row(db, new, "C1")
        assert (c1.answer, c1.flags) == ("yes", [CONTACT_OUTSIDE_RECORDING_FLAG])
        copied = asyncio.run(judge_runner.evaluate_conversation("call-gap", vid, provider_name="fake", mode="partial",
                                                                base_evaluation_id=new, only_codes={"C7"}))
    with store() as db:  # a rule row is simply copied by the next partial run
        assert CONTACT_OUTSIDE_RECORDING_FLAG in _row(db, copied, "C1").flags
