"""Dynamics 365 Omnichannel integration: transcript parsing and the /api/integrations/dynamics endpoints.

Synthetic transcripts in the documented Omnichannel shape (Microsoft Learn, "Download transcripts in bulk"); in-memory
SQLite per test; the evaluation job starter is monkeypatched, so no test calls a model or touches api/data/cqa.db.
"""
from __future__ import annotations

import base64
import codecs
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import cqa.integrations.dynamics as dyn
import cqa.routers.integrations as integ
from cqa.checklist.service import create_checklist
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.integrations import (
    DynamicsParseError,
    decode_documentbody,
    detect_language,
    encode_documentbody,
    normalize_channel,
    normalize_conversation_id,
    normalize_language,
    parse_transcript,
    sample_messages,
)
from cqa.jobs import estimate as estimator
from cqa.jobs import runner
from cqa.models import ChecklistVersion, Conversation, Dataset, Evaluation, Job, Setting

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
T0 = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
CID = "7f0c2a4e-1b3d-4c5e-9f60-000000000001"

AGENT = {"displayName": "Алина Сапарова", "id": "5f1c9b7e-0000-4000-8000-00000000a001"}
SUPERVISOR = {"displayName": "Ерлан Абенов", "id": "5f1c9b7e-0000-4000-8000-00000000a002"}
CUSTOMER = {"displayName": "Customer", "id": "0c2d4e6f-0000-4000-8000-00000000c001"}
BOT = {"displayName": "Freedom Bot", "id": "28b0b0b0-0000-4000-8000-00000000b001"}
DYNAMICS_BOT = {"displayName": "DynamicsBot", "id": "bc81db89-c4d7-4763-91fe-086fcc2e6daf"}


# ----------------------------------------------------------------------------- synthetic Omnichannel messages


def ts(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat(timespec="milliseconds")


def msg(sec: float, content: str, *, user: dict | None = None, app: dict | None = None, tags: str | None = None, **extra: Any) -> dict:
    """One message as Omnichannel stores it (field set from the Microsoft Learn samples)."""
    frm = None
    if user is not None or app is not None:
        frm = {"user": user, "application": app, "phone": None, "guest": None, "encrypted": None, "onPremises": None,
               "azureApplicationInstance": None, "applicationInstance": None, "device": None}
    out = {"content": content, "contentType": "text", "createdDateTime": ts(sec), "from": frm, "likes": [], "attachments": [],
           "id": str(int((T0 + timedelta(seconds=sec)).timestamp() * 1000)), "created": ts(sec).replace("+00:00", "Z"),
           "culture": None, "deleted": None, "important": None, "modified": None, "modifiedDateTime": None}
    if tags is not None:
        out["tags"] = tags
    out.update(extra)
    return out


def control(sec: float, kind: str, target: str) -> dict:
    eventtime = int((T0 + timedelta(seconds=sec)).timestamp() * 1000)
    content = (f"<{kind}><eventtime>{eventtime}</eventtime><initiator>28:{DYNAMICS_BOT['id']}</initiator>"
               f"<rosterVersion>1</rosterVersion><target>{target}</target></{kind}>")
    return msg(sec, content, isControlMessage=True)


def ru_chat() -> list[dict]:
    """Bot -> agent, a supervisor consult (private), HTML, an attachment, a deleted line, join/leave and noise events."""
    return [
        control(0, "addmember", f"28:{BOT['id']}"),
        msg(1, "Здравствуйте, не проходит вывод средств", user=CUSTOMER, tags="private,parentID-5493180821148088016",
            deliveryMode="unbridged"),
        msg(5, "Здравствуйте! Я виртуальный помощник Freedom. Перевожу на специалиста.", app=BOT, tags="public"),
        control(40, "addmember", f"8:orgid:{AGENT['id']}"),
        msg(41, "Алина Сапарова присоединилась к беседе.", app=DYNAMICS_BOT, tags="system"),
        msg(60, "Здравствуйте! Меня зовут Алина. Подскажите номер заявки на вывод?", user=AGENT, tags="public,messageId-1,RTT-C1"),
        msg(70, "Проверь блокировку в «Изменение сведений»", user=SUPERVISOR, tags="private"),
        msg(75, "Ерлан, клиент ждёт ответа", user=AGENT, tags="private,messageId-2"),
        msg(90, "Заявка 12345", user=CUSTOMER),
        msg(120, "<p>Спасибо! Вывод заблокирован до обновления данных:&nbsp;<b>Меню → Поручения</b></p>", user=AGENT,
            tags="public", contentType="RichText/Html"),
        msg(150, "", app={"displayName": "Customer", "id": "6cc05310-05d9-4ce5-8fc0-d606c478f896"}, deliveryMode="bridged",
            attachments=[{"id": "0-wus-d3-1", "index": 0, "fileType": "png", "name": "screen.png", "annotationid": "cfb551a8"}]),
        msg(155, "ой, не то", user=CUSTOMER, deleted=True),
        msg(170, "Остались ли вопросы? Хорошего дня!", user=AGENT, tags="public"),
        control(180, "deletemember", f"8:orgid:{AGENT['id']}"),
        msg(181, "<topicupdate><eventtime>1</eventtime><value>x</value></topicupdate>", isControlMessage=True),
    ]


def kz_chat() -> list[dict]:
    return [
        msg(0, "Сәлеметсіз бе, шотыма ақша түспеді", user=CUSTOMER, tags="FromCustomer"),
        control(20, "addmember", f"8:orgid:{AGENT['id']}"),
        msg(30, "Сәлеметсіз бе! Менің атым Айгүл. Қазір тексеремін, бір минут күте тұрыңыз.", user=AGENT, tags="public"),
        msg(90, "Рақмет, күтемін", user=CUSTOMER, tags="FromCustomer"),
        msg(120, "Ақша бүгін кешке дейін түседі. Сау болыңыз!", user=AGENT, tags="public"),
    ]


def en_chat() -> list[dict]:
    return [
        msg(0, "Hi, I cannot log in to the app", user=CUSTOMER),
        msg(30, "Hello! My name is Alina. Which error do you see?", user=AGENT, tags="public"),
        msg(60, "It says wrong password", user=CUSTOMER),
        msg(90, "Please use Forgot password, the code comes by SMS. Have a good day!", user=AGENT, tags="public"),
    ]


def annotation_payload(messages: list[dict], **extra: Any) -> dict:
    payload = {"annotation": {"annotationid": "3a5c7e90-0000-4000-8000-00000000a365", "objecttypecode": "msdyn_transcript",
                              "_objectid_value": "9b1d3f50-0000-4000-8000-00000000f365", "documentbody": encode_documentbody(messages)}}
    payload.update(extra)
    return payload


def speakers(record: dict) -> list[str]:
    return [t["speaker"] for t in record["transcript"]]


def texts(record: dict) -> list[str]:
    return [t["text"] for t in record["transcript"]]


# ----------------------------------------------------------------------------- parse_transcript: speaker mapping


def test_documented_shape_maps_speakers_and_events():
    rec = parse_transcript(annotation_payload(ru_chat(), msdyn_ocliveworkitemid="{7F0C2A4E-1B3D-4C5E-9F60-000000000001}"))
    assert rec["conversation_id"] == CID
    assert rec["channel"] == "agent chat" and rec["language"] == "RU" and rec["audio_url"] is None
    assert speakers(rec) == ["client", "bot", "system", "system", "operator", "system", "system", "client", "operator", "client",
                             "operator", "system"]
    t = texts(rec)
    assert t[2] == "Агент присоединился к беседе"  # <addmember> of an 8:orgid: agent, wording matches the normalizer
    assert t[3] == "Алина Сапарова присоединилась к беседе."  # tags=system from DynamicsBot
    assert t[5] == "[internal] Ерлан Абенов: Проверь блокировку в «Изменение сведений»"
    assert t[6] == "[internal] Алина Сапарова: Ерлан, клиент ждёт ответа"
    assert t[8] == "Спасибо! Вывод заблокирован до обновления данных: Меню → Поручения"  # HTML and &nbsp; stripped
    assert t[9] == "[вложение: screen.png]"  # attachment-only message from the "Customer" application identity
    assert t[11] == "Агент вышел из беседы"
    assert all("ой, не то" not in x for x in t)  # deleted message dropped
    # original timestamps are kept as sent
    assert rec["transcript"][0]["timestamp"] == ts(1) and rec["transcript"][-1]["timestamp"] == ts(180)
    stats = rec["d365"]
    assert stats["skipped"] == {"control": 2, "deleted": 1}  # bot <addmember> and <topicupdate> are not join/leave events of people
    assert stats["internal_messages"] == 2 and stats["private_ignored"] is False
    assert stats["agents"] == ["Алина Сапарова"]  # the supervisor only consulted privately
    assert stats["source_field"] == "annotation.documentbody" and stats["conversation_id_source"] == "msdyn_ocliveworkitemid"
    assert stats["transcript_id"] == "9b1d3f50-0000-4000-8000-00000000f365"
    assert stats["annotation_id"] == "3a5c7e90-0000-4000-8000-00000000a365"
    assert stats["speakers"] == {"client": 3, "bot": 1, "system": 5, "operator": 3}


def test_customer_message_tagged_private_stays_client():
    # the Microsoft sample shows a customer line tagged "private,parentID-..."; only agent messages become [internal]
    rec = parse_transcript({"messages": ru_chat()[:3]})
    assert speakers(rec) == ["client", "bot"]
    assert texts(rec)[0] == "Здравствуйте, не проходит вывод средств"


def test_private_messages_skip_mode():
    rec = parse_transcript({"messages": ru_chat()}, private_messages="skip")
    assert not any(x.startswith("[internal]") for x in texts(rec))
    assert rec["d365"]["skipped"]["private"] == 2 and rec["d365"]["internal_messages"] == 0


def test_control_messages_modes():
    all_events = parse_transcript({"messages": ru_chat()}, control_messages="all")
    t = texts(all_events)
    assert t[0] == "Бот присоединился к беседе" and "Служебное событие: topicupdate" in t
    none = parse_transcript({"messages": ru_chat()}, control_messages="none")
    assert "Агент присоединился к беседе" not in texts(none)
    assert none["d365"]["skipped"]["control"] == 4


def test_private_tag_on_every_agent_message_is_ignored():
    messages = [msg(0, "Здравствуйте, вопрос по тарифу", user=CUSTOMER),
                msg(30, "Здравствуйте! Меня зовут Алина, тариф виден в «Кабинете».", user=AGENT, tags="private"),
                msg(60, "Хорошего дня!", user=AGENT, tags="private")]
    rec = parse_transcript({"messages": messages})
    assert speakers(rec) == ["client", "operator", "operator"]
    assert rec["d365"]["private_ignored"] is True


def test_unlabelled_users_first_speaker_is_client():
    named_customer = {"displayName": "Иван Петров", "id": "c-2"}
    messages = [msg(0, "Добрый день, как пополнить счёт?", user=named_customer),
                msg(20, "Здравствуйте! Меня зовут Алина. Через «Пополнить».", user=AGENT),
                msg(40, "Спасибо", user=named_customer)]
    rec = parse_transcript({"messages": messages})
    assert speakers(rec) == ["client", "operator", "client"]


def test_public_tag_identifies_agent_even_when_customer_speaks_later():
    named_customer = {"displayName": "Иван Петров", "id": "c-2"}
    messages = [msg(0, "Здравствуйте! Меня зовут Алина, чем помочь?", user=AGENT, tags="public"),
                msg(20, "Как пополнить счёт?", user=named_customer)]
    assert speakers(parse_transcript({"messages": messages})) == ["operator", "client"]


def test_orgid_target_identifies_agent():
    named_customer = {"displayName": "Иван Петров", "id": "c-2"}
    messages = [msg(0, "Здравствуйте! Меня зовут Алина, чем помочь?", user=AGENT),
                control(1, "addmember", f"8:orgid:{AGENT['id']}"),
                msg(20, "Как пополнить счёт?", user=named_customer)]
    assert speakers(parse_transcript({"messages": messages})) == ["operator", "system", "client"]


def test_from_customer_tag_and_phone_identity():
    named_customer = {"displayName": "Иван Петров", "id": "c-2"}
    messages = [msg(0, "Здравствуйте! Меня зовут Алина.", user=AGENT),
                msg(10, "Как пополнить счёт?", user=named_customer, tags="ChannelId-lcw,FromCustomer")]
    assert speakers(parse_transcript({"messages": messages})) == ["operator", "client"]
    sms = [{"content": "Баланс?", "createdDateTime": ts(0), "from": {"phone": {"id": "+77010000000", "displayName": "+7 701 000 00 00"}}},
           {"content": "Здравствуйте! Меня зовут Алина.", "createdDateTime": ts(5), "from": {"user": AGENT}}]
    assert speakers(parse_transcript({"messages": sms})) == ["client", "operator"]


def test_participants_override():
    messages = [msg(0, "Здравствуйте! Меня зовут Алина.", user=AGENT),
                msg(10, "Как пополнить счёт?", user={"displayName": "Иван Петров", "id": "c-2"})]
    rec = parse_transcript({"messages": messages}, participants={"Иван Петров": "customer", AGENT["id"]: "agent"})
    assert speakers(rec) == ["operator", "client"]
    with pytest.raises(DynamicsParseError) as e:
        parse_transcript({"messages": messages}, participants={"Иван Петров": "boss"})
    assert e.value.code == "invalid_participants"


def test_simplified_and_organizer_messages():
    simple = [{"role": "customer", "content": "Здравствуйте", "timestamp": "00:00"},
              {"role": "bot", "content": "Соединяю"},
              {"role": "user", "content": "Здравствуйте, меня зовут Алина"},  # D365 "user" = agent (C1)
              {"speaker": "end user", "text": "Спасибо"},
              {"speaker": "system", "text": "Оператор закрыл чат"},
              {"role": "agent", "content": "внутренняя заметка", "private": True}]
    rec = parse_transcript({"messages": simple})
    assert speakers(rec) == ["client", "bot", "operator", "client", "system", "system"]
    assert texts(rec)[-1] == "[internal] внутренняя заметка"
    organizer = {"conversation_id": "p9-ru-001", "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Привет"},
                                                                  {"timestamp": "00:20", "speaker": "operator", "text": "Здравствуйте"}]}
    rec2 = parse_transcript({"transcript": organizer})
    assert rec2["conversation_id"] == "p9-ru-001" and speakers(rec2) == ["client", "operator"]
    assert rec2["transcript"][1]["timestamp"] == "00:20"


def test_bot_adaptive_card_text():
    card = {"type": "AdaptiveCard", "body": [{"type": "TextBlock", "text": "Выберите тему"},
                                             {"type": "ActionSet", "actions": [{"title": "Вывод средств"}, {"title": "Тарифы"}]}]}
    messages = [msg(0, "Здравствуйте", user=CUSTOMER), msg(2, json.dumps(card, ensure_ascii=False), app=BOT),
                msg(5, "", app=BOT, attachments=[{"contentType": "application/vnd.microsoft.card.hero", "content": {"title": "Меню", "text": "Чем помочь?"}}])]
    t = texts(parse_transcript({"messages": messages}))
    assert t[1] == "Выберите тему Вывод средств Тарифы" and t[2] == "Меню Чем помочь?"


def test_epoch_id_timestamp_fallback_and_literal_newlines():
    epoch_ms = int((T0 + timedelta(seconds=30, milliseconds=220)).timestamp() * 1000)
    m = {"id": str(epoch_ms), "content": "Great! Just give me a moment. \\nWhich product?", "from": {"user": AGENT}, "tags": "public"}
    rec = parse_transcript({"messages": [msg(0, "Hi", user=CUSTOMER), m]})
    assert rec["transcript"][1]["timestamp"] == "2026-11-07T09:00:30.220+00:00"
    assert rec["transcript"][1]["text"] == "Great! Just give me a moment. Which product?"
    assert parse_transcript({"messages": [{"content": "x", "from": {"user": CUSTOMER}, "createdDateTime": 1793955600}]})["transcript"][0]["timestamp"] \
        == "2026-11-06T09:00:00.000+00:00"  # epoch seconds


# ----------------------------------------------------------------------------- input variants


def test_documentbody_variants_decode_to_the_same_record():
    messages = en_chat()
    raw = json.dumps(messages, ensure_ascii=False).encode("utf-8")
    b64 = base64.b64encode(raw).decode()
    expected = parse_transcript({"messages": messages})["transcript"]
    variants = [
        {"documentbody": b64},
        {"documentBody": b64},
        {"annotation": {"DocumentBody": b64}},
        {"annotation": b64},
        {"documentbody": "data:application/json;base64," + b64},
        {"documentbody": base64.urlsafe_b64encode(raw).decode().rstrip("=")},
        {"documentbody": "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))},
        {"documentbody": base64.b64encode(codecs.BOM_UTF8 + raw).decode()},
        {"documentbody": base64.b64encode(json.dumps(messages).encode("utf-16-le")).decode()},
        {"documentbody": base64.b64encode(json.dumps(messages).encode("utf-16")).decode()},
        {"documentbody": base64.b64encode(gzip.compress(raw)).decode()},
        {"documentbody": base64.b64encode(json.dumps(json.dumps(messages)).encode()).decode()},  # JSON encoded twice
        {"documentbody": json.dumps(messages)},  # already decoded text
        {"transcript": messages},
        {"transcript": {"messages": messages}},
        {"transcript": {"Content": json.dumps(messages)}},
        {"transcript": json.dumps(messages)},
        {"messages": json.dumps(messages)},
    ]
    for payload in variants:
        assert parse_transcript(payload)["transcript"] == expected, payload.keys()
    assert parse_transcript(messages)["transcript"] == expected  # a bare list
    assert parse_transcript(json.dumps({"messages": messages}))["transcript"] == expected  # a JSON string


def test_decode_documentbody_roundtrip_and_errors():
    assert decode_documentbody(encode_documentbody(sample_messages())) == sample_messages()
    for bad, code in (("not base64 at all!", "invalid_base64"), (base64.b64encode(b"hello").decode(), "invalid_transcript_json"),
                      (base64.b64encode(b"\xff\xfe\xfd\x00\x01").decode(), "invalid_transcript_json"), ("   ", "empty_transcript"),
                      (12, "invalid_base64")):
        with pytest.raises(DynamicsParseError) as e:
            decode_documentbody(bad)
        assert e.value.code == code


def test_parse_errors():
    cases = [
        ({}, "missing_transcript"),
        ({"evaluate": True}, "missing_transcript"),
        ({"annotation": {"objecttypecode": "msdyn_ocliveworkitem", "documentbody": encode_documentbody(en_chat())}}, "not_a_transcript"),
        ({"objecttypecode": "incident", "documentbody": encode_documentbody(en_chat())}, "not_a_transcript"),
        ({"messages": []}, "empty_transcript"),
        ({"messages": [control(0, "addmember", f"8:orgid:{AGENT['id']}"), msg(1, "x", app=DYNAMICS_BOT, tags="system")]}, "empty_transcript"),
        ({"transcript": {"foo": "bar"}}, "invalid_transcript_json"),
        ({"documentbody": "@@@"}, "invalid_base64"),
    ]
    for payload, code in cases:
        with pytest.raises(DynamicsParseError) as e:
            parse_transcript(payload)
        assert e.value.code == code, (payload, e.value.code)
        assert e.value.status == 422 and e.value.as_detail()["code"] == code
    with pytest.raises(DynamicsParseError) as e:
        parse_transcript({"messages": en_chat()}, order="random")
    assert e.value.code == "invalid_option" and e.value.as_detail()["errors"][0]["field"] == "order"


def test_size_limits(monkeypatch):
    monkeypatch.setattr(dyn, "MAX_MESSAGES", 3)
    with pytest.raises(DynamicsParseError) as e:
        parse_transcript({"messages": en_chat()})
    assert e.value.code == "payload_too_large" and e.value.status == 413
    monkeypatch.setattr(dyn, "MAX_DOCUMENTBODY_CHARS", 50)
    with pytest.raises(DynamicsParseError) as e:
        parse_transcript({"documentbody": encode_documentbody(en_chat())})
    assert e.value.status == 413


# ----------------------------------------------------------------------------- ids, channel, language, order


def test_conversation_id_sources():
    body = encode_documentbody(en_chat())
    assert parse_transcript({"documentbody": body}, conversation_id="{7F0C2A4E-1B3D-4C5E-9F60-000000000001}")["conversation_id"] == CID
    rec = parse_transcript({"documentbody": body, "_msdyn_liveworkitemidid_value": CID.upper()})
    assert rec["conversation_id"] == CID and rec["d365"]["conversation_id_source"] == "_msdyn_liveworkitemidid_value"
    assert parse_transcript({"transcript": {"liveWorkItemId": CID, "messages": en_chat()}})["conversation_id"] == CID
    assert parse_transcript({"conversation": {"activityid": CID}, "messages": en_chat()})["conversation_id"] == CID
    assert parse_transcript({"msdyn_liveworkitemidid": {"id": CID, "logicalName": "msdyn_ocliveworkitem"}, "messages": en_chat()})["conversation_id"] == CID
    by_transcript = parse_transcript(annotation_payload(en_chat()))
    assert by_transcript["conversation_id"] == "9b1d3f50-0000-4000-8000-00000000f365"
    assert by_transcript["d365"]["conversation_id_source"] == "_objectid_value"
    a, b = parse_transcript({"messages": en_chat()}), parse_transcript({"documentbody": body})
    assert a["conversation_id"] == b["conversation_id"] and a["conversation_id"].startswith("d365-")
    assert a["d365"]["conversation_id_source"] == "content_hash"
    assert parse_transcript({"messages": kz_chat()})["conversation_id"] != a["conversation_id"]


def test_normalize_conversation_id():
    assert normalize_conversation_id("{7F0C2A4E-1B3D-4C5E-9F60-000000000001}") == CID
    assert normalize_conversation_id("p1-ru-001") == "p1-ru-001"
    assert normalize_conversation_id("  ") is None and normalize_conversation_id(None) is None
    for bad in ("bad id with spaces", "x" * 65, "-leading", "a/b"):
        with pytest.raises(DynamicsParseError) as e:
            normalize_conversation_id(bad)
        assert e.value.code == "invalid_conversation_id"


def test_channel_detection():
    body = {"messages": en_chat()}
    assert parse_transcript(body)["channel"] == "agent chat"
    assert parse_transcript({**body, "msdyn_channel": "192370000"})["channel"] == "voice call"
    assert parse_transcript({**body, "msdyn_channel": 192440000})["channel"] == "voice call"
    assert parse_transcript({**body, "msdyn_channel": "192360000"})["channel"] == "agent chat"
    assert parse_transcript({**body, "conversation": {"msdyn_channel@OData.Community.Display.V1.FormattedValue": "Voice"}})["channel"] == "voice call"
    assert parse_transcript({**body, "msdyn_transcripttargetidtype": "phonecall"})["channel"] == "voice call"
    assert parse_transcript(body, audio_url="https://example.org/rec.mp3")["channel"] == "voice call"
    assert parse_transcript(body, channel="voice")["channel"] == "voice call"
    rec = parse_transcript({**body, "msdyn_channel": "192370000"}, channel="Live chat")
    assert rec["channel"] == "agent chat" and rec["d365"]["channel_source"] == "channel"
    with pytest.raises(DynamicsParseError) as e:
        parse_transcript(body, channel="pigeon post")
    assert e.value.code == "invalid_channel"


def test_normalize_channel_values():
    assert normalize_channel("agent chat") == "agent chat" and normalize_channel("voice call") == "voice call"
    assert normalize_channel("192360000,192370000") == "voice call"
    assert normalize_channel([192340000]) == "agent chat"
    assert normalize_channel("WhatsApp") == "agent chat" and normalize_channel("Телефонный звонок") == "voice call"
    assert normalize_channel(None) is None and normalize_channel("") is None and normalize_channel("??") is None


def test_language_detection_rules():
    assert detect_language(["Сәлеметсіз бе! Менің атым Айгүл."]) == "KZ"
    assert detect_language(["Рақмет"]) == "KZ"
    assert detect_language(["Здравствуйте, чем могу помочь?"]) == "RU"
    long_ru = "Здравствуйте! Меня зовут Нұрлан. " + "Сейчас проверю вашу заявку на вывод средств и вернусь с ответом. " * 6
    assert detect_language([long_ru]) == "RU"  # a Kazakh name in Russian text does not make it Kazakh
    assert detect_language(["Hello, how can I help?", "Freedom Broker"]) == "EN"
    assert detect_language(["Hello, I am Иван"]) == "EN"
    assert detect_language(["12345 :)"]) == "EN"


def test_language_in_records_and_override():
    assert parse_transcript({"messages": kz_chat()})["language"] == "KZ"
    assert parse_transcript({"messages": en_chat()})["language"] == "EN"
    assert parse_transcript({"messages": ru_chat()})["language"] == "RU"
    # the bot greets bilingually in every chat: the customer and the agent decide the language
    bilingual = [msg(0, "Сәлеметсіз бе! Здравствуйте! Қош келдіңіз!", app=BOT), msg(1, "Как пополнить счёт?", user=CUSTOMER),
                 msg(2, "Здравствуйте! Меня зовут Алина, через «Пополнить».", user=AGENT, tags="public")]
    assert parse_transcript({"messages": bilingual})["language"] == "RU"
    rec = parse_transcript({"messages": en_chat()}, language="kk-KZ")
    assert rec["language"] == "KZ" and rec["d365"]["language_detected"] is False
    kz = parse_transcript({"messages": kz_chat()})
    assert "Агент қосылды" in texts(kz)  # control events are rendered in the detected language
    en = parse_transcript({"messages": [control(0, "addmember", f"8:orgid:{AGENT['id']}"), *en_chat()]})
    assert texts(en)[0] == "Agent joined the conversation"


def test_normalize_language_values():
    for value, code in (("ru", "RU"), ("ru-RU", "RU"), ("KK", "KZ"), ("kz", "KZ"), ("kk_KZ", "KZ"), ("en-US", "EN"), ("English", "EN"),
                        (1049, "RU"), ("1087", "KZ"), (1033, "EN")):
        assert normalize_language(value) == code
    assert normalize_language(None) is None and normalize_language("") is None
    for bad in ("fr", 1036, "klingon"):
        with pytest.raises(DynamicsParseError) as e:
            normalize_language(bad)
        assert e.value.code == "invalid_language"


def test_order_auto_keep_chronological():
    messages = en_chat()
    newest_first = list(reversed(messages))
    auto = parse_transcript({"messages": newest_first})
    assert texts(auto)[0] == "Hi, I cannot log in to the app" and auto["d365"]["order"] == "reversed"
    kept = parse_transcript({"messages": newest_first}, order="keep")
    assert texts(kept)[0].startswith("Please use Forgot password") and kept["d365"]["order"] == "kept"
    shuffled = [messages[1], messages[0], messages[3], messages[2]]
    assert parse_transcript({"messages": shuffled})["d365"]["order"] == "kept"  # not entirely newest-first: original order
    chrono = parse_transcript({"messages": shuffled}, order="chronological")
    assert texts(chrono) == texts(parse_transcript({"messages": messages})) and chrono["d365"]["order"] == "sorted"


# ----------------------------------------------------------------------------- endpoint fixtures


class FakeActor:
    """Stands in for the security middleware (pure ASGI): puts request.state.actor like cqa.security would."""

    def __init__(self, app, role: str):
        self.app, self.role = app, role

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["actor"] = {"role": self.role, "name": "Тест", "request_id": "t1"}
        await self.app(scope, receive, send)


async def _envelope(request: Request, exc: HTTPException):  # same as cqa.main
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    started: list[dict] = []

    def fake_start(conversation_ids, checklist_version_id, *, provider, model, mode, base_version_id, only_codes, extra_params=None):
        job_id = f"j_test{len(started) + 1:02d}"
        started.append({"conversation_ids": list(conversation_ids), "checklist_version_id": checklist_version_id, "provider": provider,
                        "model": model, "mode": mode, "base_version_id": base_version_id, "only_codes": only_codes,
                        "extra_params": extra_params})
        with Session() as s:  # like the real runner: a queued job row
            s.add(Job(id=job_id, status="queued", total=len(conversation_ids),
                      params={"checklist_version_id": checklist_version_id, "conversation_ids": list(conversation_ids), "provider": provider}))
            s.commit()
        return job_id

    monkeypatch.setattr(runner, "start_evaluation_job", fake_start)
    for var in ("WEB_BASE_URL", "PUBLIC_API_URL", integ.INTEGRATION_KEY_ENV):
        monkeypatch.delenv(var, raising=False)
    for field in ("web_base_url", "public_api_url", "integrations_api_key"):  # if cqa.config grows these settings later
        if field in type(settings).model_fields:
            monkeypatch.setattr(settings, field, None)
    monkeypatch.setattr(settings, "daily_budget_usd", 75.0)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 2)
    monkeypatch.setattr(settings, "default_provider", "anthropic")
    yield SimpleNamespace(Session=Session, started=started)
    engine.dispose()


def make_client(env, role: str | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(integ.router)
    app.add_exception_handler(HTTPException, _envelope)
    if role:
        app.add_middleware(FakeActor, role=role)

    def override():
        s = env.Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def seed_version(env) -> int:
    with env.Session() as s:
        return create_checklist(s, SEED, change_note="v1", compile_missing=False).id


def expected_estimate(env, conversation_id: str, version_id: int, provider: str = "anthropic") -> float:
    """The per-dialog, token-based estimate (cqa.jobs.estimate) of a stored conversation: what the ingest must reserve."""
    with env.Session() as s:
        conv, version = s.get(Conversation, conversation_id), s.get(ChecklistVersion, version_id)
        return estimator.estimate_run(s, [conv], version, provider=provider, model=None).total_usd


URL = "/api/integrations/dynamics/transcripts"


# ----------------------------------------------------------------------------- endpoint: ingest


def test_router_paths():
    paths = {r.path for r in integ.router.routes}
    assert integ.router.prefix == "/api/integrations"
    assert paths == {"/api/integrations/dynamics/transcripts", "/api/integrations/dynamics/manifest"}


def test_ingest_without_evaluation(env):
    client = make_client(env)
    r = client.post(URL, json=annotation_payload(ru_chat(), conversation_id=CID.upper()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversation_id"] == CID and body["turns"] == 12 and body["language"] == "RU" and body["job_id"] is None
    assert body["card_url"] == f"https://cqa-freedom-pi.vercel.app/#/conversations/{CID}"
    assert body["embed_url"] == f"https://cqa-freedom-pi.vercel.app/?embed=1&conversation={CID}"
    assert body["created"] is True and body["channel"] == "agent chat" and body["evaluation"] == {"status": "not_requested"}
    assert body["operator_name"] == "Алина"  # detected from the greeting by the normalizer, as for every source
    assert body["first_response_seconds"] == 20.0  # agent joined at 40 s, first agent line at 60 s
    assert body["d365"]["agents"] == ["Алина Сапарова"] and body["warnings"] == []
    assert body["agent_name"] == "Алина Сапарова"  # source-system identity, kept apart from the greeting-detected name
    assert env.started == []
    with env.Session() as s:
        conv = s.get(Conversation, CID)
        assert conv.turn_count == 12 and conv.language == "RU" and conv.channel == "agent chat"
        assert conv.agent_name == "Алина Сапарова" and conv.operator_name == "Алина"
        assert [t.speaker for t in conv.turns][:5] == ["client", "bot", "system", "system", "operator"]
        assert conv.turns[0].timestamp_raw == ts(1) and conv.turns[0].offset_seconds == 0.0
        assert conv.turns[4].offset_seconds == 59.0 and conv.turns[4].segment == "operator"
        assert conv.meta["source"] == "dynamics365" and conv.meta["d365"]["content_hash"]
        assert conv.meta["speakers"]["operator"] == 3 and conv.meta["operator_start_idx"] == 4
        ds = s.get(Dataset, conv.dataset_id)
        assert ds.group_id == "D365" and ds.source == "dynamics365" and ds.record_count == 1
        assert "Customer" not in json.dumps(conv.meta, ensure_ascii=False)  # no customer display names stored


def test_web_base_url_override(env, monkeypatch):
    monkeypatch.setenv("WEB_BASE_URL", "https://cqa.example.org/")
    body = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "d365-test-1"}).json()
    assert body["card_url"] == "https://cqa.example.org/#/conversations/d365-test-1"
    assert body["embed_url"] == "https://cqa.example.org/?embed=1&conversation=d365-test-1"


def test_repost_is_an_upsert(env):
    client = make_client(env)
    first = client.post(URL, json=annotation_payload(kz_chat(), conversation_id=CID)).json()
    second = client.post(URL, json=annotation_payload(kz_chat(), conversation_id=CID)).json()
    assert first["created"] is True and second["created"] is False and second["turns"] == first["turns"] == 5
    assert second["language"] == "KZ"
    changed = client.post(URL, json={"conversation_id": CID, "messages": [*kz_chat(), msg(200, "Тағы бір сұрақ", user=CUSTOMER)]}).json()
    assert changed["turns"] == 6
    with env.Session() as s:
        assert len(s.get(Conversation, CID).turns) == 6
        assert s.execute(select(Dataset)).scalars().one().record_count == 1


def test_ingest_voice_call(env):
    call = [msg(0, "Алло, здравствуйте", user=CUSTOMER), control(5, "addmember", f"8:orgid:{AGENT['id']}"),
            msg(10, "Здравствуйте! Компания Freedom, меня зовут Алина.", user=AGENT, tags="public")]
    body = make_client(env).post(URL, json={"messages": call, "msdyn_channel": "192370000", "conversation_id": "call-1",
                                            "audio_url": "https://example.org/rec.mp3"}).json()
    assert body["channel"] == "voice call"
    with env.Session() as s:
        conv = s.get(Conversation, "call-1")
        assert conv.audio_url == "https://example.org/rec.mp3"
        assert conv.meta["operator_start_idx"] == 2  # the rendered "Агент присоединился" event opens the operator segment
        assert [t.offset_seconds for t in conv.turns] == [0.0, 5.0, 10.0]


def test_ingest_duplicates_use_real_timestamps(env):
    messages = [msg(0, "Спасибо", user=CUSTOMER), msg(1, "Спасибо", user=CUSTOMER), msg(90, "Спасибо", user=CUSTOMER),
                msg(100, "Пожалуйста! Меня зовут Алина.", user=AGENT, tags="public")]
    make_client(env).post(URL, json={"messages": messages, "conversation_id": "dup-1"})
    with env.Session() as s:
        conv = s.get(Conversation, "dup-1")
        assert [t.is_duplicate for t in conv.turns] == [False, True, False, False]
        assert conv.meta["duplicates"] == 1


def test_warnings_for_fallback_ids_and_reversed_order(env):
    body = make_client(env).post(URL, json={"messages": list(reversed(en_chat()))}).json()
    assert body["conversation_id"].startswith("d365-")
    assert any("хэш содержимого" in w for w in body["warnings"]) and any("развёрнут" in w for w in body["warnings"])
    only_bot = make_client(env).post(URL, json={"messages": [msg(0, "Здравствуйте", user=CUSTOMER), msg(1, "Бот на связи", app=BOT)]}).json()
    assert "В стенограмме нет реплик оператора." in only_bot["warnings"]


def test_conflict_with_non_d365_conversation(env):
    with env.Session() as s:
        s.add(Conversation(id="p1-ru-001", channel="agent chat", language="RU", turn_count=0, meta={}))
        s.commit()
    client = make_client(env)
    r = client.post(URL, json={"conversation_id": "p1-ru-001", "messages": en_chat()})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conversation_exists"
    with env.Session() as s:
        assert s.get(Conversation, "p1-ru-001").language == "RU"
    r = client.post(URL, json={"conversation_id": "p1-ru-001", "messages": en_chat(), "overwrite": True})
    assert r.status_code == 200 and r.json()["language"] == "EN"
    with env.Session() as s:
        assert s.get(Conversation, "p1-ru-001").meta["source"] == "dynamics365"


# ----------------------------------------------------------------------------- endpoint: evaluation


def test_evaluate_starts_a_job(env):
    vid = seed_version(env)
    r = make_client(env).post(URL, json=annotation_payload(ru_chat(), conversation_id=CID, evaluate=True))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == "j_test01"
    est = expected_estimate(env, CID, vid)
    assert est >= 0.18  # never below the historical flat rate of a chat (8 criteria are judged)
    assert body["evaluation"] == {"status": "started", "job_id": "j_test01", "checklist_version_id": vid, "provider": "anthropic",
                                  "model": None, "estimated_cost_usd": est}
    assert env.started == [{"conversation_ids": [CID], "checklist_version_id": vid, "provider": "anthropic", "model": None, "mode": "full",
                            "base_version_id": None, "only_codes": None,
                            "extra_params": {"estimated_cost_usd": est, "source": "dynamics365", "paid": True}}]


def test_job_starter_without_extra_params(env, monkeypatch):
    seed_version(env)
    calls = []

    def old_start(conversation_ids, checklist_version_id, *, provider, model, mode, base_version_id, only_codes):
        calls.append((conversation_ids, provider))
        return "j_old"

    monkeypatch.setattr(runner, "start_evaluation_job", old_start)  # the runner signature before extra_params existed
    body = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "o1", "evaluate": True}).json()
    assert body["job_id"] == "j_old" and calls == [(["o1"], "anthropic")]


def test_evaluate_provider_selection(env):
    vid = seed_version(env)
    client = make_client(env)
    body = client.post(URL, json={"messages": en_chat(), "conversation_id": "e1", "evaluate": True, "provider": "local"}).json()
    assert body["evaluation"]["provider"] == "local" and body["evaluation"]["estimated_cost_usd"] == 0.0
    body = client.post(URL, json={"messages": kz_chat(), "conversation_id": "e2", "evaluate": True, "provider": "anthropic:claude-sonnet-5-5",
                                  "checklist_version_id": vid}).json()
    assert (body["evaluation"]["provider"], body["evaluation"]["model"]) == ("anthropic", "claude-sonnet-5-5")
    with env.Session() as s:
        s.add(Setting(key="default_provider", value="ensemble"))
        s.commit()
    with env.Session() as s:  # make room for a third job (fake jobs stay queued)
        for job in s.execute(select(Job)).scalars():
            job.status = "completed"
        s.commit()
    body = client.post(URL, json={"messages": ru_chat(), "conversation_id": "e3", "evaluate": "true"}).json()
    assert body["evaluation"]["provider"] == "ensemble"
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "e4", "evaluate": True, "provider": "gpt-9"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_provider"
    assert r.json()["error"]["errors"][0]["field"] == "provider"
    with env.Session() as s:
        assert s.get(Conversation, "e4") is None  # validated before anything is written
    # the offline judge is routed natively by cqa.judge.runner._members (never billed to Claude)
    body = client.post(URL, json={"messages": en_chat(), "conversation_id": "e5", "evaluate": True, "provider": "fake"}).json()
    assert body["evaluation"]["provider"] == "fake" and body["evaluation"]["estimated_cost_usd"] == 0.0


def test_evaluate_needs_a_checklist(env):
    client = make_client(env)
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "c1", "evaluate": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_checklist"
    seed_version(env)
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "c1", "evaluate": True, "checklist_version_id": 999})
    assert r.status_code == 404 and r.json()["error"]["code"] == "checklist_version_not_found"
    with env.Session() as s:
        assert s.get(Conversation, "c1") is None
    assert env.started == []


def test_unchanged_transcript_is_not_evaluated_twice(env):
    vid = seed_version(env)
    client = make_client(env)
    payload = annotation_payload(en_chat(), conversation_id=CID, evaluate=True)
    assert client.post(URL, json=payload).json()["job_id"] == "j_test01"
    again = client.post(URL, json=payload).json()  # the first job is still queued
    assert again["job_id"] is None
    assert again["evaluation"] == {"status": "skipped_unchanged", "checklist_version_id": vid, "evaluation_id": None,
                                   "existing_job_id": "j_test01", "evaluation_status": "queued"}
    with env.Session() as s:  # the job finished and produced an evaluation
        s.get(Job, "j_test01").status = "completed"
        s.add(Evaluation(conversation_id=CID, checklist_version_id=vid, status="completed", job_id="j_test01", cost_usd=0.17))
        s.commit()
    done = client.post(URL, json=payload).json()
    assert done["evaluation"]["status"] == "skipped_unchanged" and done["evaluation"]["evaluation_status"] == "completed"
    forced = client.post(URL, json={**payload, "force": True}).json()
    assert forced["evaluation"]["status"] == "started" and forced["job_id"] == "j_test02"
    changed = client.post(URL, json={**annotation_payload([*en_chat(), msg(200, "One more thing", user=CUSTOMER)]),
                                     "conversation_id": CID, "evaluate": True}).json()
    assert changed["evaluation"]["status"] == "started"
    assert len(env.started) == 3


def test_failed_evaluation_is_retried(env):
    vid = seed_version(env)
    client = make_client(env)
    payload = {"messages": en_chat(), "conversation_id": "f1", "evaluate": True}
    client.post(URL, json=payload)
    with env.Session() as s:
        s.get(Job, "j_test01").status = "completed"
        s.add(Evaluation(conversation_id="f1", checklist_version_id=vid, status="failed", job_id="j_test01"))
        s.commit()
    assert client.post(URL, json=payload).json()["evaluation"]["status"] == "started"


def test_budget_guard(env, monkeypatch):
    vid = seed_version(env)
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    with env.Session() as s:
        s.add(Conversation(id="old", channel="agent chat", language="RU", turn_count=0, meta={"source": "dynamics365"}))
        s.flush()
        s.add(Evaluation(conversation_id="old", checklist_version_id=vid, status="completed", cost_usd=0.9))
        s.add(Evaluation(conversation_id="old", checklist_version_id=vid, status="completed", cost_usd=50.0,
                         created_at=datetime.now(UTC) - timedelta(hours=30)))  # outside the 24 h window
        s.commit()
    r = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "b1", "evaluate": True})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0  # cqa.jobs.guards: when the 24 h window frees enough
    err = r.json()["error"]
    assert err["code"] == "budget_exceeded" and err["ingested"] is True and err["conversation_id"] == "b1"
    assert err["budget"]["spent_24h"] == 0.9 and err["budget"]["limit"] == 1.0 and err["estimate_usd"] == expected_estimate(env, "b1", vid)
    assert err["card_url"].endswith("/#/conversations/b1") and err["turns"] == 4 and err["message"]
    assert env.started == []
    with env.Session() as s:
        assert s.get(Conversation, "b1").turn_count == 4  # the transcript is kept for a later evaluation
    local = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "b1", "evaluate": True, "provider": "local"})
    assert local.status_code == 200  # a $0 run is never blocked by the budget
    monkeypatch.setattr(settings, "daily_budget_usd", 0.0)  # 0 disables the cap
    assert make_client(env).post(URL, json={"messages": kz_chat(), "conversation_id": "b1", "evaluate": True}).status_code == 200


def test_concurrent_jobs_guard(env):
    seed_version(env)
    with env.Session() as s:
        s.add_all([Job(id="j_a", status="running"), Job(id="j_b", status="queued")])
        s.commit()
    r = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "g1", "evaluate": True})
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "too_many_jobs" and err["running"] == 2 and err["ingested"] is True and env.started == []


def test_local_guard_fallback_without_platform_guards(env, monkeypatch):
    vid = seed_version(env)
    monkeypatch.setattr(integ, "_platform_guards", lambda: None)
    with env.Session() as s:
        s.add_all([Job(id="j_a", status="running"), Job(id="j_b", status="queued")])
        s.commit()
    r = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "l1", "evaluate": True})
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    assert r.json()["error"]["code"] == "too_many_jobs" and r.json()["error"]["running_jobs"] == 2
    with env.Session() as s:
        for job in s.execute(select(Job)).scalars():
            job.status = "completed"
        s.add(Conversation(id="old", channel="agent chat", language="RU", turn_count=0, meta={"source": "dynamics365"}))
        s.flush()
        s.add(Evaluation(conversation_id="old", checklist_version_id=vid, status="completed", cost_usd=0.9))
        s.commit()
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    r = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "l1", "evaluate": True})
    assert r.status_code == 429 and r.headers["retry-after"] == "3600"
    err = r.json()["error"]
    assert err["code"] == "budget_exceeded" and err["budget"] == {"spent_24h": 0.9, "limit": 1.0, "remaining": 0.1,
                                                                  "estimate": expected_estimate(env, "l1", vid)}
    assert make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "l1", "evaluate": True,
                                            "provider": "local"}).status_code == 200


def test_job_start_failure_is_reported(env, monkeypatch):
    seed_version(env)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(runner, "start_evaluation_job", broken)
    r = make_client(env).post(URL, json={"messages": en_chat(), "conversation_id": "s1", "evaluate": True})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "evaluation_not_started" and err["ingested"] is True and "database is locked" in err["message"]


# ----------------------------------------------------------------------------- endpoint: request errors, permissions, key


def test_request_errors(env, monkeypatch):
    client = make_client(env)
    r = client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_json"
    r = client.post(URL, content=b"", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "empty_body"
    r = client.post(URL, json=[msg(0, "hi", user=CUSTOMER)])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_body"
    r = client.post(URL, json={"messages": en_chat(), "evaluate": "maybe", "private_messages": "bogus"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and {e["field"] for e in err["errors"]} == {"evaluate", "private_messages"}
    r = client.post(URL, json={"annotation": {"documentbody": "%%% not base64"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_base64"
    assert r.json()["error"]["errors"] == [{"field": "annotation.documentbody", "message": r.json()["error"]["message"]}]
    r = client.post(URL, json={"messages": en_chat(), "audio_url": "ftp://example.org/x.mp3"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_audio_url"
    r = client.post(URL, json={"messages": en_chat(), "language": "fr"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_language"
    r = client.post(URL, json={"annotation": {"objecttypecode": "msdyn_ocliveworkitem", "documentbody": encode_documentbody(en_chat())}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_a_transcript"
    monkeypatch.setattr(integ, "MAX_BODY_BYTES", 100)
    r = client.post(URL, json={"messages": en_chat()})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    with env.Session() as s:
        assert s.execute(select(Conversation)).first() is None


@pytest.mark.parametrize(("role", "evaluate", "status"), [
    ("operator", False, 403), ("visitor", False, 403), ("reviewer", False, 403), ("director", False, 403),
    ("analyst", False, 200), ("lead", False, 200), ("admin", False, 200), ("analyst", True, 200),
])
def test_permissions(env, role, evaluate, status):
    seed_version(env)
    r = make_client(env, role=role).post(URL, json={"messages": en_chat(), "conversation_id": f"perm-{role}", "evaluate": evaluate})
    assert r.status_code == status, r.text
    if status == 403:
        err = r.json()["error"]
        assert err["code"] == "forbidden" and err["role"] == role and err["permission"] == "integrations.ingest"
        with env.Session() as s:
            assert s.get(Conversation, f"perm-{role}") is None


def test_evaluate_permission_is_checked_separately(env, monkeypatch):
    seed_version(env)
    monkeypatch.setattr(integ, "_has_permission", lambda role, permission: permission == "integrations.ingest")
    client = make_client(env, role="director")
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "d1", "evaluate": True})
    assert r.status_code == 403 and r.json()["error"]["permission"] == "evaluations.run"
    assert client.post(URL, json={"messages": en_chat(), "conversation_id": "d1"}).status_code == 200


def test_role_header_is_enforced_without_the_middleware(env):
    client = make_client(env)  # no security middleware: cqa.security.roles.current_actor reads the headers
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "h1"}, headers={"X-CQA-Role": "operator"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "h1"}, headers={"X-CQA-Role": "martian"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_role"
    r = client.post(URL, json={"messages": en_chat(), "conversation_id": "h1"},
                    headers={"X-CQA-Role": "analyst", "X-CQA-User": "Power%20Automate"})
    assert r.status_code == 200


def test_fallback_permissions_without_the_security_package(env, monkeypatch):
    monkeypatch.setattr(integ, "_security", lambda: None)
    assert make_client(env, role="operator").post(URL, json={"messages": en_chat()}).status_code == 403
    assert make_client(env, role="lead").post(URL, json={"messages": en_chat()}).status_code == 200
    assert make_client(env).post(URL, json={"messages": kz_chat()}).status_code == 200  # no actor: demo


def test_integration_key(env, monkeypatch):
    monkeypatch.setenv(integ.INTEGRATION_KEY_ENV, "s3cret-key")
    client = make_client(env)
    payload = {"messages": en_chat(), "conversation_id": "k1"}
    r = client.post(URL, json=payload)
    assert r.status_code == 401 and r.json()["error"]["code"] == "integration_key_required"
    r = client.post(URL, json=payload, headers={integ.INTEGRATION_KEY_HEADER: "wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_integration_key"
    r = client.post(URL, json=payload, headers={integ.INTEGRATION_KEY_HEADER: "s3cret-key"})
    assert r.status_code == 200
    manifest = client.get("/api/integrations/dynamics/manifest").json()
    assert manifest["security"]["integration_key"]["required"] is True
    assert "s3cret-key" not in json.dumps(manifest)


# ----------------------------------------------------------------------------- endpoint: manifest


def test_manifest(env, monkeypatch):
    client = make_client(env)
    r = client.get("/api/integrations/dynamics/manifest")
    assert r.status_code == 200
    m = r.json()
    wr = m["web_resource"]
    assert wr["build_command"] == "pnpm build:single" and wr["output"] == "dist-single/index.html"
    assert wr["working_directory"] == "web" and wr["output_path"] == "web/dist-single/index.html"
    snippet = wr["config_snippet"]
    assert "window.CQA_CONFIG" in snippet and "apiUrl" in snippet and "embed: true" in snippet and "conversation:" in snippet
    assert 'q.get("id")' in snippet and "replace(/[{}]/g" in snippet  # D365 passes the record GUID with braces
    assert m["api_base_url"] == "http://testserver" and m["web_base_url"] == "https://cqa-freedom-pi.vercel.app"
    assert "index.html#?embed=1&conversation=" in wr["loader_html"]
    assert len(m["form_tab"]["steps"]) >= 6 and m["form_tab"]["table"] == "msdyn_ocliveworkitem"
    assert "IFRAME_cqa_card" in m["iframe_alternative"]["form_script"]
    pa = m["power_automate"]
    assert pa["trigger"]["filter_rows"] == "objecttypecode eq 'msdyn_transcript'"
    assert pa["http_action"]["uri"] == "http://testserver/api/integrations/dynamics/transcripts"
    assert "documentbody" in pa["http_action"]["body"]["annotation"]
    assert m["endpoints"]["ingest"]["permission"] == "integrations.ingest"
    assert m["links"]["embed"] == "https://cqa-freedom-pi.vercel.app/?embed=1&conversation={conversation_id}"
    assert m["security"]["integration_key"]["required"] is False
    # the example payload is a working request
    example = pa["example_payload"]
    assert decode_documentbody(example["annotation"]["documentbody"]) == sample_messages()
    posted = client.post(URL, json=example)
    assert posted.status_code == 200 and posted.json()["turns"] == 7 and posted.json()["operator_name"] == "Алина"
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.org/")
    m2 = client.get("/api/integrations/dynamics/manifest").json()
    assert m2["api_base_url"] == "https://api.example.org" and 'apiUrl: data.get("api") || "https://api.example.org"' in m2["web_resource"]["config_snippet"]
    m3 = client.get("/api/integrations/dynamics/manifest", headers={"x-forwarded-proto": "https", "x-forwarded-host": "api.railway.test"})
    assert m3.json()["api_base_url"] == "https://api.example.org"  # the explicit setting wins over proxy headers
    monkeypatch.delenv("PUBLIC_API_URL")
    m4 = client.get("/api/integrations/dynamics/manifest", headers={"x-forwarded-proto": "https", "x-forwarded-host": "api.railway.test"})
    assert m4.json()["api_base_url"] == "https://api.railway.test"


def test_openapi_documents_the_body(env):
    schema = make_client(env).get("/openapi.json").json()
    body = schema["paths"]["/api/integrations/dynamics/transcripts"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert {"annotation", "documentbody", "messages", "evaluate", "checklist_version_id", "provider"} <= set(body["properties"])
