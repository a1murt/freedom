"""Checklist governance (docs/arch/checklists.md): validation, weights and export ids in the version JSON, portable
export / import, drafts (activate=false), judge_spec reuse, the single-criterion trial, and the partial
re-evaluation flows of the control check ("add a criterion", "replace a criterion", "change a weight").

Offline by construction: the Anthropic key is cleared, the rubric compiler and the judge provider are stubbed, and every
database is an in-memory SQLite (api/data/cqa.db is never opened).
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cqa.checklist import compiler
from cqa.checklist import service as svc
from cqa.checklist.validation import (ORGANIZER_EXPORT_IDS, ChecklistValidationError, export_ids_for, prepare_checklist_payload,
                                      prepare_criterion_payload, validate_checklist_payload)
from cqa.config import settings
from cqa.db import Base, get_db
from cqa.judge import runner as judge_runner
from cqa.judge.contract import ProviderResult, Usage
from cqa.judge.providers import ProviderError
from cqa.models import Checklist, ChecklistVersion, Conversation, Evaluation, EvaluationCriterion, Job, LlmTrace, Turn
from cqa.routers.checklists import router as checklists_router

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
SEED = json.loads((SEEDS / "checklist_freedom_v1.json").read_text(encoding="utf-8"))
SPECS = json.loads((SEEDS / "judge_specs_v1.json").read_text(encoding="utf-8"))
CODES = [c["code"] for c in SEED["criteria"]]
CID = "t-gov-001"
OPERATOR_GREETING = "Здравствуйте! Меня зовут Алина. Подскажите, какую ошибку видите?"
TURNS = [
    ("client", "00:00", "Здравствуйте, не могу войти в приложение"),
    ("bot", "00:05", "Переключаю на оператора."),
    ("operator", "00:42", OPERATOR_GREETING),
    ("client", "01:10", "Пишет «неверный пароль»"),
    ("operator", "01:40", "Нажмите «Забыли пароль», код придёт по SMS."),
    ("operator", "02:30", "Остались ли ещё вопросы? Хорошего дня!"),
    ("system", "02:45", "Чат закрыт"),
]
# stub verdicts: everything at the top except the two criteria the control check adds / replaces
ANSWERS = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": "20", "C5": "yes", "C6": "10", "C7": "yes", "C8": "no", "C9": "no", "C6b": "no"}
C9 = {"code": "C9", "section": "S3", "title": "Персонализация", "answer_type": "binary", "weight": 10, "na_allowed": False, "is_critical": False,
      "instruction": "Да — оператор хотя бы раз обратился к клиенту по имени после приветствия."}
C6B = {"code": "C6b", "section": "S3", "title": "Грамотность и стиль", "answer_type": "binary", "weight": 15, "na_allowed": False,
       "instruction": "Да — в ответах оператора нет орфографических ошибок, канцелярита и неуместных выражений."}
COPIED = "copied_from_previous_version"
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


# ============================================================================================ fixtures and stubs


@pytest.fixture(autouse=True)
def compiled_codes(monkeypatch) -> list[str]:
    """No network anywhere: no API key, a deterministic compiler that records what it compiled, and a provider
    factory that refuses unless a test installs the stub judge."""
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "default_provider", "anthropic")
    monkeypatch.setattr(settings, "default_model", "claude-opus-5-5")
    monkeypatch.setattr(settings, "local_endpoint", None)
    calls: list[str] = []

    async def fake_compile(crit: dict[str, Any]) -> dict[str, Any]:
        calls.append(crit.get("code"))
        return compiler.fallback_spec(crit) | {"_compiled_by": "test-compiler"}

    def no_provider(name: str, model: str | None = None):
        raise ProviderError(f"provider '{name}' is disabled in tests")

    monkeypatch.setattr(compiler, "compile_spec_async", fake_compile)
    monkeypatch.setattr(judge_runner, "get_provider", no_provider)
    return calls


@pytest.fixture()
def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    engine.dispose()


@pytest.fixture()
def db(make_session):
    s = make_session()
    yield s
    s.close()


@pytest.fixture()
def runner_db(make_session, monkeypatch):
    """cqa.judge.runner.evaluate_conversation opens its own sessions: point it at the in-memory database."""
    monkeypatch.setattr(judge_runner, "SessionLocal", make_session)
    return make_session


@pytest.fixture()
def client(make_session):
    app = FastAPI()
    app.include_router(checklists_router)

    def _override():
        s = make_session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


_HEADER = re.compile(r"#\s*Criterion\s+([A-Za-z][A-Za-z0-9_-]{0,15})")


def _criterion_code(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        for block in m.get("content") or []:
            found = _HEADER.search(block.get("text", "")) if isinstance(block, dict) else None
            if found:
                return found.group(1)
    raise AssertionError("criterion header not found in the judge prompt")


class StubJudge:
    """Offline judge provider: answers from a table keyed by criterion code and cites operator turn 2 verbatim."""

    name = "stub"

    def __init__(self, model: str | None, hub: "JudgeHub"):
        self.model = model or "stub-model"
        self.hub = hub

    async def complete(self, system, messages, schema, *, effort="high", max_tokens=4096) -> ProviderResult:
        code = _criterion_code(messages)
        self.hub.calls.append(code)
        self.hub.transcripts.append(messages[0]["content"][0]["text"])
        if code in self.hub.fail:
            raise ProviderError(f"stub refused {code}")
        answer = self.hub.answers.get(code, "yes")
        applicable = answer != "N/A"
        verdict = {"criterion_code": code, "applicable": applicable, "na_reason": "" if applicable else "Условие N/A выполнено.",
                   "answer": answer if applicable else "", "confidence": 0.9,
                   "evidence": [{"turn_idx": 2, "speaker": "operator", "quote": OPERATOR_GREETING, "role": "supports"}],
                   "explanation": f"{code}: ответ {answer}.", "step_results": [], "flags": [], "claims": []}
        return ProviderResult(parsed=verdict, usage=Usage(input_tokens=1000, output_tokens=100), raw={"stub": True}, model=self.model,
                              provider=self.name, latency_ms=3)

    def cost(self, usage: Usage) -> float:
        return 0.01


class JudgeHub:
    def __init__(self):
        self.answers = dict(ANSWERS)
        self.calls: list[str] = []
        self.transcripts: list[str] = []
        self.fail: set[str] = set()
        self.requested: list[tuple[str, str | None]] = []

    def provider(self, name: str, model: str | None = None) -> StubJudge:
        self.requested.append((name, model))
        return StubJudge(model, self)


@pytest.fixture()
def judge(monkeypatch) -> JudgeHub:
    hub = JudgeHub()
    monkeypatch.setattr(judge_runner, "get_provider", hub.provider)
    return hub


# ============================================================================================ data helpers


def seed_payload() -> dict[str, Any]:
    p = copy.deepcopy(SEED)
    for c in p["criteria"]:
        c["judge_spec"] = copy.deepcopy(SPECS[c["code"]])
    return p


def studio_criteria(version: ChecklistVersion) -> list[dict[str, Any]]:
    """What Checklist Studio sends when saving: the stored criteria without ids and without judge specs."""
    crits = svc.export_version(version)["criteria"]
    for c in crits:
        c.pop("judge_spec", None)
    return crits


def add_conversation(db: Session, cid: str = CID, channel: str = "agent chat", turns: list[tuple[str, str, str]] = TURNS) -> None:
    db.add(Conversation(id=cid, channel=channel, language="RU", operator_name="Алина", turn_count=len(turns), first_response_seconds=37.0,
                        meta={"operator_start_idx": 2}))
    for idx, (speaker, ts, text) in enumerate(turns):
        db.add(Turn(conversation_id=cid, idx=idx, timestamp_raw=ts, offset_seconds=float(idx * 20), speaker=speaker, text=text,
                    segment="operator" if idx >= 2 else "pre"))
    db.commit()


def make_v1(db: Session) -> ChecklistVersion:
    return svc.create_checklist(db, seed_payload(), change_note="v1", compile_missing=False)


def evaluate(version: ChecklistVersion, *, base: int | None = None, only: list[str] | set[str] | None = None) -> int:
    kwargs: dict[str, Any] = {}
    if base is not None:
        kwargs = {"mode": "partial", "base_evaluation_id": base, "only_codes": set(only or [])}
    return asyncio.run(judge_runner.evaluate_conversation(CID, version.id, provider_name="anthropic", **kwargs))


def load(make_session, evaluation_id: int) -> tuple[Evaluation, dict[str, EvaluationCriterion]]:
    s = make_session()
    try:
        ev = s.get(Evaluation, evaluation_id)
        return ev, {r.code: r for r in ev.criteria}
    finally:
        s.close()


def fields_of(errors: list[dict[str, str]]) -> set[str]:
    return {e["field"] for e in errors}


def require(condition: bool, message: str) -> None:
    """Set-up checks inside xfail tests: a broken set-up must fail loudly, not count as the expected failure."""
    if not condition:
        pytest.fail(f"set-up: {message}")


# ============================================================================================ validation: seed and normalisation


def test_organizer_seed_is_valid_canonical_and_idempotent():
    prepared = prepare_checklist_payload(seed_payload())
    assert prepared.errors == [] and prepared.warnings == []
    assert validate_checklist_payload(seed_payload()) == []
    again = prepare_checklist_payload(prepared.payload)
    assert again.payload == prepared.payload and again.errors == [] and again.warnings == []
    # normalising stored data must not change judge-relevant signatures (or every re-save would recompile specs)
    for raw, norm in zip(SEED["criteria"], prepared.payload["criteria"]):
        stored = dict(raw, critical_value=raw.get("critical_value") or ("yes" if raw["is_critical"] else None))
        assert compiler.judge_relevant_signature(stored) == compiler.judge_relevant_signature(norm), raw["code"]
    c8 = prepared.payload["criteria"][7]
    assert c8["critical_value"] == "yes" and c8["max_points"] == 0 and c8["weight"] == 0


def test_binary_options_are_normalised():
    p = seed_payload()
    p["criteria"][0]["options"] = [{"value": "Нет"}, {"value": "Да"}]
    p["criteria"][1]["options"] = None
    p["criteria"][7]["options"] = []  # gross error without options → 0/0 points like the organizer C8
    p["criteria"][7].pop("critical_value", None)
    out = prepare_checklist_payload(p)
    assert out.errors == []
    c1, c2, c8 = out.payload["criteria"][0], out.payload["criteria"][1], out.payload["criteria"][7]
    assert c1["options"] == [{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}]
    assert c2["options"] == c1["options"] and c1["max_points"] == 1
    assert c8["options"] == [{"value": "yes", "label": "Да", "points": 0}, {"value": "no", "label": "Нет", "points": 0}]
    assert c8["critical_value"] == "yes" and c8["max_points"] == 0


def test_scale_options_are_normalised():
    p = seed_payload()
    p["criteria"][3]["options"] = [{"value": "0"}, {"value": "10", "anchor": "Всё закрыто"}, {"value": 5, "anchor": ""}]
    p["criteria"][3]["max_points"] = 99
    out = prepare_checklist_payload(p)
    assert out.errors == []
    c4 = out.payload["criteria"][3]
    assert c4["options"] == [{"value": 10, "label": "10", "points": 10, "anchor": "Всё закрыто"},
                             {"value": 5, "label": "5", "points": 5}, {"value": 0, "label": "0", "points": 0}]
    assert c4["max_points"] == 10
    assert "criteria[3].max_points" in fields_of(out.warnings)


def test_missing_section_is_created_with_a_warning_and_overrides_keys_are_normalised():
    p = seed_payload()
    p["criteria"].append(dict(C9, section="S9", channel_overrides={"Voice_Call": {"instruction": "Для звонка — по имени после приветствия."}}))
    out = prepare_checklist_payload(p)
    assert out.errors == []
    assert {"code": "S9", "title": "S9"} in out.payload["sections"]
    assert "criteria[8].section" in fields_of(out.warnings)
    assert out.payload["criteria"][8]["channel_overrides"] == {"voice call": {"instruction": "Для звонка — по имени после приветствия."}}
    assert "criteria" in fields_of(out.warnings)  # weights sum to 110, not 100: informational only


def test_inherited_sections_are_used_for_new_versions():
    payload = {"criteria": [dict(C9, section="S3"), dict(C6B, section="S1")]}
    sections = [{"code": "S1", "title": "Начало"}, {"code": "S3", "title": "Завершение"}]
    out = prepare_checklist_payload(payload, inherited_sections=sections)
    assert out.errors == [] and "sections" not in out.payload
    assert all(w["field"] != "criteria[0].section" for w in out.warnings)


def test_warnings_for_empty_instruction_and_unknown_export_id_mapping():
    p = seed_payload()
    p["criteria"][0]["instruction"] = ""
    p["scoring_rules"]["export_ids"] = {"C42": "something"}
    out = prepare_checklist_payload(p)
    assert out.errors == []
    assert {"criteria[0].instruction", "scoring_rules.export_ids.C42"} <= fields_of(out.warnings)


def _mutate(path: str, value: Any):
    def apply(p: dict[str, Any]) -> None:
        target: Any = p
        keys = [int(k) if k.isdigit() else k for k in re.findall(r"[^.\[\]]+", path)]
        for k in keys[:-1]:
            target = target[k]
        target[keys[-1]] = value
    return apply


def _set_options(i: int, options: list[Any]):
    return _mutate(f"criteria.{i}.options", options)


def _two_c1(p: dict[str, Any]) -> None:
    p["criteria"][1]["code"] = "C1"


def _c1_lower(p: dict[str, Any]) -> None:
    p["criteria"][1]["code"] = "c1"


def _all_zero_weights(p: dict[str, Any]) -> None:
    for c in p["criteria"]:
        c["weight"] = 0


def _critical_scale(p: dict[str, Any]) -> None:
    p["criteria"][7]["answer_type"] = "scale"
    p["criteria"][7]["options"] = [{"value": 1, "points": 0}, {"value": 0, "points": 0}]


def _export_id_clash(p: dict[str, Any]) -> None:
    p["criteria"].append(copy.deepcopy(C9))
    p["scoring_rules"]["export_ids"] = {"C9": "contact"}


def _duplicate_section(p: dict[str, Any]) -> None:
    p["sections"].append({"code": "S1", "title": "Ещё раз"})


def _override_na_without_condition(p: dict[str, Any]) -> None:
    p["criteria"][4]["channel_overrides"] = {"voice call": {"na_allowed": True}}


INVALID_CASES = [
    ("empty criteria", _mutate("criteria", []), "criteria"),
    ("criteria not a list", _mutate("criteria", "C1,C2"), "criteria"),
    ("criterion not an object", _mutate("criteria.0", "C1"), "criteria[0]"),
    ("missing code", _mutate("criteria.0.code", None), "criteria[0].code"),
    ("code starts with a digit", _mutate("criteria.0.code", "9C"), "criteria[0].code"),
    ("code with a space", _mutate("criteria.0.code", "C 1"), "criteria[0].code"),
    ("cyrillic code", _mutate("criteria.0.code", "К1"), "criteria[0].code"),
    ("code longer than 16", _mutate("criteria.0.code", "C" + "1" * 16), "criteria[0].code"),
    ("duplicate code", _two_c1, "criteria[1].code"),
    ("duplicate code by case", _c1_lower, "criteria[1].code"),
    ("empty title", _mutate("criteria.0.title", "   "), "criteria[0].title"),
    ("missing title", _mutate("criteria.0.title", None), "criteria[0].title"),
    ("title too long", _mutate("criteria.0.title", "Я" * 201), "criteria[0].title"),
    ("unknown answer type", _mutate("criteria.0.answer_type", "multiple"), "criteria[0].answer_type"),
    ("binary value other than yes/no", _set_options(0, [{"value": "yes"}, {"value": "maybe"}]), "criteria[0].options[1].value"),
    ("binary duplicate value", _set_options(0, [{"value": "yes"}, {"value": "no"}, {"value": "да"}]), "criteria[0].options[2].value"),
    ("binary with one option", _set_options(0, [{"value": "yes"}]), "criteria[0].options"),
    ("binary negative points", _set_options(0, [{"value": "yes", "points": -1}, {"value": "no"}]), "criteria[0].options[0].points"),
    ("binary no above yes", _set_options(0, [{"value": "yes", "points": 0}, {"value": "no", "points": 1}]), "criteria[0].options"),
    ("scale with one step", _set_options(3, [{"value": 20}]), "criteria[3].options"),
    ("scale non-numeric value", _set_options(3, [{"value": "много"}, {"value": 0}]), "criteria[3].options[0].value"),
    ("scale duplicate value", _set_options(3, [{"value": 10}, {"value": "10"}]), "criteria[3].options[1].value"),
    ("scale negative points", _set_options(3, [{"value": 20, "points": -5}, {"value": 0}]), "criteria[3].options[0].points"),
    ("scale non-text anchor", _set_options(3, [{"value": 20, "anchor": 7}, {"value": 0}]), "criteria[3].options[0].anchor"),
    ("scale without points above zero", _set_options(3, [{"value": 1, "points": 0}, {"value": 0, "points": 0}]), "criteria[3].options"),
    ("weight not a number", _mutate("criteria.0.weight", "десять"), "criteria[0].weight"),
    ("weight negative", _mutate("criteria.0.weight", -5), "criteria[0].weight"),
    ("weight boolean", _mutate("criteria.0.weight", True), "criteria[0].weight"),
    ("gross error with weight", _mutate("criteria.7.weight", 10), "criteria[7].weight"),
    ("gross error on a scale", _critical_scale, "criteria[7].answer_type"),
    ("gross error value", _mutate("criteria.7.critical_value", "maybe"), "criteria[7].critical_value"),
    ("no positive weight", _all_zero_weights, "criteria"),
    ("N/A without condition", _mutate("criteria.1.na_condition", None), "criteria[1].na_condition"),
    ("N/A with blank condition", _mutate("criteria.1.na_condition", "  "), "criteria[1].na_condition"),
    ("na_allowed not boolean", _mutate("criteria.0.na_allowed", "иногда"), "criteria[0].na_allowed"),
    ("unknown channel", _mutate("criteria.0.channel_overrides", {"email": {"instruction": "…"}}), 'criteria[0].channel_overrides["email"]'),
    ("unknown override field", _mutate("criteria.0.channel_overrides", {"voice call": {"weight": 5}}),
     'criteria[0].channel_overrides["voice call"].weight'),
    ("override N/A without any condition", _override_na_without_condition, 'criteria[4].channel_overrides["voice call"].na_condition'),
    ("overrides not an object", _mutate("criteria.0.channel_overrides", "voice call"), "criteria[0].channel_overrides"),
    ("duplicate section", _duplicate_section, "sections[4].code"),
    ("section code too long", _mutate("criteria.0.section", "S" * 17), "criteria[0].section"),
    ("judge_spec of a wrong shape", _mutate("criteria.0.judge_spec", {"definition": 1}), "criteria[0].judge_spec"),
    ("order not an integer", _mutate("criteria.0.order", "первый"), "criteria[0].order"),
    ("total scale zero", _mutate("scoring_rules.total_scale", 0), "scoring_rules.total_scale"),
    ("rounding decimals", _mutate("scoring_rules.rounding.decimals", 9), "scoring_rules.rounding.decimals"),
    ("critical mode", _mutate("scoring_rules.critical.mode", "zero_total"), "scoring_rules.critical.mode"),
    ("export id clash", _export_id_clash, "criteria[8].code"),
    ("name not text", _mutate("name", 42), "name"),
]


@pytest.mark.parametrize("label,mutation,field", INVALID_CASES, ids=[c[0] for c in INVALID_CASES])
def test_validation_rules(label, mutation, field):
    payload = seed_payload()
    mutation(payload)
    errors = validate_checklist_payload(payload)
    assert field in fields_of(errors), f"{label}: {errors}"
    for e in errors:
        assert set(e) == {"field", "message"} and CYRILLIC.search(e["message"]), e


def test_zero_weights_are_reported_even_next_to_other_errors():
    p = seed_payload()
    _all_zero_weights(p)
    p["criteria"][0]["channel_overrides"] = {"voice call": {"weight": 5}}  # an override field named "weight" is not a weight error
    assert {"criteria", 'criteria[0].channel_overrides["voice call"].weight'} <= fields_of(validate_checklist_payload(p))


def test_validation_error_carries_all_problems_in_one_answer():
    p = seed_payload()
    p["criteria"][0]["title"] = ""
    p["criteria"][2]["weight"] = -1
    p["criteria"][1]["na_condition"] = None
    with pytest.raises(ChecklistValidationError) as exc:
        svc.create_checklist(None, p)  # validation runs before any database access
    err = exc.value.to_error()
    assert err["code"] == "invalid_checklist"
    assert {"criteria[0].title", "criteria[2].weight", "criteria[1].na_condition"} <= fields_of(err["errors"])
    assert err["message"].startswith("Чек-лист не прошёл проверку: 3 ошибки.")


def test_single_criterion_rules_do_not_need_a_whole_checklist():
    crit, errors, warnings = prepare_criterion_payload(dict(C9, weight=0))
    assert errors == [] and crit["options"][0]["value"] == "yes" and crit["max_points"] == 1
    _, errors, _ = prepare_criterion_payload({"code": "C9", "title": "", "is_critical": True, "weight": 5})
    assert {"criterion.title", "criterion.weight"} <= fields_of(errors)


# ============================================================================================ weights and export ids


def test_version_json_has_weights_and_export_ids(db):
    v1 = make_v1(db)
    j = svc.serialize_version(v1)
    assert j["weights"]["total"] == 100.0
    assert j["weights"]["by_code"] == {"C1": 10.0, "C2": 15.0, "C3": 20.0, "C4": 20.0, "C5": 15.0, "C6": 15.0, "C7": 5.0, "C8": 0.0}
    assert j["weights"]["normalized"] == j["weights"]["by_code"]
    assert j["export_ids"] == ORGANIZER_EXPORT_IDS

    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="+C9")
    w = svc.serialize_version(v2)["weights"]
    assert w["total"] == 110.0 and w["by_code"]["C9"] == 10.0
    assert w["normalized"] == {"C1": 9.09, "C2": 13.64, "C3": 18.18, "C4": 18.18, "C5": 13.64, "C6": 13.64, "C7": 4.55, "C8": 0.0, "C9": 9.09}
    assert svc.serialize_version(v2)["export_ids"]["C9"] == "c9"


def test_export_ids_defaults_overrides_and_parity_with_results_json():
    from cqa.export.results_json import DEFAULT_EXPORT_IDS

    assert ORGANIZER_EXPORT_IDS == DEFAULT_EXPORT_IDS
    ids = export_ids_for(["C1", "C6b", "Tone_2"], {"export_ids": {"C6b": "dialogue"}})
    assert ids == {"C1": "contact", "C6b": "dialogue", "Tone_2": "tone_2"}


def test_replaced_criterion_can_take_the_organizer_export_id(db):
    v1 = make_v1(db)
    crits = [C6B if c["code"] == "C6" else c for c in studio_criteria(v1)]
    rules = dict(v1.scoring_rules, export_ids={"C6b": "dialogue"})
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": crits, "scoring_rules": rules}, change_note="C6 → C6b")
    assert svc.serialize_version(v2)["export_ids"]["C6b"] == "dialogue"
    with pytest.raises(ChecklistValidationError) as exc:  # with C6 still present the id would be taken twice
        svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C6B], "scoring_rules": rules}, change_note="clash")
    assert "criteria[8].code" in fields_of(exc.value.errors)


# ============================================================================================ judge_spec reuse and compilation


def test_unchanged_criteria_keep_their_judge_spec(db, compiled_codes):
    v1 = make_v1(db)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1)}, change_note="same")
    assert compiled_codes == []
    assert {c.code: c.judge_spec for c in v2.criteria} == {c.code: c.judge_spec for c in v1.criteria}
    d = svc.diff_versions(v1, v2)
    assert d["added"] == d["removed"] == d["changed"] == d["rejudge_codes"] == []


def test_changed_and_new_criteria_get_new_specs_and_reverts_reuse_older_ones(db, compiled_codes):
    v1 = make_v1(db)
    original = studio_criteria(v1)
    edited = copy.deepcopy(original)
    edited[0]["instruction"] += " Приветствие — в первой же реплике."
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": edited + [C9, dict(C6B, code="C10")]}, change_note="edit C1, +C9, +C10")
    assert sorted(compiled_codes) == ["C1", "C10", "C9"]  # compiled together, unchanged ones reused
    d = svc.diff_versions(v1, v2)
    assert d["added"] == ["C9", "C10"]
    assert d["changed"][0]["code"] == "C1" and {"instruction", "judge_spec"} <= set(d["changed"][0]["fields"]) and d["changed"][0]["requires_rejudge"]
    compiled_codes.clear()
    v3 = svc.create_version(db, v1.checklist_id, {"criteria": original}, change_note="revert to v1")
    assert compiled_codes == []  # C1 text equals v1 again: v1's reviewed spec is reused, not recompiled
    assert {c.code: c.judge_spec for c in v3.criteria} == {c.code: c.judge_spec for c in v1.criteria}


def test_specs_compile_before_anything_is_written(db, make_session, monkeypatch):
    """The SQLite write lock must not be held while the LLM compiles (jobs write progress meanwhile, busy_timeout is 5 s)."""
    seen: list[tuple[str, int, int]] = []

    async def probing_compile(crit):
        s = make_session()  # same in-memory connection: rows flushed but not committed would be visible here
        seen.append((crit["code"], s.execute(select(func.count(Checklist.id))).scalar_one(),
                     s.execute(select(func.count(ChecklistVersion.id))).scalar_one()))
        s.close()
        return compiler.fallback_spec(crit)

    monkeypatch.setattr(compiler, "compile_spec_async", probing_compile)
    payload = seed_payload()
    payload["criteria"][0]["judge_spec"] = None
    v1 = svc.create_checklist(db, payload)
    svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="+C9")
    assert seen == [("C1", 0, 0), ("C9", 1, 1)]


def test_compile_specs_false_stores_no_spec(db, compiled_codes):
    v1 = make_v1(db)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="no compile", compile_missing=False)
    assert compiled_codes == [] and next(c for c in v2.criteria if c.code == "C9").judge_spec is None


def test_ensure_seed_validates_and_stores_the_organizer_checklist(db, compiled_codes):
    svc.ensure_seed(db)
    svc.ensure_seed(db)  # second start: sync only, no second checklist
    assert db.execute(select(func.count(Checklist.id))).scalar_one() == 1
    v = svc.active_version(db)
    assert v.version_no == 1 and [c.code for c in sorted(v.criteria, key=lambda c: c.order)] == CODES
    assert all(c.judge_spec == SPECS[c.code] for c in v.criteria) and compiled_codes == []
    assert next(c for c in v.criteria if c.code == "C8").critical_value == "yes"


# ============================================================================================ API: validation, drafts, export


def _api_seed(client) -> dict[str, Any]:
    r = client.post("/api/checklists", json=seed_payload() | {"change_note": "v1"})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_create_rejects_invalid_payload_with_envelope(client, make_session):
    p = seed_payload()
    p["criteria"][2]["weight"] = "двадцать"
    p["criteria"][7]["weight"] = 5
    r = client.post("/api/checklists", json=p)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_checklist" and CYRILLIC.search(err["message"])
    assert {"criteria[2].weight", "criteria[7].weight"} <= fields_of(err["errors"])
    s = make_session()
    assert s.execute(select(func.count(Checklist.id))).scalar_one() == 0  # nothing written
    s.close()
    r = client.post("/api/checklists", json={"name": "Без критериев"})
    assert r.status_code == 422 and r.json()["error"]["errors"][0]["field"] == "criteria"


def test_api_new_version_validation_and_not_found(client):
    v1 = _api_seed(client)
    bad = {"criteria": [dict(C9, title=" ")], "change_note": "bad"}
    r = client.post(f"/api/checklists/{v1['checklist_id']}/versions", json=bad)
    assert r.status_code == 422 and "criteria[0].title" in fields_of(r.json()["error"]["errors"])
    assert len(client.get(f"/api/checklists/{v1['checklist_id']}").json()["versions"]) == 1
    r = client.post("/api/checklists/999/versions", json=bad)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_api_version_responses_carry_weights_export_ids_and_warnings(client):
    v1 = _api_seed(client)
    assert v1["weights"]["total"] == 100.0 and v1["export_ids"]["C8"] == "critical" and v1["warnings"] == []
    crits = [c for c in v1["criteria"]]
    body = {"criteria": crits + [dict(C9, section="S5")], "change_note": "+C9 in a new section"}
    r = client.post(f"/api/checklists/{v1['checklist_id']}/versions", json=body)
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["weights"]["total"] == 110.0 and v2["weights"]["normalized"]["C9"] == 9.09
    assert {"code": "S5", "title": "S5"} in v2["sections"]
    assert "criteria[8].section" in fields_of(v2["warnings"])
    got = client.get(f"/api/checklists/{v1['checklist_id']}/versions/{v2['id']}").json()
    assert got["weights"] == v2["weights"] and got["export_ids"] == v2["export_ids"] and "warnings" not in got


def test_api_activate_false_keeps_the_previous_active_version(client):
    v1 = _api_seed(client)
    cl_id = v1["checklist_id"]
    crits = [dict(c, weight=10) if c["code"] == "C7" else c for c in v1["criteria"]]
    r = client.post(f"/api/checklists/{cl_id}/versions", json={"criteria": crits, "change_note": "draft: C7 = 10", "activate": False})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["is_active"] is False and draft["version_no"] == 2
    cl = client.get(f"/api/checklists/{cl_id}").json()
    assert cl["active_version"]["id"] == v1["id"]
    assert {v["id"]: v["is_active"] for v in cl["versions"]} == {v1["id"]: True, draft["id"]: False}
    assert client.get("/api/checklists/active").json()["id"] == v1["id"]
    r = client.post(f"/api/checklists/{cl_id}/versions/{draft['id']}/activate")
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert client.get("/api/checklists/active").json()["id"] == draft["id"]
    v3 = client.post(f"/api/checklists/{cl_id}/versions", json={"criteria": crits, "change_note": "default activates"}).json()
    cl = client.get(f"/api/checklists/{cl_id}").json()
    assert v3["is_active"] is True and [v["is_active"] for v in cl["versions"]] == [False, False, True]


def test_api_export_round_trip(client, compiled_codes):
    v1 = _api_seed(client)
    r = client.get(f"/api/checklists/{v1['checklist_id']}/versions/{v1['id']}/export")
    assert r.status_code == 200 and "content-disposition" not in r.headers
    doc = r.json()
    assert doc["format"] == "cqa.checklist/1" and doc["name"] == SEED["name"]
    assert doc["exported_from"] == {"checklist_id": v1["checklist_id"], "version_id": v1["id"], "version_no": 1, "change_note": "v1",
                                    "exported_at": doc["exported_from"]["exported_at"]}
    assert all("id" not in c and c["judge_spec"] == SPECS[c["code"]] for c in doc["criteria"])

    imported = client.post("/api/checklists", json=doc)
    assert imported.status_code == 200, imported.text
    new = imported.json()
    assert new["checklist_id"] != v1["checklist_id"] and compiled_codes == []  # specs travel with the export
    doc2 = client.get(f"/api/checklists/{new['checklist_id']}/versions/{new['id']}/export").json()
    for key in ("name", "description", "scoring_rules", "sections", "criteria"):
        assert doc2[key] == doc[key], key
    assert new["weights"] == v1["weights"] and new["export_ids"] == v1["export_ids"]

    # the same file can also become a new version of an existing checklist: nothing changes, nothing to re-judge
    same = client.post(f"/api/checklists/{v1['checklist_id']}/versions", json=doc | {"change_note": "re-import"}).json()
    d = client.get(f"/api/checklists/{v1['checklist_id']}/versions/{v1['id']}/diff/{same['id']}").json()
    assert d["rejudge_codes"] == [] and d["changed"] == []


def test_api_import_endpoint(client, compiled_codes):
    v1 = _api_seed(client)
    doc = client.get(f"/api/checklists/{v1['checklist_id']}/versions/{v1['id']}/export").json()
    r = client.post("/api/checklists/import", json=doc)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["change_note"] == f"Импорт: чек-лист #{v1['checklist_id']}, версия 1" and new["version_no"] == 1 and compiled_codes == []
    assert [c["judge_spec"] for c in new["criteria"]] == [c["judge_spec"] for c in doc["criteria"]]
    r = client.post("/api/checklists/import", json=doc | {"format": "excel/2"})
    assert r.status_code == 422 and r.json()["error"]["errors"][0]["field"] == "format"
    broken = copy.deepcopy(doc)
    broken["criteria"][0]["weight"] = -1
    r = client.post("/api/checklists/import", json=broken)
    assert r.status_code == 422 and "criteria[0].weight" in fields_of(r.json()["error"]["errors"])
    plain = {"name": "Свой чек-лист", "criteria": [C9]}  # a hand-written file without format / judge specs
    r = client.post("/api/checklists/import", json=plain | {"change_note": "мой файл"})
    assert r.status_code == 200 and r.json()["change_note"] == "мой файл" and compiled_codes == ["C9"]


def test_api_export_download_and_not_found(client):
    v1 = _api_seed(client)
    r = client.get(f"/api/checklists/{v1['checklist_id']}/versions/{v1['id']}/export", params={"download": "true"})
    assert r.status_code == 200 and r.headers["content-disposition"] == f'attachment; filename="checklist-{v1["checklist_id"]}-v1.json"'
    assert client.get(f"/api/checklists/{v1['checklist_id']}/versions/999/export").json()["error"]["code"] == "not_found"
    assert client.get(f"/api/checklists/999/versions/{v1['id']}/export").status_code == 404


# ============================================================================================ test-criterion


def _trial(client, **body):
    return client.post("/api/checklists/test-criterion", json=body)


def _counts(make_session) -> tuple[int, int, int]:
    s = make_session()
    try:
        return tuple(s.execute(select(func.count(m.id))).scalar_one() for m in (Evaluation, EvaluationCriterion, LlmTrace))  # type: ignore[return-value]
    finally:
        s.close()


def test_trial_judges_one_draft_criterion_without_persisting(client, db, make_session, judge, compiled_codes):
    add_conversation(db)
    r = _trial(client, criterion=C9, conversation_id=CID, provider="anthropic")
    assert r.status_code == 200, r.text
    out = r.json()
    assert judge.calls == ["C9"] and judge.requested == [("anthropic", "claude-opus-5-5")]
    assert out["verdict"]["criterion_code"] == "C9" and out["verdict"]["answer"] == "no" and out["verdict"]["applicable"] is True
    assert out["points"] == 0.0 and out["max_points"] == 1
    assert out["judge_spec"]["_compiled_by"] == "test-compiler" and out["judge_spec_source"] == "compiled" and compiled_codes == ["C9"]
    assert out["cost_usd"] == 0.01 and out["latency_ms"] >= 0 and out["model"] == "stub:claude-opus-5-5"
    assert out["input_tokens"] == 1000 and out["output_tokens"] == 100 and out["persisted"] is False
    assert (out["answer"], out["applicable"], out["confidence"], out["score"]) == ("no", True, 0.9, 0.0)  # captured by the audit trail
    assert out["verdict"]["evidence"][0]["turn_idx"] == 2
    assert _counts(make_session) == (0, 0, 0)


def test_trial_spec_can_be_saved_with_the_version(client, db, judge, compiled_codes):
    v1 = make_v1(db)
    add_conversation(db)
    out = _trial(client, criterion=C9, conversation_id=CID).json()
    compiled_codes.clear()
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [dict(C9, judge_spec=out["judge_spec"])]}, change_note="+C9")
    assert compiled_codes == [] and next(c for c in v2.criteria if c.code == "C9").judge_spec == out["judge_spec"]


def test_trial_with_given_spec_fake_provider_and_no_compile(client, db, judge, compiled_codes):
    add_conversation(db)
    spec = compiler.fallback_spec(C9)
    out = _trial(client, criterion=dict(C9, judge_spec=spec), conversation_id=CID).json()
    assert out["judge_spec"] == spec and out["judge_spec_source"] == "provided"
    out = _trial(client, criterion=C9, conversation_id=CID, provider="fake").json()
    assert out["judge_spec_source"] == "fallback" and out["provider"] == "fake" and judge.requested[-1] == ("fake", None)
    out = _trial(client, criterion=C9, conversation_id=CID, compile_spec=False).json()
    assert out["judge_spec_source"] == "fallback"
    assert compiled_codes == []


def test_trial_masks_pii_for_cloud_providers_only(client, db, judge):
    turns = TURNS[:4] + [("operator", "01:40", "Перезвоните нам по номеру +7 701 123 45 67.")] + TURNS[5:]
    add_conversation(db, turns=turns)
    _trial(client, criterion=C9, conversation_id=CID, provider="anthropic")
    _trial(client, criterion=C9, conversation_id=CID, provider="local")
    assert "+7 701 123 45 67" not in judge.transcripts[0] and "[PHONE_1]" in judge.transcripts[0]
    assert "+7 701 123 45 67" in judge.transcripts[1] and judge.requested[1][0] == "local"


def test_trial_with_ensemble_votes(client, db, judge):
    add_conversation(db)
    out = _trial(client, criterion=C9, conversation_id=CID, provider="ensemble").json()
    assert len(out["votes"]) == 3 and out["cost_usd"] == 0.03 and out["model"].count("stub:") == 3


def test_trial_validation_errors(client, db, judge):
    add_conversation(db)
    r = _trial(client, criterion={"code": "9", "title": "", "na_allowed": True}, conversation_id=CID)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_criterion" and err["message"].startswith("Критерий не прошёл проверку")
    assert {"criterion.code", "criterion.title", "criterion.na_condition"} <= fields_of(err["errors"])
    r = _trial(client, criterion=C9, provider="gpt")
    assert r.status_code == 422 and {"conversation_id", "provider"} <= fields_of(r.json()["error"]["errors"])
    r = _trial(client, conversation_id=CID)
    assert r.status_code == 422 and "criterion" in fields_of(r.json()["error"]["errors"])
    assert judge.calls == []


def test_trial_unknown_conversation_unavailable_provider_and_failed_judge(client, db, judge, monkeypatch):
    add_conversation(db)
    r = _trial(client, criterion=C9, conversation_id="nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    def refuse(name, model=None):
        raise ProviderError("no key")

    monkeypatch.setattr(judge_runner, "get_provider", refuse)
    r = _trial(client, criterion=C9, conversation_id=CID)
    assert r.status_code == 400 and r.json()["error"]["code"] == "provider_unavailable"
    monkeypatch.setattr(judge_runner, "get_provider", judge.provider)
    judge.fail = {"C9"}
    r = _trial(client, criterion=C9, conversation_id=CID)
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "judge_failed" and "stub refused C9" in err["message"] and err["model"] == "stub:claude-opus-5-5"


def test_trial_respects_the_daily_budget(client, db, judge, monkeypatch):
    v1 = make_v1(db)
    add_conversation(db)
    db.add(Evaluation(conversation_id=CID, checklist_version_id=v1.id, status="completed", cost_usd=0.99))
    db.commit()
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    r = _trial(client, criterion=C9, conversation_id=CID)
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "budget_exceeded" and err["budget"]["spent_24h"] == 0.99 and judge.calls == []
    assert _trial(client, criterion=C9, conversation_id=CID, provider="local").status_code == 200  # free providers are not capped


# ============================================================================================ partial re-evaluation (control check)


def test_full_evaluation_of_the_organizer_checklist(db, runner_db, judge):
    v1 = make_v1(db)
    add_conversation(db)
    ev, rows = load(runner_db, evaluate(v1))
    assert sorted(judge.calls) == sorted(CODES)
    assert ev.status == "completed" and ev.total_score == 100.0 and ev.critical_error is False
    assert all(COPIED not in (r.flags or []) for r in rows.values())


def test_partial_after_adding_c9_judges_only_c9(db, runner_db, judge, compiled_codes):
    """(a) v2 = v1 + C9 (weight 10): only C9 is judged, the eight others are copied, totals use weights 110 → 100."""
    v1 = make_v1(db)
    add_conversation(db)
    e1 = evaluate(v1)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="v2: +C9")
    assert compiled_codes == ["C9"]
    rejudge = svc.diff_versions(v1, v2)["rejudge_codes"]
    assert rejudge == ["C9"]
    judge.calls.clear()
    ev, rows = load(runner_db, evaluate(v2, base=e1, only=rejudge))
    assert judge.calls == ["C9"]
    assert ev.mode == "partial" and ev.status == "completed" and ev.cost_usd == 0.01
    assert set(rows) == set(CODES) | {"C9"}
    assert all(COPIED in rows[c].flags for c in CODES) and COPIED not in rows["C9"].flags
    assert rows["C9"].answer == "no" and rows["C9"].score == 0.0
    v2_ids = {c.code: c.id for c in v2.criteria}
    assert all(r.criterion_id == v2_ids[code] for code, r in rows.items())
    assert ev.total_score == 90.91 and ev.final_total_score == 90.91  # 100 of 110 weighted points
    assert rows["C1"].weight == 10.0 and rows["C1"].weight_applied == pytest.approx(10 * 100 / 110)
    assert sum(r.weight_applied for r in rows.values()) == pytest.approx(100.0)
    s3 = next(s for s in ev.sections if s["code"] == "S3")
    assert s3 == {"code": "S3", "title": "Ведение и завершение диалога", "score": 31.82, "max": 40.91}


def test_partial_after_replacing_c6_with_c6b(db, runner_db, judge):
    """(b) v3 replaces C6 by C6b: C6 disappears, C6b is judged, C9 is copied from v2, totals recomputed."""
    v1 = make_v1(db)
    add_conversation(db)
    e1 = evaluate(v1)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="v2: +C9")
    e2 = evaluate(v2, base=e1, only=svc.diff_versions(v1, v2)["rejudge_codes"])
    v3 = svc.create_version(db, v1.checklist_id, {"criteria": [C6B if c["code"] == "C6" else c for c in studio_criteria(v2)]},
                            change_note="v3: C6 → C6b")
    d = svc.diff_versions(v2, v3)
    assert d["added"] == ["C6b"] and d["removed"] == ["C6"] and d["changed"] == [] and d["rejudge_codes"] == ["C6b"]
    judge.calls.clear()
    ev, rows = load(runner_db, evaluate(v3, base=e2, only=d["rejudge_codes"]))
    assert judge.calls == ["C6b"]
    assert "C6" not in rows and set(rows) == (set(CODES) - {"C6"}) | {"C6b", "C9"}
    assert COPIED not in rows["C6b"].flags and rows["C6b"].answer == "no"
    assert COPIED in rows["C9"].flags and rows["C9"].answer == "no"
    assert ev.total_score == 77.27  # (110 − C9 10 − C6b 15) / 110
    s3 = next(s for s in ev.sections if s["code"] == "S3")
    assert s3["score"] == 18.18 and s3["max"] == 40.91


def test_partial_after_weight_only_change_needs_no_llm_calls(db, runner_db, judge):
    """(c) a weight change alone re-judges nothing: rows are copied and the total is recomputed with the new weights."""
    v1 = make_v1(db)
    add_conversation(db)
    e1 = evaluate(v1)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="v2: +C9")
    e2 = evaluate(v2, base=e1, only=["C9"])
    v3 = svc.create_version(db, v1.checklist_id, {"criteria": [dict(c, weight=5) if c["code"] == "C9" else c for c in studio_criteria(v2)]},
                            change_note="v3: C9 weight 5")
    d = svc.diff_versions(v2, v3)
    assert d["changed"] == [{"code": "C9", "fields": ["weight"], "requires_rejudge": False}] and d["rejudge_codes"] == []
    judge.calls.clear()
    ev, rows = load(runner_db, evaluate(v3, base=e2, only=d["rejudge_codes"]))
    assert judge.calls == [] and ev.cost_usd == 0.0 and ev.status == "completed"
    assert rows["C9"].weight == 5.0 and ev.total_score == 95.24  # 100 of 105 weighted points


# ---------------------------------------------------------------------- runner findings (fixed in the integration step)
# docs/arch/checklists.md Integration requests #1–#4; formerly strict xfails, see docs/arch/integration.md.


def test_job_runner_weight_only_change_needs_no_llm_calls(db, make_session, runner_db, judge, monkeypatch):
    from cqa.jobs import runner as jobs_runner

    monkeypatch.setattr(jobs_runner, "SessionLocal", make_session)
    v1 = make_v1(db)
    add_conversation(db)
    evaluate(v1)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": [dict(c, weight=10) if c["code"] == "C7" else c for c in studio_criteria(v1)]},
                            change_note="C7 weight 10")
    rejudge = svc.diff_versions(v1, v2)["rejudge_codes"]
    require(rejudge == [], f"weight-only diff should need no rejudge, got {rejudge}")
    db.add(Job(id="j_gov_test", type="evaluate", status="queued", total=1, params={}))
    db.commit()
    judge.calls.clear()
    asyncio.run(jobs_runner._run(jobs_runner.JobState("j_gov_test"), [CID], v2.id, "anthropic", None, "partial", v1.id, rejudge))
    s = make_session()
    job = s.get(Job, "j_gov_test")
    require(job.status == "completed" and job.done == 1, f"job did not complete: {job.status} {job.log}")
    s.close()
    assert judge.calls == [], f"weight-only partial job re-judged {len(judge.calls)} criteria"


def test_partial_rejudges_criteria_that_failed_in_the_base_evaluation(db, runner_db, judge):
    v1 = make_v1(db)
    add_conversation(db)
    judge.fail = {"C5"}
    e1 = evaluate(v1)
    base, base_rows = load(runner_db, e1)
    require(base.status == "completed" and "error" in base_rows["C5"].flags, "base evaluation should have a failed C5 row")
    judge.fail = set()
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="+C9")
    judge.calls.clear()
    ev, rows = load(runner_db, evaluate(v2, base=e1, only=["C9"]))
    assert "C5" in judge.calls, "C5 failed in the base evaluation and was copied as a failure"
    assert "error" not in rows["C5"].flags and rows["C5"].answer == "yes"


def test_chained_partials_keep_a_single_copied_flag(db, runner_db, judge):
    v1 = make_v1(db)
    add_conversation(db)
    e1 = evaluate(v1)
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="+C9")
    e2 = evaluate(v2, base=e1, only=["C9"])
    v3 = svc.create_version(db, v1.checklist_id, {"criteria": [C6B if c["code"] == "C6" else c for c in studio_criteria(v2)]}, change_note="C6b")
    _, rows = load(runner_db, evaluate(v3, base=e2, only=["C6b"]))
    assert rows["C1"].flags.count(COPIED) == 1, rows["C1"].flags


def test_partial_keeps_reviewer_decisions_on_copied_criteria(db, make_session, runner_db, judge):
    from cqa.review import service as review

    v1 = make_v1(db)
    add_conversation(db)
    e1 = evaluate(v1)
    s = make_session()
    row = s.execute(select(EvaluationCriterion).where(EvaluationCriterion.evaluation_id == e1, EvaluationCriterion.code == "C7")).scalar_one()
    ap = review.create_appeal(s, row, "Прощания не было", "Operator")
    review.decide(s, ap, action="change", new_answer="no", reason="Оператор не попрощался", reviewer_label="Reviewer", add_to_fewshot=False)
    require(s.get(Evaluation, e1).final_total_score == 95.0, "reviewer decision should lower the final total of e1 to 95")
    s.close()
    v2 = svc.create_version(db, v1.checklist_id, {"criteria": studio_criteria(v1) + [C9]}, change_note="+C9")
    ev, rows = load(runner_db, evaluate(v2, base=e1, only=["C9"]))
    assert rows["C7"].final_answer == "no" and rows["C7"].review_state == "changed"
    assert ev.final_total_score == 86.36  # (110 − C9 10 − C7 5) / 110 with the reviewer's C7
