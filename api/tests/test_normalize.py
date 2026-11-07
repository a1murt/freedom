import json
from pathlib import Path

import pytest

from cqa.ingest.adapter import record_from_text
from cqa.ingest.normalize import extract_name, normalize, pack_transcript, parse_clock, parse_iso

DEV = Path("/private/tmp/claude-501/-Users-k4ssym--claude-spares-spare-sCVwL5/02572ec3-fc12-448e-97dd-11d655c1b409/scratchpad/data/dev.json")
pytestmark = pytest.mark.skipif(not DEV.exists(), reason="DEV dataset not available")


def load():
    recs = json.loads(DEV.read_text())["data"]["records"]
    return {r["conversation_id"]: r for r in recs}


def test_parse_timestamps():
    assert parse_clock("01:15") == 75
    assert parse_clock("1:02:03") == 3723
    assert parse_iso("2026-11-03T13:10:00.6388245+00:00") is not None


def test_chat_segments_and_name():
    n = normalize(load()["p1-ru-001"])
    assert n.operator_start_idx == 9
    assert n.turns[8].segment == "pre" and n.turns[9].segment == "operator"
    assert n.operator_name == "Алина"
    assert n.first_response_seconds == 75 - 38
    assert n.meta["escalation_idx"] == 8


def test_kz_name_and_en_name():
    d = load()
    assert normalize(d["p1-kz-002"]).operator_name == "Нұрлан"
    assert normalize(d["p1-en-003"]).operator_name == "Eva"


def test_call_segments_and_duplicates():
    n = normalize(load()["p4-ru-001"])
    joined = next(t for t in n.turns if t.speaker == "system" and "присоединился" in t.text)
    assert n.operator_start_idx == joined.idx + 1
    assert any(t.is_duplicate for t in n.turns)
    assert n.terminal_event == "client_ended_call"
    assert all(t.offset_seconds is not None for t in n.turns)
    # paused interval subtracted: offsets stay monotonic non-decreasing
    offs = [t.offset_seconds for t in n.turns]
    assert all(b >= a - 1e-6 for a, b in zip(offs, offs[1:]))


def test_call_internal_block():
    n = normalize(load()["p4-ru-002"])
    assert any(t.segment == "internal" for t in n.turns)


def test_terminal_events():
    d = load()
    assert normalize(d["p1-ru-018"]).terminal_event == "closed_by_operator"
    assert normalize(d["p1-ru-012"]).terminal_event == "connection_lost"
    assert normalize(d["p1-ru-025"]).terminal_event == "client_left"
    assert normalize(d["p1-ru-006"]).meta["identity_confirmed_event"] is True


def test_pack_transcript_skips_duplicates():
    n = normalize(load()["p4-ru-004"])
    packed = pack_transcript(n.turns)
    assert packed.count("\n") + 1 == sum(1 for t in n.turns if not t.is_duplicate)


def test_record_from_text():
    rec = record_from_text("client: Где тариф?\noperator\tЗдравствуйте, я Алина. Во вкладке Кабинет.\n[02:00] client: Спасибо", "manual-1")
    assert [t["speaker"] for t in rec["transcript"]] == ["client", "operator", "client"]
    assert rec["transcript"][2]["timestamp"] == "02:00"


def test_extract_name_edge_cases():
    assert extract_name("Здравствуйте! Меня зовут Алина.") == "Алина"
    assert extract_name("Hi, I'm Eva. Open the account area.") == "Eva"
    assert extract_name("Привет, где именно ищете?") is None
