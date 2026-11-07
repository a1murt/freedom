"""Round 3, PDF card: judge texts carry the same turn numbers as the API and the UI. ``display_turn_refs`` shifts the
judge's 0-based turn labels to 1-based and, given the dialog's last turn index, leaves a number beyond the dialog as
written («сообщение системы 404»). The PDF shifted without that bound: «сообщение системы 405» in the PDF, «404» on
the card. Two more outputs disagreed with the card: the calibration disagreements (GET /api/calibration/agreement)
returned the stored texts unshifted («реплике 3»), and the checklist trial (POST /api/checklists/test-criterion)
shifted its verdict's explanation but not the claim notes, step lines and ensemble votes of the same answer.

Offline: in-memory SQLite (api/data/cqa.db is never opened), no API key; the trial's judge is a local stub.
"""
from __future__ import annotations

import asyncio
import base64
import re
import zlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.calibration.service import agreement
from cqa.checklist import service as checklist_svc
from cqa.config import settings
from cqa.db import Base
from cqa.export.pdf_export import evaluation_pdf
from cqa.judge import runner as judge_runner
from cqa.judge.contract import ProviderResult, Usage
from cqa.models import (
    Checklist,
    ChecklistVersion,
    Conversation,
    Criterion,
    Evaluation,
    EvaluationCriterion,
    GoldLabel,
    Turn,
)
from cqa.serializers import evaluation_dict

N_TURNS = 12  # judge labels 0..11: «404» and «500» cannot be turns of this dialog
EXPLANATION = "Клиент получил сообщение системы 404 в реплике 3, оператор не объяснил ошибку."
NA_REASON = "Сообщение системы 500 в реплике 0, клиент ушёл."


# ------------------------------------------------------------------------------------------------ PDF text

# tokens of a page content stream that carry text
_CONTENT = re.compile(
    rb"/(?P<font>[\w+]+)\s+[\d.]+\s+Tf"  # font subset selection: «/F3+0 8 Tf»
    rb"|\((?P<string>(?:[^()\\]|\\.)*)\)"  # literal string (escapes decoded by _unescape)
    rb"|(?P<move>T\*|T[dDm]|ET)(?![\w*])",  # operators that start a new line
    re.DOTALL,
)
_ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}


def _unescape(s: bytes) -> bytes:
    """A PDF literal string's escapes: \\ddd (octal), \\n, \\(, \\\\ …"""
    return re.sub(rb"\\([0-7]{1,3}|.)", lambda m: bytes([int(m.group(1), 8) & 0xFF]) if m.group(1)[0] in b"01234567"
                  else _ESCAPES.get(m.group(1)[0], m.group(1)), s, flags=re.DOTALL)


def _pdf_text(pdf: bytes) -> str:
    """The text drawn on the pages, whitespace collapsed (a line wrapped inside a table cell reads as one).

    reportlab subsets the TrueType fonts, so Cyrillic is not stored as UTF-8 (a byte search finds only ASCII): each
    string of a page's content stream is decoded through the ToUnicode map of the font subset selected before it."""
    objs = {int(n): body for n, body in re.findall(rb"(\d+) 0 obj(.*?)endobj", pdf, re.DOTALL)}

    def stream(n: int) -> bytes:
        body = objs[n]
        start = body.index(b"stream") + len(b"stream")
        start += 2 if body[start:start + 2] == b"\r\n" else 1
        data = body[start:start + int(re.search(rb"/Length (\d+)", body).group(1))]
        if b"/ASCII85Decode" in body:
            data = base64.a85decode(data.strip().removesuffix(b"~>"))
        return zlib.decompress(data) if b"/FlateDecode" in body else data

    cmaps: dict[bytes, dict[int, str]] = {}
    for body in objs.values():
        name, to_unicode = re.search(rb"/Name /(\S+)", body), re.search(rb"/ToUnicode (\d+) 0 R", body)
        if name and to_unicode:
            pairs = re.findall(rb"<([0-9A-F]{2})> <([0-9A-F]{4,6})>", stream(int(to_unicode.group(1))))
            cmaps[name.group(1)] = {int(code, 16): chr(int(uni, 16)) for code, uni in pairs}
    out: list[str] = []
    for n in re.findall(rb"/Contents (\d+) 0 R", pdf):
        cmap = None
        for m in _CONTENT.finditer(stream(int(n))):
            if m.lastgroup == "font":
                cmap = cmaps.get(m.group("font"))
            elif m.lastgroup == "string":
                raw = _unescape(m.group("string"))
                if cmap is None:  # a standard font (Helvetica): WinAnsi bytes
                    out.append(raw.decode("latin-1"))
                else:
                    out.append("".join(cmap.get(b, "\N{REPLACEMENT CHARACTER}") for b in raw))
            else:
                out.append(" ")
    return " ".join("".join(out).split())


# ------------------------------------------------------------------------------------------------ fixture


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def _seed(db: Session, turn_count: int | None) -> int:
    """A 12-turn chat (turn_count as given: the stored count can be missing) with one verdict citing turns and one N/A
    verdict; returns the evaluation id."""
    cl = Checklist(name="Freedom QA")
    db.add(cl)
    db.flush()
    v = ChecklistVersion(checklist_id=cl.id, version_no=1, is_active=True, scoring_rules={}, sections=[])
    db.add(v)
    db.add(Conversation(id="pdf-ru-001", channel="agent chat", language="RU", operator_name="Алина", turn_count=turn_count))
    db.flush()
    for i in range(N_TURNS):
        speaker = "operator" if i % 2 else "client"
        db.add(Turn(conversation_id="pdf-ru-001", idx=i, timestamp_raw=f"00:{i:02d}", speaker=speaker, text=f"Текст {i}",
                    segment="operator" if speaker == "operator" else "pre"))
    crits = [Criterion(checklist_version_id=v.id, code=code, order=order, section="S1", title=title, instruction="…",
                       answer_type="binary", options=[{"value": "yes", "points": 1}, {"value": "no", "points": 0}], max_points=1,
                       weight=5, na_allowed=True, is_critical=False)
             for order, (code, title) in enumerate([("C1", "Установление контакта"), ("C3", "Идентификация клиента")], start=1)]
    db.add_all(crits)
    ev = Evaluation(conversation_id="pdf-ru-001", checklist_version_id=v.id, status="completed", provider="fake",
                    total_score=100, final_total_score=100)
    db.add(ev)
    db.flush()
    db.add_all([
        EvaluationCriterion(evaluation_id=ev.id, criterion_id=crits[0].id, code="C1", applicable=True, answer="yes", score=1,
                            max_points=1, weight=5, weight_applied=5, earned=5, explanation=EXPLANATION,
                            evidence=[{"turn_idx": 3, "speaker": "client", "quote": "Текст 3", "role": "supports"}]),
        EvaluationCriterion(evaluation_id=ev.id, criterion_id=crits[1].id, code="C3", applicable=False, na_reason=NA_REASON,
                            max_points=1, weight=5, weight_applied=0, earned=0),
    ])
    db.commit()
    return ev.id


# ------------------------------------------------------------------------------------------------ turn references


def test_numbers_beyond_the_dialog_stay_and_turn_labels_are_shifted(db):
    text = _pdf_text(evaluation_pdf(db, _seed(db, N_TURNS)))
    assert "сообщение системы 404 в реплике 4," in text  # the explanation: label 3 is turn 4, «404» is not a turn
    assert "N/A Сообщение системы 500 в реплике 1," in text  # the N/A reason, the same way
    assert "системы 405" not in text and "системы 501" not in text
    assert "реплике 3," not in text and "реплике 0," not in text
    assert "#4 Клиент: «Текст 3»" in text  # evidence turn numbers were already 1-based


@pytest.mark.parametrize("turn_count", [N_TURNS, 0, None])
def test_pdf_judge_texts_match_the_api(db, turn_count):
    """The PDF prints what the card shows, also when the dialog's turn count is missing (0/None: no bound, as in the API)."""
    ev_id = _seed(db, turn_count)
    text = _pdf_text(evaluation_pdf(db, ev_id))
    rows = {r["code"]: r for r in evaluation_dict(db, db.get(Evaluation, ev_id))["criteria"]}
    assert " ".join(rows["C1"]["explanation"].split()) in text
    assert " ".join(rows["C3"]["na_reason"].split()) in text


# ------------------------------------------------------------------------------------------------ calibration


@pytest.mark.parametrize("turn_count", [N_TURNS, 0, None])
def test_calibration_disagreements_quote_the_judge_like_the_card(db, turn_count):
    """The AI-vs-human disagreements carry the card's texts (they returned the stored labels: «реплике 3», «реплике 0»)."""
    ev = db.get(Evaluation, _seed(db, turn_count))
    db.add_all([GoldLabel(conversation_id="pdf-ru-001", criterion_code="C1", value="no", labeler="team"),  # the AI said yes
                GoldLabel(conversation_id="pdf-ru-001", criterion_code="C3", value="yes", labeler="team")])  # the AI said N/A
    db.commit()
    card = {r["code"]: r for r in evaluation_dict(db, ev)["criteria"]}
    dis = {d["code"]: d for d in agreement(db, checklist_version_id=ev.checklist_version_id)["disagreements"]}
    assert set(dis) == {"C1", "C3"}
    assert (dis["C1"]["explanation"], dis["C1"]["na_reason"]) == (card["C1"]["explanation"], None)
    assert (dis["C3"]["na_reason"], dis["C3"]["explanation"]) == (card["C3"]["na_reason"], None)
    if turn_count:  # bounded by the dialog: «404» and «500» stay, the labels are shifted
        assert dis["C1"]["explanation"] == "Клиент получил сообщение системы 404 в реплике 4, оператор не объяснил ошибку."
        assert dis["C3"]["na_reason"] == "Сообщение системы 500 в реплике 1, клиент ушёл."


# ------------------------------------------------------------------------------------------------ checklist trial

TRIAL_CRITERION = {"code": "C9", "section": "S3", "title": "Персонализация", "answer_type": "binary", "weight": 10,
                   "na_allowed": False, "is_critical": False, "instruction": "Да — оператор обратился к клиенту по имени.",
                   "judge_spec": {"definition": "Обращение по имени", "decision_steps": ["Найдите обращение по имени."]}}


class _TrialJudge:
    """An offline judge member: every text it writes cites the judge's label 3, next to «сообщение системы 404»."""

    name, model = "stub", "stub-model"

    async def complete(self, system, messages, schema, *, effort="high", max_tokens=4096) -> ProviderResult:
        verdict = {"criterion_code": "C9", "applicable": True, "na_reason": "", "answer": "no", "confidence": 0.9,
                   "evidence": [{"turn_idx": 3, "speaker": "operator", "quote": "Текст 3", "role": "supports"}],
                   "explanation": EXPLANATION, "step_results": ["Шаг 1: в реплике [3] обращения по имени нет."], "flags": [],
                   "claims": [{"turn_idx": 3, "claim": "Ошибка 404 пройдёт сама", "status": "unverified", "note": "см. реплику 3"}]}
        return ProviderResult(parsed=verdict, usage=Usage(input_tokens=10, output_tokens=5), raw={}, model=self.model,
                              provider=self.name, latency_ms=1)

    def cost(self, usage: Usage) -> float:
        return 0.0


@pytest.fixture
def trial_judge(monkeypatch):
    """No network: no API key, no local endpoint, and every judge member (fake, the ensemble's three) is the stub."""
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "local_endpoint", None)
    monkeypatch.setattr(judge_runner, "get_provider", lambda name, model=None, **kw: _TrialJudge())


@pytest.mark.parametrize("provider", ["fake", "ensemble"])
def test_trial_answer_has_one_turn_numbering(db, trial_judge, provider):
    """One trial answer, one numbering: the claim notes, step lines and member votes were left with the judge's labels
    («см. реплику 3») while the explanation beside them read «реплике 4»."""
    _seed(db, N_TURNS)
    out = asyncio.run(checklist_svc.run_criterion_test(db, criterion=dict(TRIAL_CRITERION), conversation_id="pdf-ru-001",
                                                       provider=provider))
    v = out["verdict"]
    assert v["explanation"] == "Клиент получил сообщение системы 404 в реплике 4, оператор не объяснил ошибку."
    assert v["step_results"] == ["Шаг 1: в реплике [4] обращения по имени нет."]
    # the note as the card shows it (claims_out); turn_idx stays the 0-based index, the claim is the operator's words
    assert v["claims"] == [{"turn_idx": 3, "claim": "Ошибка 404 пройдёт сама", "status": "unverified", "note": "см. реплику 4"}]
    assert v["evidence"][0]["turn_idx"] == 3
    if provider == "fake":
        assert out["votes"] is None  # a single judge has no votes
    else:
        assert len(out["votes"]) == 3 and {x["explanation"] for x in out["votes"]} == {v["explanation"]}
