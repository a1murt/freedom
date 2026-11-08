"""Judge providers without network.

- FakeProvider (offline deterministic judge) on real prompts built by cqa.judge.packer, checked by cqa.judge.validate.
- OpenAICompatibleProvider over httpx.MockTransport (json_schema response_format, 400 retry, <think> stripping, errors).
- AnthropicProvider with a fake SDK client in anthropic_provider._client (messages.create with output_config.format, the
  request messages.parse would send); SDK exceptions are built with the SDK's own constructors and HTTP library (httpx2
  in anthropic 2.x).

An autouse fixture clears the API key and the cached client, so nothing here can reach the Claude API.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from anthropic import _exceptions as anthropic_exceptions
from pydantic import BaseModel, TypeAdapter

from cqa.analytics.violations import classify
from cqa.config import settings
from cqa.ingest.normalize import normalize
from cqa.judge import runner as judge_runner
from cqa.judge.contract import JudgeSpec, Usage, Verdict
from cqa.judge.packer import ConversationPack, CriterionPack, build_bundle
from cqa.judge.providers import (
    ProviderError,
    ProviderRefusal,
    anthropic_provider,
    get_provider,
    openai_compatible,
)
from cqa.judge.providers.anthropic_provider import AnthropicProvider
from cqa.judge.providers.fake import FakeProvider, parse_criterion, parse_transcript
from cqa.judge.providers.openai_compatible import OpenAICompatibleProvider
from cqa.judge.validate import retry_instruction, validate_verdict

API_DIR = Path(__file__).resolve().parents[1]
SEED = json.loads((API_DIR / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
SPECS = json.loads((API_DIR / "seeds" / "judge_specs_v1.json").read_text(encoding="utf-8"))
CODES = [c["code"] for c in SEED["criteria"]]
# The SDK builds its exceptions on its own HTTP library (httpx2 in anthropic 2.x, httpx before): use the same one.
SDK_HTTP = getattr(anthropic_exceptions, "httpx2", None) or anthropic_exceptions.httpx
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(anthropic_provider, "_client", None)


# ============================================================================================ fixtures: dialogs


def chat(*operator_lines: str, client: str = "Добрый день, где скачать отчёт брокера за год?") -> list[tuple[str, str, str]]:
    rows = [("00:00", "client", client), ("00:04", "bot", "Здравствуйте! Я виртуальный помощник AIM. Подключаю оператора."),
            ("00:10", "system", "Чат передан оператору")]
    rows += [(f"00:{20 + 5 * i:02d}", "operator", line) for i, line in enumerate(operator_lines)]
    return rows


RU_GOOD = [
    ("00:00", "client", "Здравствуйте, пополнил счёт вчера, а деньги так и не зачислены."),
    ("00:04", "bot", "Здравствуйте! Я виртуальный помощник AIM. Подключаю оператора, ожидайте, пожалуйста."),
    ("00:10", "system", "Чат передан оператору"),
    ("00:40", "operator", "Здравствуйте! Меня зовут Алина, компания Freedom Broker. Понимаю ваше беспокойство, сейчас разберёмся."),
    ("00:55", "operator", "Уточните, пожалуйста, ФИО и номер счёта для проверки?"),
    ("01:30", "client", "Иванов Иван, счёт 40817"),
    ("01:35", "system", "Личность клиента подтверждена"),
    ("02:05", "operator", "Спасибо! Ваш платёж в обработке: зачисление занимает до одного рабочего дня, средства поступят сегодня до 18:00."),
    ("02:30", "client", "Понял, спасибо."),
    ("02:40", "operator", "Пожалуйста! Хорошего дня, до свидания!"),
]
RU_BAD = chat("Меня зовут Алексей. Что у вас?",
              "Вы вообще читать умеете? Всё есть в приложении: «Меню → Отчёты и документы → Отчёт брокера».",
              "Пока.")
KZ_GOOD = [
    ("00:00", "client", "Сәлеметсіз бе, шотқа ақша түспеді."),
    ("00:05", "bot", "Сәлеметсіз бе! Мен AIM виртуалды көмекшісімін. Операторға қосып жатырмын."),
    ("00:10", "system", "Чат операторға жіберілді"),
    ("00:40", "operator", "Сәлеметсіз бе! Менің атым Нұрлан, Freedom Broker."),
    ("00:50", "operator", "Тексеру үшін аты-жөніңізді және ЖСН жазыңызшы?"),
    ("01:20", "client", "Серіков Ерлан"),
    ("01:40", "operator", "Рақмет! Аударым өңделуде, бүгін шотқа түседі."),
    ("01:55", "operator", "Тағы сұрақтарыңыз болса, жазыңыз. Сау болыңыз!"),
]
EN_GOOD = [
    ("00:00", "client", "Hi, my deposit has not arrived in my account."),
    ("00:05", "bot", "Hello! I am the AIM virtual assistant. Connecting you to an operator."),
    ("00:10", "system", "Chat transferred to an operator"),
    ("00:40", "operator", "Good afternoon! My name is Eva, Freedom Broker support."),
    ("00:50", "operator", "Could you confirm your full name and date of birth?"),
    ("01:20", "client", "John Smith, 01.01.1990"),
    ("01:40", "operator", "Thank you! The transfer is being processed and will be credited today."),
    ("01:55", "operator", "Have a good day!"),
]
CALL = [
    ("2026-11-03T10:00:00.0000000+00:00", "system", "Запись и транскрибирование запущены"),
    ("2026-11-03T10:00:05.0000000+00:00", "bot", "Сәлеметсіз бе, Freedom Broker"),
    ("2026-11-03T10:00:20.0000000+00:00", "system", "Агент присоединился к разговору с клиентом"),
    ("2026-11-03T10:00:25.0000000+00:00", "operator", "Добрый день, Freedom Broker, меня зовут [Имя скрыто]. Чем могу помочь?"),
    ("2026-11-03T10:00:35.0000000+00:00", "client", "Хочу узнать баланс счёта."),
    ("2026-11-03T10:00:45.0000000+00:00", "operator", "Назовите, пожалуйста, ФИО и дату рождения."),
    ("2026-11-03T10:00:55.0000000+00:00", "client", "[Данные идентификации скрыты]"),
    ("2026-11-03T10:01:05.0000000+00:00", "operator", "Спасибо, на счёте 100 долларов. Всего доброго!"),
    ("2026-11-03T10:01:10.0000000+00:00", "system", "Клиент завершил разговор"),
    ("2026-11-03T10:01:12.0000000+00:00", "operator", "Алло?"),
]
DIALOGS = {"ru_good": (RU_GOOD, "agent chat", "RU"), "ru_bad": (RU_BAD, "agent chat", "RU"), "kz_good": (KZ_GOOD, "agent chat", "KZ"),
           "en_good": (EN_GOOD, "agent chat", "EN"), "call": (CALL, "voice call", "RU")}


def conv_pack(rows: list[tuple[str, str, str]], channel: str = "agent chat", language: str = "RU", cid: str = "t-1") -> ConversationPack:
    rec = {"conversation_id": cid, "channel": channel, "language": language,
           "transcript": [{"timestamp": ts, "speaker": sp, "text": tx} for ts, sp, tx in rows]}
    n = normalize(rec)
    return ConversationPack(conversation_id=cid, channel=channel, language=language, turns=n.turns, operator_start_idx=n.operator_start_idx,
                            terminal_event=n.terminal_event, identity_confirmed_event=bool(n.meta.get("identity_confirmed_event")),
                            first_response_seconds=n.first_response_seconds, operator_name=n.operator_name)


def crit_pack(seed_code: str, /, **over: Any) -> CriterionPack:
    """CriterionPack of a seeded organizer criterion, with field overrides (code=, options=, na_allowed=, …)."""
    c = next(x for x in SEED["criteria"] if x["code"] == seed_code)
    pack = CriterionPack(code=c["code"], title=c["title"], instruction=c["instruction"], answer_type=c["answer_type"], options=c["options"],
                         na_allowed=c["na_allowed"], na_condition=c.get("na_condition"), channel_overrides=c.get("channel_overrides") or {},
                         judge_spec=SPECS.get(seed_code), is_critical=c["is_critical"])
    for key, value in over.items():
        setattr(pack, key, value)
    return pack


def judge(conv: ConversationPack, crit: CriterionPack, provider: FakeProvider | None = None, messages: list[dict[str, Any]] | None = None):
    bundle = build_bundle(conv, crit)
    res = run((provider or FakeProvider()).complete(bundle.system, messages or bundle.messages, Verdict))
    return Verdict.model_validate(res.parsed), res, bundle


def checked(conv: ConversationPack, crit: CriterionPack) -> Verdict:
    """Fake verdict that passed cqa.judge.validate without a single issue."""
    v, _, _ = judge(conv, crit)
    val = validate_verdict(v.model_copy(deep=True), conv.turns, crit, conversation_terminal=conv.terminal_event, channel=conv.channel)
    assert val.issues == [] and not val.needs_retry, (crit.code, val.issues)
    return val.verdict


# ============================================================================================ fake provider


def test_get_provider_registers_fake_and_keeps_other_names():
    p = get_provider("fake")
    assert isinstance(p, FakeProvider) and (p.name, p.model) == ("fake", "fake-judge")
    assert get_provider("fake:judge-x").model == "judge-x"
    assert get_provider("fake", "judge-y").model == "judge-y"
    assert p.cost(Usage(input_tokens=10**6, output_tokens=10**6, cache_read_tokens=10**6)) == 0.0
    assert get_provider("anthropic").name == "anthropic" and get_provider("local").name == "local"
    with pytest.raises(ProviderError, match="Unknown provider"):
        get_provider("nope")


@pytest.mark.parametrize("dialog", sorted(DIALOGS))
def test_fake_verdicts_are_valid_for_every_criterion(dialog):
    rows, channel, language = DIALOGS[dialog]
    conv = conv_pack(rows, channel, language)
    by_idx = {t.idx: t for t in conv.turns}
    for code in CODES:
        crit = crit_pack(code)
        v, res, _ = judge(conv, crit)
        assert res.provider == "fake" and res.model == "fake-judge" and res.stop_reason == "end_turn"
        assert res.usage == Usage() and res.raw["usage"] == Usage().model_dump() and res.raw["rule"]
        assert v.criterion_code == code and "fake_judge" in v.flags
        assert CYRILLIC.search(v.explanation), v.explanation
        assert v.evidence, code
        for ev in v.evidence:
            turn = by_idx[ev.turn_idx]
            assert ev.speaker == turn.speaker and ev.quote and ev.quote in turn.text
        assert any(by_idx[ev.turn_idx].speaker == "operator" for ev in v.evidence), code
        checked(conv, crit)


def test_fake_heuristics_on_the_reference_dialogs():
    def answers(rows, channel="agent chat", language="RU"):
        conv = conv_pack(rows, channel, language)
        return {code: (v.answer if v.applicable else "N/A") for code in CODES for v in [checked(conv, crit_pack(code))]}

    perfect = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": "20", "C5": "yes", "C6": "10", "C7": "yes", "C8": "no"}
    assert answers(RU_GOOD) == perfect
    assert answers(KZ_GOOD, language="KZ") == perfect
    assert answers(EN_GOOD, language="EN") == perfect
    assert answers(RU_BAD) == {"C1": "no", "C2": "N/A", "C3": "N/A", "C4": "20", "C5": "no", "C6": "10", "C7": "no", "C8": "yes"}
    # calls: the greeting and farewell are read in the operator segment; the line logged after hang-up is ignored
    assert answers(CALL, "voice call") == perfect


def test_c1_greeting_and_missing_greeting():
    yes = checked(conv_pack(RU_GOOD), crit_pack("C1"))
    assert yes.answer == "yes" and yes.evidence[0].turn_idx == 3 and yes.explanation.startswith("Установление контакта — Да.")
    no = checked(conv_pack(RU_BAD), crit_pack("C1"))
    assert no.answer == "no" and no.evidence[0].turn_idx == 3 and no.checked_span is not None
    assert no.explanation.startswith("Установление контакта — Нет.")
    assert checked(conv_pack(chat("Hi! How can I help?")), crit_pack("C1")).answer == "yes"
    assert checked(conv_pack(chat("Добрый вечер, слушаю вас.")), crit_pack("C1")).answer == "yes"
    assert checked(conv_pack(chat("Слушаю.")), crit_pack("C1")).answer == "no"


@pytest.mark.parametrize("line, kind", [
    ("Вы вообще читать умеете? Всё написано в приложении.", "insult"),
    ("Переведите 5000 тенге мне на карту, и я всё сделаю быстрее.", "paid_bypass"),
    ("Оставьте ваш личный номер, вечером проконсультирую за отдельную плату.", "paid_bypass"),
])
def test_c8_stop_phrases_are_flagged_and_classified(line, kind):
    conv = conv_pack(chat("Здравствуйте! Меня зовут Алексей.", line))
    v = checked(conv, crit_pack("C8"))
    assert v.answer == "yes" and v.evidence[0].turn_idx == 4 and v.evidence[0].quote == line
    assert v.explanation.startswith("Грубые ошибки — Да.")
    # the gross-violations view (cqa.analytics.violations) classifies the explanation into the right type
    assert classify(v.explanation, v.flags, [e.quote for e in v.evidence]) == [kind]


def test_c8_without_stop_phrases_is_no():
    v = checked(conv_pack(RU_GOOD), crit_pack("C8"))
    assert v.answer == "no" and v.explanation.startswith("Грубые ошибки — Нет.")


def test_c2_c3_na_only_when_allowed_and_no_account_words():
    conv = conv_pack(RU_BAD)
    for code in ("C2", "C3"):
        v = checked(conv, crit_pack(code))
        assert v.applicable is False and v.answer == "" and v.na_reason and "N/A" in v.explanation
        assert v.evidence[0].role == "context"
    # N/A not allowed for this checklist version → the criterion is judged
    assert checked(conv, crit_pack("C2", na_allowed=False)).answer == "yes"  # "Что у вас?" is a question
    assert checked(conv, crit_pack("C3", na_allowed=False)).answer == "no"
    # account words make the criterion applicable even when N/A is allowed
    assert checked(conv_pack(chat("Добрый день! Меня зовут Алексей. Ваш счёт ограничен.")), crit_pack("C3")).answer == "no"


def test_c3_identification_sources():
    request = checked(conv_pack(RU_GOOD), crit_pack("C3"))
    assert request.answer == "yes" and request.evidence[0].turn_idx == 4  # "ФИО и номер счёта"
    event_rows = chat("Здравствуйте! Меня зовут Алина.", "Сейчас проверю ваш счёт.", client="Не вижу пополнение счёта.")
    event_rows.insert(5, ("00:28", "system", "Личность клиента подтверждена"))
    event = checked(conv_pack(event_rows), crit_pack("C3"))
    assert event.answer == "yes" and [e.speaker for e in event.evidence] == ["system", "operator"]
    mask_rows = chat("Здравствуйте! Меня зовут Алина.", "Спасибо, вижу ваш счёт.", client="Не вижу пополнение счёта.")
    mask_rows.insert(4, ("00:22", "client", "[Данные идентификации скрыты]"))
    mask = checked(conv_pack(mask_rows), crit_pack("C3"))
    assert mask.answer == "yes" and mask.evidence[0].quote == "[Данные идентификации скрыты]"


def test_c5_c7_politeness_and_farewell():
    assert checked(conv_pack(RU_BAD), crit_pack("C5")).answer == "no"
    assert checked(conv_pack(RU_GOOD), crit_pack("C5")).answer == "yes"
    v = checked(conv_pack(RU_BAD), crit_pack("C7"))
    assert v.answer == "no" and v.evidence[0].quote == "Пока." and v.checked_span is not None
    assert checked(conv_pack(chat("Здравствуйте!", "Всего доброго!")), crit_pack("C7")).answer == "yes"


def test_scale_criteria_pick_the_top_step_whatever_the_order():
    conv = conv_pack(RU_BAD)
    assert checked(conv, crit_pack("C4")).answer == "20"
    assert checked(conv, crit_pack("C6")).answer == "10"
    ascending = [{"value": 0, "label": "0", "points": 0}, {"value": 5, "label": "5", "points": 5}, {"value": 10, "label": "10", "points": 10}]
    custom = crit_pack("C6", code="Q1", title="Скорость ответа", options=ascending, judge_spec=None, channel_overrides={})
    assert checked(conv, custom).answer == "10"


def test_unknown_criterion_gets_the_positive_option():
    new = crit_pack("C5", code="C9", title="Предложение дополнительной помощи", judge_spec=None,
                    instruction="Да — оператор спросил, остались ли у клиента вопросы.")
    v = checked(conv_pack(RU_GOOD), new)
    assert v.answer == "yes" and v.explanation.startswith("Предложение дополнительной помощи — Да.")


def test_fake_is_deterministic_and_ignores_message_shape():
    conv, crit = conv_pack(RU_BAD), crit_pack("C1")
    first, res, bundle = judge(conv, crit)
    again, _, _ = judge(conv, crit, provider=FakeProvider())
    assert again == first
    # OpenAI-style string contents
    flat = [{"role": m["role"], "content": "\n".join(b["text"] for b in m["content"])} for m in bundle.messages]
    assert judge(conv, crit, messages=flat)[0] == first
    # a validation retry and an arbiter round append messages; the verdict stays the same
    retry = bundle.messages + [{"role": "assistant", "content": [{"type": "text", "text": json.dumps(res.parsed, ensure_ascii=False)}]},
                               {"role": "user", "content": [{"type": "text", "text": retry_instruction(["answer 'x' is not an allowed option"])}]}]
    assert judge(conv, crit, messages=retry)[0] == first
    arbiter = bundle.messages + [{"role": "user", "content": [{"type": "text", "text": "Independent judges disagree on criterion C1 (…)."}]}]
    assert judge(conv, crit, messages=arbiter)[0] == first


def test_multiline_turns_are_reconstructed_exactly():
    rows = chat("Здравствуйте!\nМеня зовут Алина.\n\nЧем могу помочь?")
    conv = conv_pack(rows)
    bundle = build_bundle(conv, crit_pack("C1"))
    turns = parse_transcript(bundle.messages[0]["content"][0]["text"])
    assert [(t.idx, t.speaker, t.segment) for t in turns] == [(0, "client", "pre"), (1, "bot", "pre"), (2, "system", "pre"), (3, "operator", "operator")]
    assert turns[3].text == rows[3][2]
    assert checked(conv, crit_pack("C1")).evidence[0].quote == rows[3][2]


def test_criterion_block_parsing_and_errors():
    bundle = build_bundle(conv_pack(RU_GOOD), crit_pack("C4"))
    info = parse_criterion([m["content"][0]["text"] for m in bundle.messages if m["role"] == "user"])
    assert (info.code, info.title, info.kind, info.values, info.na_allowed) == ("C4", "Корректная и полная консультация", "scale",
                                                                               ["20", "15", "10", "5", "0"], True)
    with pytest.raises(ProviderError, match="no criterion block"):
        parse_criterion(["## Transcript\n[0] client (00:00): Привет"])
    with pytest.raises(ProviderError, match="lists no allowed answers"):
        parse_criterion(["# Criterion Q · Тест\nAllowed answers: scale, one of: \nN/A: not allowed"])
    no_header = [{"role": "user", "content": "## Transcript\n[0] client (00:00): Привет"}]
    with pytest.raises(ProviderError):
        run(FakeProvider().complete("sys", no_header, Verdict))


def test_fake_compiles_a_judge_spec_and_rejects_unknown_schemas():
    crit = {"title": "Тест", "instruction": "Оператор поздоровался и представился.", "answer_type": "binary", "na_allowed": False}
    prompt = [{"role": "user", "content": [{"type": "text", "text": "Write a JudgeSpec.\n\nCriterion:\n" + json.dumps(crit, ensure_ascii=False, indent=1)}]}]
    res = run(FakeProvider().complete("compile", prompt, JudgeSpec))
    spec = JudgeSpec.model_validate(res.parsed)
    assert spec.definition == crit["instruction"] and spec.decision_steps and res.usage == Usage()

    class Other(BaseModel):
        x: int

    with pytest.raises(ProviderError, match="cannot produce schema"):
        run(FakeProvider().complete("sys", prompt, Other))


def test_fake_through_the_runner_single_and_ensemble():
    conv, crit = conv_pack(RU_BAD), crit_pack("C8")
    v, traces, issues = run(judge_runner.judge_one(get_provider("fake"), conv, crit, explanation_language="ru", fewshots=[],
                                                   reference_facts=[], effort="high"))
    assert v.answer == "yes" and issues == []
    assert [(t["provider"], t["model"], t["kind"], t["cost_usd"], t["input_tokens"]) for t in traces] == [("fake", "fake-judge", "judge", 0.0, 0)]
    out = run(judge_runner.judge_criterion([FakeProvider(), FakeProvider("fake-judge-2")], conv, crit,
                                           rs={"judge_effort": "high", "explanation_language": "ru"}, fewshots=[], reference_facts=[]))
    assert out.error is None and out.cost_usd == 0.0 and out.verdict.answer == "yes"
    assert [vote["model"] for vote in out.votes] == ["fake-judge", "fake-judge-2"] and "judges_disagree" not in out.verdict.flags


# ============================================================================================ OpenAI-compatible provider


def _verdict_json(**over: Any) -> dict[str, Any]:
    v = {"criterion_code": "C1", "applicable": True, "na_reason": "", "answer": "yes", "confidence": 0.9,
         "evidence": [{"turn_idx": 3, "speaker": "operator", "quote": "Здравствуйте!", "role": "supports"}], "checked_span": None,
         "explanation": "Установление контакта — Да.", "step_results": [], "flags": [], "claims": []}
    v.update(over)
    return v


def _completion(content: str, *, model: str = "qwen3:8b", finish: str = "stop") -> dict[str, Any]:
    return {"id": "cmpl-1", "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}}


class _MockHttpx:
    """Stands in for the httpx module inside openai_compatible: every AsyncClient gets a MockTransport."""

    def __init__(self, handler):
        self._handler = handler

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:  # mirrors httpx.AsyncClient
        kwargs["transport"] = httpx.MockTransport(self._handler)
        return httpx.AsyncClient(*args, **kwargs)


@pytest.fixture
def local_llm(monkeypatch):
    """Install a request handler; returns the list of captured request bodies."""
    calls: list[dict[str, Any]] = []

    def install(respond):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
            return respond(body, len(calls))

        monkeypatch.setattr(openai_compatible, "httpx", _MockHttpx(handler))
        return calls

    return install


def _local() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(base_url="http://llm.test/v1/", model="qwen3:8b", api_key="sk-local")


def _bundle_messages() -> tuple[str, list[dict[str, Any]]]:
    bundle = build_bundle(conv_pack(RU_GOOD), crit_pack("C1"))
    return bundle.system, bundle.messages


def test_local_json_schema_request_and_success(local_llm):
    verdict = _verdict_json()
    calls = local_llm(lambda body, n: httpx.Response(200, json=_completion(json.dumps(verdict, ensure_ascii=False))))
    system, messages = _bundle_messages()
    res = run(_local().complete(system, messages, Verdict, max_tokens=1234))
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://llm.test/v1/chat/completions" and call["headers"]["authorization"] == "Bearer sk-local"
    body = call["body"]
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("qwen3:8b", 0, 1234)
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "Verdict", "schema": Verdict.model_json_schema()}}
    sent = body["messages"]
    assert sent[0] == {"role": "system", "content": system}
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user", "user"]
    assert all(isinstance(m["content"], str) for m in sent)  # Anthropic content blocks are flattened to text
    assert sent[1]["content"] == messages[0]["content"][0]["text"]
    assert sent[-1]["content"].startswith("Return only the JSON object matching the schema: ")
    assert res.parsed == verdict and Verdict.model_validate(res.parsed)
    assert (res.provider, res.model, res.stop_reason) == ("local", "qwen3:8b", "stop")
    assert res.usage == Usage(input_tokens=120, output_tokens=40) and res.raw["usage"]["total_tokens"] == 160
    assert _local().cost(res.usage) == 0.0


def test_local_retries_without_response_format_on_400(local_llm):
    verdict = _verdict_json(answer="no")

    def respond(body, n):
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}})
        return httpx.Response(200, json=_completion(json.dumps(verdict)))

    calls = local_llm(respond)
    res = run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert len(calls) == 2
    assert "response_format" in calls[0]["body"] and "response_format" not in calls[1]["body"]
    assert calls[1]["body"]["messages"] == calls[0]["body"]["messages"]
    assert res.parsed["answer"] == "no"


def test_local_400_without_response_format_hint_is_an_error(local_llm):
    calls = local_llm(lambda body, n: httpx.Response(400, json={"error": {"message": "context length exceeded"}}))
    with pytest.raises(ProviderError, match="Local provider HTTP 400"):
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert len(calls) == 1


def test_local_retry_failure_is_an_error(local_llm):
    calls = local_llm(lambda body, n: httpx.Response(400, text="unknown field response_format") if n == 1 else httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError, match="HTTP 500"):
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert len(calls) == 2


@pytest.mark.parametrize("content", [
    "<think>The client wants {\"answer\": \"no\"}? No, greeting is present.</think>\n{json}",
    "<think>step 1</think><think>step 2</think>Here is the verdict:\n{json}\nDone.",
    "```json\n{json}\n```",
])
def test_local_strips_think_blocks_and_prose(local_llm, content):
    verdict = _verdict_json()
    local_llm(lambda body, n: httpx.Response(200, json=_completion(content.replace("{json}", json.dumps(verdict, ensure_ascii=False)))))
    res = run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert res.parsed == verdict
    assert res.raw["text"].startswith(content[:6])  # the raw text (with the think block) is kept for the trace


@pytest.mark.parametrize("status", [404, 500, 503])
def test_local_non_200_is_a_provider_error(local_llm, status):
    local_llm(lambda body, n: httpx.Response(status, text="upstream says no"))
    with pytest.raises(ProviderError) as err:
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert f"HTTP {status}" in str(err.value) and "upstream says no" in str(err.value)


def test_local_without_json_is_a_provider_error(local_llm):
    local_llm(lambda body, n: httpx.Response(200, json=_completion("<think>hmm</think>I cannot answer that.")))
    with pytest.raises(ProviderError, match="no JSON object"):
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))


def test_local_broken_json_should_be_a_provider_error(local_llm):
    local_llm(lambda body, n: httpx.Response(200, json=_completion('{"answer": "yes", "confidence": }')))
    with pytest.raises(ProviderError):
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))


def test_local_connection_error_should_be_a_provider_error(local_llm):
    def refuse(body, n):
        raise httpx.ConnectError("connection refused")

    local_llm(refuse)
    with pytest.raises(ProviderError, match="(?i)connection"):
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))


# ============================================================================================ Anthropic provider


class _FakeMessages:
    def __init__(self, outcomes: tuple[Any, ...]):
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeAnthropic:
    """Duck-typed AsyncAnthropic: only client.messages.create(...) is used by AnthropicProvider."""

    def __init__(self, *outcomes: Any):
        self.messages = _FakeMessages(outcomes)


def _message(parsed: Any, *, stop_reason: str = "end_turn", usage: Any = None, model: str = "claude-opus-5-5",
             text: str | None = None) -> SimpleNamespace:
    """A Messages API response: thinking + one text block holding the structured output (JSON of `parsed`)."""
    if usage is None:
        usage = SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=800, cache_creation_input_tokens=100)
    if text is None:
        if isinstance(parsed, BaseModel):
            text = parsed.model_dump_json()
        elif parsed is not None:
            text = json.dumps(parsed, ensure_ascii=False)
        else:
            text = '{"answer": "yes"}'  # not a Verdict: what a truncated or refused answer may leave behind
    return SimpleNamespace(id="msg_test", model=model, stop_reason=stop_reason, usage=usage,
                           content=[SimpleNamespace(type="thinking", thinking="…"), SimpleNamespace(type="text", text=text)])


def _status_error(cls: type[anthropic.APIStatusError], status: int, message: str = "boom") -> anthropic.APIStatusError:
    request = SDK_HTTP.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "api_error", "message": message}}
    response = SDK_HTTP.Response(status, request=request, headers={"request-id": "req_test"}, json=body)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


MESSAGES = [{"role": "user", "content": [{"type": "text", "text": "## Transcript\n[0] client (00:00): Привет"}]}]


def _claude(monkeypatch, *outcomes: Any) -> _FakeAnthropic:
    fake = _FakeAnthropic(*outcomes)
    monkeypatch.setattr(anthropic_provider, "_client", fake)
    return fake


def test_claude_parsed_output_success(monkeypatch):
    verdict = Verdict.model_validate(_verdict_json())
    fake = _claude(monkeypatch, _message(verdict))
    provider = AnthropicProvider("claude-opus-5-5")
    res = run(provider.complete("SYS", MESSAGES, Verdict, effort="medium", max_tokens=2048))
    call = fake.messages.calls[0]
    # exactly the request messages.parse(output_format=Verdict) sends: the SDK's transformed JSON schema in output_config
    schema = anthropic.transform_schema(TypeAdapter(Verdict).json_schema())
    assert call == {"model": "claude-opus-5-5", "max_tokens": 2048, "system": [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}],
                    "messages": MESSAGES, "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}}}
    assert res.parsed == verdict.model_dump()
    assert res.usage == Usage(input_tokens=1200, output_tokens=300, cache_read_tokens=800, cache_write_tokens=100)
    assert res.raw == {"id": "msg_test", "model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": res.usage.model_dump(),
                       "text": verdict.model_dump_json()}
    assert (res.provider, res.model, res.stop_reason) == ("anthropic", "claude-opus-5-5", "end_turn") and res.latency_ms >= 0
    rin, rout, rcache = settings.rate_for("claude-opus-5-5")
    # cache writes (5-minute TTL) are billed at 1.25x the input rate and are part of the cost
    assert provider.cost(res.usage) == pytest.approx((1200 * rin + 300 * rout + 800 * rcache + 100 * rin * 1.25) / 1_000_000)


def test_claude_dict_output_and_missing_usage_fields(monkeypatch):
    _claude(monkeypatch, _message(_verdict_json(answer="no"), usage=SimpleNamespace(input_tokens=10, output_tokens=None), model=""))
    res = run(AnthropicProvider("claude-sonnet-5-5").complete("SYS", MESSAGES, Verdict))
    assert res.parsed["answer"] == "no"
    assert res.usage == Usage(input_tokens=10)
    assert res.model == "claude-sonnet-5-5"  # falls back to the requested model when the response has none


def test_claude_refusal_raises_provider_refusal(monkeypatch):
    _claude(monkeypatch, _message(None, stop_reason="refusal"))
    with pytest.raises(ProviderRefusal, match="refusal") as err:
        run(AnthropicProvider("claude-opus-5-5").complete("SYS", MESSAGES, Verdict))
    assert issubclass(ProviderRefusal, ProviderError)
    # the refused response was billed: its usage and cost travel with the error
    assert err.value.billed and err.value.usage.input_tokens == 1200 and err.value.cost_usd > 0


def test_claude_without_structured_output_is_an_error(monkeypatch):
    _claude(monkeypatch, _message(None, stop_reason="max_tokens"))
    with pytest.raises(ProviderError, match=r"No structured output returned \(stop_reason=max_tokens\)") as err:
        run(AnthropicProvider("claude-opus-5-5").complete("SYS", MESSAGES, Verdict))
    assert not isinstance(err.value, ProviderRefusal)
    assert err.value.billed and err.value.usage.output_tokens == 300


def test_claude_truncated_json_is_a_billed_error(monkeypatch):
    """Output cut off at max_tokens mid-JSON: the SDK's parse() would raise before usage is readable; create() keeps it."""
    _claude(monkeypatch, _message(None, stop_reason="max_tokens", text='{"criterion_code": "C1", "applicable": tr'))
    with pytest.raises(ProviderError, match="stop_reason=max_tokens") as err:
        run(AnthropicProvider("claude-opus-5-5").complete("SYS", MESSAGES, Verdict))
    rin, rout, rcache = settings.rate_for("claude-opus-5-5")
    assert err.value.cost_usd == pytest.approx((1200 * rin + 300 * rout + 800 * rcache + 100 * rin * 1.25) / 1_000_000)
    assert err.value.raw["stop_reason"] == "max_tokens" and err.value.raw["text"].startswith('{"criterion_code"')


@pytest.mark.parametrize("cls, status", [
    (anthropic.BadRequestError, 400),
    (anthropic.AuthenticationError, 401),
    (anthropic.RateLimitError, 429),
    (anthropic.InternalServerError, 500),
    (getattr(anthropic, "OverloadedError", anthropic.InternalServerError), 529),
])
def test_claude_api_status_errors_are_mapped(monkeypatch, cls, status):
    exc = _status_error(cls, status, "something went wrong")
    assert exc.status_code == status and exc.request_id == "req_test"
    _claude(monkeypatch, exc)
    with pytest.raises(ProviderError) as err:
        run(AnthropicProvider("claude-opus-5-5").complete("SYS", MESSAGES, Verdict))
    assert str(err.value) == f"Anthropic API error {status}: {exc.message}"
    assert err.value.__cause__ is exc and not isinstance(err.value, ProviderRefusal)


@pytest.mark.parametrize("make", [
    lambda req: anthropic.APIConnectionError(request=req),
    lambda req: anthropic.APITimeoutError(request=req),
])
def test_claude_connection_errors_are_mapped(monkeypatch, make):
    exc = make(SDK_HTTP.Request("POST", "https://api.anthropic.com/v1/messages"))
    _claude(monkeypatch, exc)
    with pytest.raises(ProviderError, match="Anthropic connection error") as err:
        run(AnthropicProvider("claude-opus-5-5").complete("SYS", MESSAGES, Verdict))
    assert err.value.__cause__ is exc


def test_claude_without_api_key_never_builds_a_client():
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
        run(AnthropicProvider("claude-opus-5-5").complete("SYS", MESSAGES, Verdict))
    assert anthropic_provider._client is None


def test_runner_retries_transient_claude_errors_only(monkeypatch):
    slept: list[float] = []

    async def no_sleep(delay: float, result: Any = None) -> Any:
        slept.append(delay)
        return result

    monkeypatch.setattr(judge_runner.asyncio, "sleep", no_sleep)
    verdict = Verdict.model_validate(_verdict_json())
    fake = _claude(monkeypatch, _status_error(anthropic.RateLimitError, 429, "rate limited"), _message(verdict))
    v, trace = run(judge_runner._call_once(AnthropicProvider("claude-opus-5-5"), "SYS", MESSAGES, "high"))
    assert v == verdict and len(fake.messages.calls) == 2 and slept == [2.0]
    assert trace["provider"] == "anthropic" and trace["input_tokens"] == 1200

    slept.clear()
    fake = _claude(monkeypatch, _status_error(anthropic.BadRequestError, 400, "invalid request"))
    with pytest.raises(ProviderError, match="Anthropic API error 400"):
        run(judge_runner._call_once(AnthropicProvider("claude-opus-5-5"), "SYS", MESSAGES, "high"))
    assert len(fake.messages.calls) == 1 and slept == []


# ------------------------------------------------------------------------------------------------ local reasoning switch (2026-11-08)

def test_local_sends_reasoning_effort_when_configured(local_llm, monkeypatch):
    from cqa.config import settings as cfg

    verdict = _verdict_json()
    calls = local_llm(lambda body, n: httpx.Response(200, json=_completion(json.dumps(verdict))))
    monkeypatch.setattr(cfg, "local_reasoning_effort", "None ")
    run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert calls[-1]["body"]["reasoning_effort"] == "none"
    monkeypatch.setattr(cfg, "local_reasoning_effort", None)
    run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert "reasoning_effort" not in calls[-1]["body"]


def test_local_drops_reasoning_effort_an_endpoint_does_not_know(local_llm, monkeypatch):
    from cqa.config import settings as cfg

    monkeypatch.setattr(cfg, "local_reasoning_effort", "none")
    verdict = _verdict_json(answer="no")

    def respond(body, n):
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "unknown parameter reasoning_effort"}})
        return httpx.Response(200, json=_completion(json.dumps(verdict)))

    calls = local_llm(respond)
    res = run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert len(calls) == 2 and "reasoning_effort" not in calls[1]["body"] and res.parsed["answer"] == "no"


def test_local_output_cut_at_the_limit_is_retried_once_with_sampling_then_fails_without_transient_words(local_llm):
    calls = local_llm(lambda body, n: httpx.Response(200, json=_completion('{"criterion_code": "C1", "appl', finish="length")))
    with pytest.raises(ProviderError, match="cut at the token limit") as e:
        run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert [c["body"]["temperature"] for c in calls] == [0.0, 0.3]
    assert not any(k in str(e.value).lower() for k in ("invalid json", "timeout", "connection"))  # not retried again


def test_local_sampled_retry_after_a_cut_can_succeed(local_llm):
    verdict = _verdict_json()
    calls = local_llm(lambda body, n: httpx.Response(200, json=_completion("", finish="length") if n == 1
                                                         else _completion(json.dumps(verdict))))
    res = run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict))
    assert res.parsed == verdict and len(calls) == 2


def test_local_output_tokens_are_capped(local_llm):
    calls = local_llm(lambda body, n: httpx.Response(200, json=_completion(json.dumps(_verdict_json()))))
    run(_local().complete("sys", [{"role": "user", "content": "hi"}], Verdict, max_tokens=4096))
    assert calls[0]["body"]["max_tokens"] == 1536
