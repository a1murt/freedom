"""Round 3, CSV: every CSV export makes formula-like text inert with one shared neutraliser (CSV injection, OWASP).

GET /api/export/evaluations.csv wrote the dialog id, channel, language, operator name, a free-text answer, provider and
model as stored, so an operator name typed through PUT /api/conversations/{id}/operator, e.g. '=HYPERLINK("http://x")',
ran as a formula when a reviewer opened the file in Excel. The registers (violations, decisions) had their own copy of
the apostrophe rule and the audit log export had none; all of them now call cqa.export.csv_safe.safe_cell. Numbers,
flags, dates, the header, the BOM and the column order stay exactly as they were. In-memory SQLite, no network.

The first character is not enough: Excel in ru-RU and kk-KZ splits a double-clicked .csv on ";", where csv.writer's quotes
around a middle cell are plain text, so the operator name 'Иван;=1+1;' gave a '=1+1' cell, and a CR or LF inside a name
started a new row there (rows pasted into a sheet split on a tab the same way). safe_cell now also puts an apostrophe right
after every ";", tab, CR or LF that a formula character follows, and the tests read the files the way those readers do.
"""
from __future__ import annotations

import csv
import io
import itertools
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.db import Base, get_db
from cqa.export import csv_export, csv_safe, registers_csv
from cqa.export.csv_export import header_for, render_answer
from cqa.models import (
    Appeal,
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Decision,
    Evaluation,
    EvaluationCriterion,
)
from cqa.routers import audit as audit_router
from cqa.routers import conversations as conversations_router
from cqa.routers import export as export_router
from cqa.security import middleware as security_mw

HYPERLINK = '=HYPERLINK("http://x")'
SUM = "@SUM(1)"
DDE_ACTOR = "+cmd|' /C calc'!A0"  # an X-CQA-User header any client can send
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")  # OWASP list, spelled out here so the test does not trust the code
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
CODES = ["C1", "C2"]
EXPECTED_HEADER = ["conversation_id", "channel", "language", "operator_name", "C1_answer", "C2_answer", "C1_score", "C2_score",
                   "C1_applicable", "C2_applicable", "C1_final", "C2_final", "total_score", "final_total_score", "critical_error",
                   "provider", "model", "cost_usd", "evaluated_at"]
BOM = b"\xef\xbb\xbf"


def _seed(db: Session) -> int:
    checklist = Checklist(name="Раунд 3: CSV")
    db.add(checklist)
    db.flush()
    version = ChecklistVersion(checklist_id=checklist.id, version_no=1, is_active=True, scoring_rules={}, sections=[])
    db.add(version)
    db.flush()
    crits = {}
    for order, (code, kind, max_points) in enumerate((("C1", "binary", 1.0), ("C2", "scale", 10.0)), start=1):
        crits[code] = Criterion(checklist_version_id=version.id, code=code, order=order, title=f"Критерий {code}",
                                instruction="—", answer_type=kind, max_points=max_points, weight=0.5)
        db.add(crits[code])
    db.flush()
    # id, channel, language, detected operator, provider, model, total, final total, cost, {code: (answer, score, final)}
    dialogs = [
        ("d-1", "agent chat", "RU", "Алина", "fake", "fake-judge", 87.5, 90.0, 0.1234,
         {"C1": ("yes", 1.0, None), "C2": (-5, -5.0, None)}),            # a negative number must stay a number
        ("d-2", "voice call", "KZ", None, "fake", "fake-judge", 40.0, 40.0, 0.0,
         {"C1": ("=1+1", 0.0, "@SUM(2)"), "C2": ("7", 7.0, None)}),      # free-text answers are text cells
        ("+77010000000", "voice call", "RU", "Нұрлан", "-fake", "+model", 100.0, 100.0, 0.05,
         {"C1": ("no", 0.0, None), "C2": (10, 10.0, None)}),             # a phone-number id starts with "+"
    ]
    for cid, channel, language, operator, provider, model, total, final_total, cost, answers in dialogs:
        db.add(Conversation(id=cid, channel=channel, language=language, operator_name=operator))
        db.flush()
        ev = Evaluation(conversation_id=cid, checklist_version_id=version.id, status="completed", provider=provider, model=model,
                        total_score=total, final_total_score=final_total, critical_error=False, cost_usd=cost, completed_at=NOW)
        db.add(ev)
        db.flush()
        for code, (answer, score, final) in answers.items():
            db.add(EvaluationCriterion(evaluation_id=ev.id, criterion_id=crits[code].id, code=code, applicable=True, answer=answer,
                                       score=score, max_points=crits[code].max_points, weight=0.5, final_answer=final,
                                       review_state="changed" if final is not None else "none"))
    db.commit()
    return version.id


def _no_real_database():
    raise AssertionError("the audit trail must not fall back to cqa.db.SessionLocal (api/data/cqa.db) in tests")


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionTest = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionTest() as db:
        version_id = _seed(db)
    monkeypatch.setattr(security_mw, "_default_session_factory", _no_real_database)
    app = FastAPI()
    for module in (conversations_router, export_router, audit_router):
        app.include_router(module.router)

    def override_get_db():
        db = SessionTest()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    security_mw.install(app, session_factory=SessionTest)  # the operator PUT is audited with the X-CQA-User name
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, version_id=version_id, Session=SessionTest)
    engine.dispose()


def _set_operator(env, cid: str, name: str, actor: str = DDE_ACTOR) -> None:
    r = env.client.put(f"/api/conversations/{cid}/operator", json={"operator_name": name},
                       headers={"X-CQA-Role": "analyst", "X-CQA-User": quote(actor, safe="")})
    assert r.status_code == 200, r.text
    assert r.json()["operator_name"] == name  # stored as typed; only the export neutralises it


def _rows(response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(BOM) and not response.content[len(BOM):].startswith(BOM)  # one BOM, kept
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


# ------------------------------------------------------------------------------------------------ evaluations CSV


def test_evaluations_csv_neutralises_operator_names_set_through_the_api(env):
    _set_operator(env, "d-1", HYPERLINK)
    _set_operator(env, "d-2", SUM)
    _set_operator(env, "+77010000000", "Нұрлан")  # a plain Cyrillic name stays as it is
    for params in ({"checklist_version_id": env.version_id}, {}):
        r = env.client.get("/api/export/evaluations.csv", params=params)
        rows = _rows(r)
        assert rows[0] == EXPECTED_HEADER == header_for(CODES)  # header and column order unchanged
        by_id = {row[0]: row for row in rows[1:]}
        # whole rows: text cells prefixed, every number, flag and date exactly as before (a negative score included)
        assert by_id["d-1"] == ["d-1", "agent chat", "RU", "'" + HYPERLINK, "yes", "-5", "1", "-5", "true", "true", "yes", "-5",
                                "87.5", "90", "false", "fake", "fake-judge", "0.1234", "2026-11-07T12:00:00+00:00"]
        assert by_id["d-2"] == ["d-2", "voice call", "KZ", "'" + SUM, "'=1+1", "7", "0", "7", "true", "true", "'@SUM(2)", "7",
                                "40", "40", "false", "fake", "fake-judge", "0", "2026-11-07T12:00:00+00:00"]
        assert by_id["'+77010000000"] == ["'+77010000000", "voice call", "RU", "Нұрлан", "no", "10", "0", "10", "true", "true",
                                          "no", "10", "100", "100", "false", "'-fake", "'+model", "0.05",
                                          "2026-11-07T12:00:00+00:00"]
        assert list(by_id) == ["'+77010000000", "d-1", "d-2"]  # rows still sorted by the stored id
        # the raw bytes Excel reads: the quoted cell starts with the apostrophe, the payload's quotes are doubled
        assert "\"'=HYPERLINK(\"\"http://x\"\")\"" in r.content.decode("utf-8-sig")
        unprefixed = [cell for row in rows[1:] for cell in row if cell.startswith(FORMULA_START)]
        assert unprefixed == ["-5", "-5", "-5"]  # only d-1's C2 answer, score and final: numbers stay numbers


def test_render_answer_keeps_numbers_and_neutralises_free_text():
    assert [render_answer(a) for a in (-5, "-2.5", " 7 ", "да", "жоқ", True, None)] == ["-5", "-2.5", "7", "yes", "no", "yes", ""]
    assert [render_answer(a) for a in ("=1+1", " @SUM(1) ", DDE_ACTOR, "-итог был")] == [
        "'=1+1", "'@SUM(1)", "'" + DDE_ACTOR, "'-итог был"]  # surrounding spaces are stripped before the check


# ------------------------------------------------------------------------------------------------ one shared neutraliser


def test_one_shared_neutraliser_for_every_csv_writer():
    assert csv_export.safe_cell is csv_safe.safe_cell
    assert registers_csv.safe_cell is csv_safe.safe_cell
    assert audit_router.safe_cell is csv_safe.safe_cell
    assert not hasattr(registers_csv, "_cell") and not hasattr(registers_csv, "_FORMULA_START")  # no private copy left
    assert csv_safe.FORMULA_START == FORMULA_START
    for start in FORMULA_START:
        assert csv_safe.safe_cell(start + "1") == "'" + start + "1"
    for value in ("Алина", "", "agent chat", "2026-11-07T12:00:00+00:00", '{"a": 1}', "1-2", 5, -5, -0.5, 0.0, True):
        assert csv_safe.safe_cell(value) == value and type(csv_safe.safe_cell(value)) is type(value)
    assert csv_safe.safe_cell(None) is None


# ------------------------------------------------------------------------------------------------ audit log CSV


def test_audit_export_neutralises_actor_names_and_ids(env):
    _set_operator(env, "d-1", HYPERLINK)
    _set_operator(env, "+77010000000", "Нұрлан")
    rows = _rows(env.client.get("/api/audit/export.csv", headers={"X-CQA-Role": "admin"}))
    assert rows[0] == list(audit_router.CSV_COLUMNS)
    events = [dict(zip(rows[0], row)) for row in rows[1:]]
    assert [(e["action"], e["entity_id"]) for e in events] == [("conversation.operator.update", "'+77010000000"),
                                                               ("conversation.operator.update", "d-1")]  # newest first
    for e in events:
        assert e["actor_name"] == "'" + DDE_ACTOR and e["actor_role"] == "analyst"
        assert e["id"].isdigit() and e["status_code"] == "200" and e["ts"][:4].isdigit()  # numbers and dates unchanged
    assert json.loads(events[1]["details"])["path"] == "/api/conversations/d-1/operator"  # details stay valid JSON
    assert not [cell for row in rows for cell in row if cell.startswith(FORMULA_START)]


# ------------------------------------------------------------------------------------------------ a cell split off inside the text

SEMICOLON_NAME = "Иван;=1+1;"  # the reviewer's names: ru/kk Excel splits on ";", and a CR or LF starts a row there
CR_NAME = "Иван\r=3+3;"
MIXED_NAME = 'Нұрлан\n@SUM(1);"=4+4;\t-2+3'  # an LF, a quote right after ";" and a tab (pasted rows split on it)
SEMICOLON_ACTOR = "x;=2+2;"  # X-CQA-User keeps ";" (only control characters are dropped)


def _runs_as_formula(cell: str) -> bool:
    # tabs and line breaks in front do not make a cell safe (a reader may drop them): the first other character counts
    return cell.lstrip("\t\r\n").startswith(("=", "+", "-", "@"))


def _cells_split_on(text: str, delimiter: str) -> list[str]:
    """Every cell that a reader splitting on ``delimiter`` sees (";" for ru/kk Excel, a tab for pasted rows). Like Excel,
    Python's reader honours a quote only at the start of a cell and ends a row at a bare CR or LF outside quotes."""
    return [cell for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter) for cell in row]


def _formula_cells(text: str) -> dict[str, list[str]]:
    """Per reader (";", tab, ","): the cells it sees that would run as a formula."""
    return {d: [cell for cell in _cells_split_on(text, d) if _runs_as_formula(cell)] for d in (";", "\t", ",")}


NO_FORMULA = {";": [], "\t": [], ",": []}


def test_safe_cell_neutralises_a_formula_after_a_break_inside_the_text():
    cases = {
        SEMICOLON_NAME: "Иван;'=1+1;",
        CR_NAME: "Иван\r'=3+3;",
        MIXED_NAME: "Нұрлан\n'@SUM(1);'\"=4+4;'\t'-2+3",  # ";" before a tab too: the tab is a formula start
        'x;"=1+1': "x;'\"=1+1",  # split on ";", the doubled quote reads as an empty quoted part, so it hides nothing
        'x;""=1': "x;'\"\"=1",
        ";;=1": ";;'=1",
        "\r\n=1": "'\r\n'=1",
        ";\r=1": ";'\r'=1",  # CR is both a break and a formula start
        "=a;=b": "'=a;'=b",
    }
    for raw, want in cases.items():
        assert csv_safe.safe_cell(raw) == want, raw
        assert csv_safe.safe_cell(want) == want  # a second pass changes nothing
    # a break before a letter, digit, space or closing quote stays as it is: the registers' "; "-joined lists included
    for text in ("Грубость; Ложь", "insult;paid_bypass", "a; -b", "1;2", "x;", 'x;"', '{"a": "x;", "b": 1}', "Алина"):
        assert csv_safe.safe_cell(text) == text
    assert csv_safe.CELL_BREAKS == (";", "\t", "\r", "\n")


def test_no_reader_finds_a_formula_cell_in_any_short_string():
    """Every string of up to 4 risky characters, written by csv.writer first, in the middle and last in a row and again in
    the next row (a quote read at the start of a cell can open a span across rows): ";", tab and "," readers see no
    formula cell. The old first-character rule fails it for thousands of them, 'a;=' among them."""
    alphabet = (";", "\t", "\r", "\n", '"', "=", "-", "a", ",", "'")
    for length in range(5):
        for chars in itertools.product(alphabet, repeat=length):
            value = csv_safe.safe_cell("".join(chars))
            buf = io.StringIO()
            csv.writer(buf).writerows([[value, "x", value, value], [value, "y"]])
            assert _formula_cells(buf.getvalue()) == NO_FORMULA, chars


def test_evaluations_csv_neutralises_cells_that_a_semicolon_or_a_line_break_splits_off(env):
    _set_operator(env, "d-1", SEMICOLON_NAME, actor=SEMICOLON_ACTOR)
    _set_operator(env, "d-2", CR_NAME, actor=SEMICOLON_ACTOR)
    _set_operator(env, "+77010000000", MIXED_NAME, actor=SEMICOLON_ACTOR)
    r = env.client.get("/api/export/evaluations.csv")
    rows = _rows(r)
    assert rows[0] == EXPECTED_HEADER
    by_id = {row[0]: row for row in rows[1:]}
    assert {cid: row[3] for cid, row in by_id.items()} == {
        "'+77010000000": "Нұрлан\n'@SUM(1);'\"=4+4;'\t'-2+3", "d-1": "Иван;'=1+1;", "d-2": "Иван\r'=3+3;"}
    # every number, flag and date as before, the negative score included
    assert by_id["d-1"] == ["d-1", "agent chat", "RU", "Иван;'=1+1;", "yes", "-5", "1", "-5", "true", "true", "yes", "-5",
                            "87.5", "90", "false", "fake", "fake-judge", "0.1234", "2026-11-07T12:00:00+00:00"]
    text = r.content.decode("utf-8-sig")
    semicolon_cells = _cells_split_on(text, ";")
    assert "'=1+1" in semicolon_cells and not {"=1+1", "=3+3"} & set(semicolon_cells)  # split off, but inert
    # only d-1's negative C2 answer, score and final, numbers on purpose, and only where "," splits the row
    assert _formula_cells(text) == {";": [], "\t": [], ",": ["-5", "-5", "-5"]}


def test_audit_export_neutralises_cells_that_a_semicolon_splits_off(env):
    _set_operator(env, "d-1", SEMICOLON_NAME, actor=SEMICOLON_ACTOR)
    _set_operator(env, "+77010000000", MIXED_NAME, actor=SEMICOLON_ACTOR)
    r = env.client.get("/api/audit/export.csv", headers={"X-CQA-Role": "admin"})
    rows = _rows(r)
    events = {row[rows[0].index("entity_id")]: dict(zip(rows[0], row)) for row in rows[1:]}
    assert {e["actor_name"] for e in events.values()} == {"x;'=2+2;"}
    assert events["d-1"]["summary"] == "Диалог d-1: оператор «Иван;'=1+1;»"
    for e in events.values():
        assert json.loads(e["details"])["path"].startswith("/api/conversations/")  # details stay valid JSON
    text = r.content.decode("utf-8-sig")
    assert "'=2+2" in _cells_split_on(text, ";") and _formula_cells(text) == NO_FORMULA


def test_decisions_register_neutralises_cells_that_a_semicolon_or_a_line_break_splits_off(env):
    with env.Session() as db:
        ec = db.scalars(select(EvaluationCriterion).join(Evaluation)
                        .where(Evaluation.conversation_id == "d-1", EvaluationCriterion.code == "C1")).one()
        appeal = Appeal(evaluation_criterion_id=ec.id, comment="Не согласен;=1+1;", author_role="operator",
                        author_name='Аноним;"@SUM(1);', status="decided")
        db.add(appeal)
        db.flush()
        db.add(Decision(appeal_id=appeal.id, action="confirm", reason="Итог был\r-2+3;", reviewer_label="Проверяющий\t=1+1",
                        reviewer_role="reviewer"))
        db.commit()
    r = env.client.get("/api/export/decisions.csv")
    rows = _rows(r)
    row = dict(zip(rows[0], rows[1]))
    assert (row["comment"], row["author"], row["reason"], row["reviewer"]) == (
        "Не согласен;'=1+1;", "Аноним;'\"@SUM(1);", "Итог был\r'-2+3;", "Проверяющий\t'=1+1")
    assert row["appeal_id"].isdigit() and row["total_score"] == "87.50"  # numbers stay numbers
    assert _formula_cells(r.content.decode("utf-8-sig")) == NO_FORMULA
