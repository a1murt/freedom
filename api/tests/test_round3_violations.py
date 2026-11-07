"""Round 3 (violations): a determiner negation in front of a coordinated list negates every item of the list.

classify() types a gross violation from the judge's C8 explanation and ignores negated mentions. _SEGMENT_SPLIT cuts a
list at «or» / «nor» / «и» / commas, and the scan of the last item found the predicate all items share («were made»)
and took it for an affirmed predicate of that item: «No insults or paid offers were made» gave paid_bypass, and a C8 = Да
explanation «The operator insulted the client; no threats or paid offers were made» put an extra paid_bypass into the
violations register. Now a leading determiner negation («no», «none of», «neither … nor», «никаких», «ни … ни»,
«ешқандай») carries over the conjuncts that share its predicate, up to the clause boundary or a conjunct with an
affirmed predicate of its own.

Review of the first fix: «никаких» / «ешқандай» had become clause-wide negations, so an elliptical «никаких извинений» or a
Kazakh «ешқандай … сұрамады» / «ешқандай тексерместен» negated an affirmed keyword elsewhere in the clause; and the
determiner scope was never ended by the conjunct's own affirmed predicate («…, и платную консультацию предложил сам»,
«…, and paid advice was offered»), by a contrast word without a comma, or by the governing verb of a determiner series
(«не проверил ни ФИО, ни кодовое слово и …»).

Offline: pure functions and an in-memory SQLite register (api/data/cqa.db is never opened); no API key, no network.
"""
from __future__ import annotations

import pytest

from cqa.analytics.violations import classify

NO_TYPE = ["other"]  # nothing affirmed in the explanation (classify's fallback without failed identification)


# ------------------------------------------------------------------------------------------------ the reported cases

@pytest.mark.parametrize("text", [
    "No insults or paid offers were made; the operator stayed polite.",
    "No rude language, insults, or offers of paid services were made by the operator.",
    "Neither insults nor paid bypass offers were made.",
])
def test_a_determiner_negation_covers_the_whole_list(text):
    assert classify(text) == NO_TYPE


def test_a_negated_list_adds_no_type_to_a_real_violation():
    assert classify("The operator insulted the client; no threats or paid offers were made.") == ["insult"]
    assert classify("Gross errors — yes. The operator insulted the client, and no threats or paid offers were made.") == ["insult"]
    assert classify("Gross errors — yes. The operator neither insulted the client nor disclosed data, but offered paid advice via "
                    "a personal number.") == ["paid_bypass"]


@pytest.mark.parametrize("text,want", [
    ("The operator offered a paid service to bypass the queue.", ["paid_bypass"]),
    ("The operator insulted the client.", ["insult"]),
    ("The operator insulted the client and offered paid help to skip verification.", ["insult", "paid_bypass"]),
    ("The operator insulted the client; no paid offers were made.", ["insult"]),
    ("No insults were made, but the operator offered a paid bypass.", ["paid_bypass"]),
])
def test_affirmed_types_stay(text, want):
    assert classify(text) == want


# ------------------------------------------------------------------------------------------------ other determiners, RU, KK

@pytest.mark.parametrize("text", [
    "None of insults, threats or paid offers were made.",
    "There were no insults or paid offers made.",  # an existential «there were» still heads the subject
    "The client confirmed that no insults or paid offers were made.",  # the determiner leads its own clause
    "Neither insults, threats, nor paid offers.",
    "The operator neither insulted the client nor offered paid help.",  # «neither» before a verb negates it too
    "Грубые ошибки — Нет. Никаких оскорблений или платных предложений.",
    "Грубые ошибки — Нет. Никаких оскорблений и платных предложений: оператор вёл себя корректно.",
    "Грубые ошибки — Нет. Ни оскорблений, ни платных предложений не было.",
    "Өрескел қателер — Жоқ. Ешқандай қорлау немесе ақылы қызмет ұсынылмады.",
])
def test_negated_lists_in_other_forms(text):
    assert classify(text) == NO_TYPE


def test_ru_negated_list_after_an_affirmed_violation():
    assert classify("Грубые ошибки — Да. Оператор оскорбил клиента, никаких платных услуг и раскрытия данных.") == ["insult"]


# ------------------------------------------------------------------------------------------------ where the scope ends

@pytest.mark.parametrize("text", [
    "No insult, paid help was offered.",  # a bare comma joins two statements, not one list
    "No insults were made and paid offers were made.",  # the first conjunct has a predicate of its own
    "The operator did not insult the client and paid help was offered.",  # «not» negates a verb, not a noun phrase
    "The operator requested no verification and paid advice was offered.",  # «no verification» is an object
    "Грубые ошибки — Да. Оператор ни разу не нагрубил и платную консультацию предложил сам.",
])
def test_the_scope_ends_at_an_own_predicate_or_a_verb_negation(text):
    assert classify(text) == ["paid_bypass"]


def test_kk_without_any_confirmation_is_still_a_disclosure():
    # «ешқандай растаусыз» = «без какого-либо подтверждения»: the «-сыз» word negates its noun, «ешқандай» only stresses it
    assert classify("Өрескел қателер — Иә. Оператор ешқандай растаусыз шот деректерін жария етті.") == ["disclosure"]
    # «бірақ» («но») ends the clause: the negated insult before it does not reach the paid offer after it
    assert classify("Өрескел қателер — Иә. Ешқандай қорлау болмады, бірақ оператор ақылы кеңес ұсынды.") == ["paid_bypass"]


# ------------------------------------------------------------------------------------------------ the register

def _register(explanations: dict[str, tuple[str, str]]) -> dict:
    """violations() over an in-memory DB with one C8 = Да evaluation per conversation id → (language, explanation)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from cqa.analytics.violations import violations
    from cqa.db import Base
    from cqa.models import (
        Checklist,
        ChecklistVersion,
        Conversation,
        Criterion,
        Evaluation,
        EvaluationCriterion,
    )

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        cl = Checklist(name="t")
        db.add(cl)
        db.flush()
        v = ChecklistVersion(checklist_id=cl.id, version_no=1, is_active=True, scoring_rules={}, sections=[])
        db.add(v)
        db.flush()
        crit = Criterion(checklist_version_id=v.id, code="C8", order=8, section="S4", title="Грубые ошибки", instruction="…",
                         answer_type="binary", options=[{"value": "yes", "points": 0}, {"value": "no", "points": 1}],
                         max_points=1, weight=0, is_critical=True, critical_value="yes")
        db.add(crit)
        db.flush()
        for cid, (language, explanation) in explanations.items():
            db.add(Conversation(id=cid, channel="agent chat", language=language, turn_count=12))
            db.flush()
            ev = Evaluation(conversation_id=cid, checklist_version_id=v.id, status="completed", total_score=0, final_total_score=0)
            db.add(ev)
            db.flush()
            db.add(EvaluationCriterion(evaluation_id=ev.id, criterion_id=crit.id, code="C8", applicable=True, answer="yes", score=0,
                                       max_points=1, explanation=explanation))
        db.commit()
        out = violations(db)
    engine.dispose()
    return out


def test_register_lists_only_the_affirmed_type():
    out = _register({"c1": ("EN", "Gross errors — yes. The operator insulted the client; no threats or paid offers were made.")})
    assert out["total"] == 1 and out["counts"] == {"insult": 1, "paid_bypass": 0, "disclosure": 0, "other": 0}
    item = out["items"][0]
    assert item["types"] == ["insult"] and item["type_labels"] == ["Оскорбление клиента"]
    assert item["classified_by"] == "explanation" and [r["code"] for r in item["rules"]] == ["insult"]


# ------------------------------------------------------------------------------------------------ review: «ешқандай» (KK)

@pytest.mark.parametrize("text,want", [
    # «ешқандай» heads the noun phrase after it and is paired with a negated verb («сұрамады») or converb («тексерместен»,
    # «жүргізбей», «жасамай»): an affirmed keyword before it or after its converb is not negated
    ("Өрескел қателер — Иә. Оператор клиентті қорлады және ешқандай кешірім сұрамады.", ["insult"]),
    ("Өрескел қателер — Иә. Оператор клиентті қорлады, ешқандай кешірім сұрамады.", ["insult"]),
    ("Өрескел қателер — Иә. Оператор ешқандай тексерместен шот деректерін жария етті.", ["disclosure"]),
    ("Өрескел қателер — Иә. Оператор ешқандай сәйкестендіру жүргізбей шоттағы шектеуді жария етті.", ["disclosure"]),
    ("Өрескел қателер — Иә. Оператор ешбір тексеру жасамай шот деректерін жария етті.", ["disclosure"]),
    ("Өрескел қателер — Иә. Ешқандай кешірім сұрамады және клиентті қорлады.", ["insult"]),
    ("Өрескел қателер — Иә. Ешқандай кешірім сұрамай клиентті қорлады.", ["insult"]),
    ("Өрескел қателер — Иә. Оператор клиентті қорламай, ақылы кеңес ұсынды.", ["paid_bypass"]),  # «қорламай» = «не оскорбляя»
])
def test_kk_determiner_stays_inside_its_noun_phrase(text, want):
    assert classify(text) == want


@pytest.mark.parametrize("text", [
    "Өрескел қателер — Жоқ. Ешқандай қорлау, ақылы қызмет немесе жария ету болмады.",  # a list closed by «немесе»
    "Өрескел қателер — Жоқ. Ешқандай қорлау және ақылы кеңес ұсынылмады.",
    "Өрескел қателер — Жоқ. Оператор ешқандай ақылы кеңес ұсынбады.",
])
def test_kk_negated_lists_still_negate(text):
    assert classify(text) == NO_TYPE


# ------------------------------------------------------------------------------------------------ review: «никаких» outside its NP

@pytest.mark.parametrize("text,want", [
    # an elliptical «никаких X» next to an affirmed noun phrase negates only X
    ("Грубые ошибки — Да. Оскорбление клиента, никаких извинений.", ["insult"]),
    ("Грубые ошибки — Да. Раскрытие данных счёта, никакой идентификации.", ["disclosure"]),
    ("Грубые ошибки — Да. Никакой эмпатии, сарказм в адрес клиента.", ["insult"]),
    ("Грубые ошибки — Да. Предложение платной консультации через личный номер, никаких оснований.", ["paid_bypass"]),
    # a time adjunct ends the determiner's noun phrase: the insult and the disclosure happened
    ("Грубые ошибки — Да. Никаких извинений после оскорбления клиента.", ["insult"]),
    ("Грубые ошибки — Да. Никакой идентификации до раскрытия данных счёта.", ["disclosure"]),
    ("Gross errors — yes. No checks before disclosing the balance.", ["disclosure"]),
])
def test_ru_determiner_reaches_only_its_own_noun_phrase(text, want):
    assert classify(text) == want


@pytest.mark.parametrize("text", [
    "Грубые ошибки — Нет. Никаких угроз, оскорблений или платных предложений.",  # a middle item of a list closed by «или»
    "Грубые ошибки — Нет. Никакой грубости, оскорблений или предложений платных услуг.",
    "Gross errors — no. No threats, insults or paid offers were made.",
    "Грубые ошибки — Нет. Никаких данных до идентификации оператор не раскрывал.",
])
def test_comma_lists_under_a_determiner_still_negate(text):
    assert classify(text) == NO_TYPE


# ------------------------------------------------------------------------------------------------ review: a series of determiners

@pytest.mark.parametrize("text,want", [
    # the second «ни» / «no» starts a segment of its own; the series is the object of «проверил» / «used», and the conjunct
    # after it has an affirmed predicate of its own
    ("Грубые ошибки — Да. Оператор не проверил ни ФИО, ни кодовое слово и платную консультацию предложил сам.", ["paid_bypass"]),
    ("Грубые ошибки — Да. Оператор не запросил ни ФИО, ни код, и конфиденциальные данные счёта сообщил сразу.", ["disclosure"]),
    ("Оператор не допустил ни оскорблений, ни угроз, и платную консультацию предложил сам.", ["paid_bypass"]),
    ("The operator used no insults, no threats, and paid advice was offered.", ["paid_bypass"]),
    ("Оператор не проверил никаких данных и платную консультацию предложил сам.", ["paid_bypass"]),
    # the governing verb sits two segments back: the series heads the object of «requested», not the subject of «was offered»
    ("Gross errors — yes. The operator requested no ID, no code word or paid advice was offered.", ["paid_bypass"]),
])
def test_a_determiner_series_after_a_verb_heads_its_object(text, want):
    assert classify(text) == want


def test_a_comma_splice_still_leads():
    # «no threats» follows a clause of its own, not a determiner series: it heads the subject of «were made»
    assert classify("Gross errors — yes. The operator insulted the client, no threats or paid offers were made.") == ["insult"]


# ------------------------------------------------------------------------------------------------ review: where the scope ends

@pytest.mark.parametrize("text,want", [
    # a conjunct with an affirmed predicate of its own: Russian negates a shared predicate («…не было»), so it is not shared
    ("Никаких оскорблений, и платную консультацию оператор предложил сам.", ["paid_bypass"]),
    ("Грубые ошибки — Да. Никаких оскорблений и платную консультацию предложил сам.", ["paid_bypass"]),
    ("Грубые ошибки — Да. Никаких оскорблений: платную консультацию оператор предложил сам.", ["paid_bypass"]),
    ("Грубые ошибки — Да. Никаких проверок: раскрытие баланса или платная консультация.", ["paid_bypass", "disclosure"]),
    # English: «and» joins a statement of its own unless it closes a list, and a singular verb names the last conjunct only
    ("There were no insults or threats, and paid advice was offered.", ["paid_bypass"]),
    ("The operator was rude, no apology and paid help was offered.", ["insult", "paid_bypass"]),
    ("No identity check and paid advice offered.", ["paid_bypass"]),
    ("There were no insults, threats, and paid advice was offered.", ["paid_bypass"]),
    # a contrast word starts a clause of its own even without a comma
    ("No insults or threats but paid advice was offered.", ["paid_bypass"]),
    ("Gross errors — yes. No paid offer but a disclosure of the balance.", ["disclosure"]),
    ("Gross errors — yes. The operator offered nothing but paid advice.", ["paid_bypass"]),
    ("Грубые ошибки — Да. Оператор не оскорблял клиента но предложил платную консультацию.", ["paid_bypass"]),
])
def test_an_own_predicate_or_a_contrast_ends_the_scope(text, want):
    assert classify(text) == want


@pytest.mark.parametrize("text", [
    "No insults, threats and paid offers were made.",  # the «and» closes a list: «were made» is shared
    "No insults, threats, and paid offers were made.",
    "There were no insults or threats, and paid advice was not offered.",  # its own predicate is negated
    "No identity check and paid advice was never offered.",
    "Никаких оскорблений и платных предложений не было.",
    "Никаких оскорблений, угроз или платных предложений оператор не допускал.",
])
def test_shared_or_negated_predicates_still_negate(text):
    assert classify(text) == NO_TYPE


def test_a_determiner_after_the_keyword_verb_negates_it():
    # «disclosed no account data» = «did not disclose any»; a comma between the verb and «no» is another phrase
    assert classify("Gross errors — yes. The operator insulted the client and disclosed no account data.") == ["insult"]
    assert classify("Gross errors — yes. The operator disclosed nothing.") == NO_TYPE
    assert classify("Gross errors — yes. The operator disclosed, no doubt, the account balance.") == ["disclosure"]


def test_register_types_kk_and_ru_explanations_from_the_text():
    out = _register({
        "k1": ("KZ", "Өрескел қателер — Иә. Оператор клиентті қорлады және ешқандай кешірім сұрамады. Негіз — [9] реплика."),
        "k2": ("KZ", "Өрескел қателер — Иә. Оператор ешқандай тексерместен шот деректерін жария етті. Негіз — [9] реплика."),
        "r1": ("RU", "Грубые ошибки — Да. Оператор не проверил ни ФИО, ни кодовое слово и платную консультацию предложил сам."),
        "r2": ("RU", "Грубые ошибки — Да. Оскорбление клиента, никаких извинений. Основание — реплика [9]."),
    })
    got = {i["conversation_id"]: (i["types"], i["classified_by"]) for i in out["items"]}
    assert got == {"k1": (["insult"], "explanation"), "k2": (["disclosure"], "explanation"),
                   "r1": (["paid_bypass"], "explanation"), "r2": (["insult"], "explanation")}
    assert out["counts"] == {"insult": 2, "paid_bypass": 1, "disclosure": 1, "other": 0}
