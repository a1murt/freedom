"""results.json (organizer JSON 1.0): mapping, scoring consistency and validation."""
import copy

from cqa.export.results_json import (
    REQUIRED_IDS,
    build_document,
    build_record,
    evidence_turns,
    model_block,
    recompute_final_score,
    validate_document,
)


def crit(code, answer, *, applicable=True, answer_type="binary", evidence=(9,), weight=None, max_points=None, flags=None, explanation="ok", checked_span=None):
    w = {"C1": 10, "C2": 15, "C3": 20, "C4": 20, "C5": 15, "C6": 15, "C7": 5, "C8": 0}[code]
    mp = {"C4": 20, "C6": 10}.get(code, 1 if code != "C8" else 0)
    return {"code": code, "answer": answer, "applicable": applicable, "answer_type": answer_type, "weight": w if weight is None else weight,
            "max_points": mp if max_points is None else max_points, "explanation": explanation, "na_reason": None if applicable else "условие N/A выполнено",
            "evidence": [{"turn_idx": i} for i in evidence], "checked_span": checked_span, "flags": flags or []}


def evaluation(**over):
    rows = [crit("C1", "yes"), crit("C2", None, applicable=False), crit("C3", None, applicable=False), crit("C4", "15", answer_type="scale"),
            crit("C5", "yes"), crit("C6", "7", answer_type="scale"), crit("C7", "yes"), crit("C8", "no")]
    ev = {"status": "completed", "criteria": rows, "model": "anthropic:claude-opus-5-5", "provider": "anthropic"}
    ev.update(over)
    return ev


def test_build_record_maps_values_and_turns():
    r = build_record("p1-ru-001", evaluation(), n_turns=12)
    assert r["status"] == "ok"
    vals = {c["criterion_id"]: c["value"] for c in r["criteria"]}
    assert vals == {"contact": True, "discovery": None, "identification": None, "consultation": 15, "empathy": True, "dialogue": 7, "closure": True, "critical": False}
    assert [c["criterion_id"] for c in r["criteria"]] == list(REQUIRED_IDS)
    assert r["criteria"][0]["evidence_turns"] == [10]  # 0-based idx 9 -> 1-based 10
    # applicable weights: 10+20+15+15+5 = 65; earned: 10 + 15 + 15 + 10.5 + 5 = 55.5 -> 85.38
    assert r["score"] == {"final_score": 85.38, "critical_flag": False}


def test_na_rationale_mentions_condition():
    r = build_record("x", evaluation(), n_turns=12)
    disc = next(c for c in r["criteria"] if c["criterion_id"] == "discovery")
    assert disc["value"] is None and disc["rationale"].startswith("N/A")


def test_critical_flag_follows_c8_and_does_not_zero_total():
    ev = evaluation()
    ev["criteria"][7] = crit("C8", "yes")
    r = build_record("x", ev, n_turns=12)
    assert r["score"]["critical_flag"] is True and r["score"]["final_score"] == 85.38


def test_failed_criterion_gives_error_record():
    ev = evaluation()
    ev["criteria"][3] = crit("C4", None, answer_type="scale", flags=["error"], explanation="Ошибка оценки: timeout")
    r = build_record("x", ev, n_turns=12)
    assert r == {"conversation_id": "x", "status": "error", "criteria": [], "score": None, "error": r["error"]}
    assert "C4" in r["error"]


def test_missing_or_failed_evaluation_gives_error_record():
    assert build_record("x", None, n_turns=3)["status"] == "error"
    assert build_record("x", {"status": "failed", "error": "rate limit"}, n_turns=3)["error"] == "rate limit"


def test_checked_span_used_when_no_evidence():
    assert evidence_turns([], {"from_turn": 9, "to_turn": 11}, 12) == [10, 12]
    assert evidence_turns([{"turn_idx": 30}], None, 12) == []  # out of range dropped


def test_recompute_matches_engine_rounding():
    vals = {"contact": True, "discovery": None, "identification": None, "consultation": 20, "empathy": True, "dialogue": 7, "closure": True, "critical": False}
    # weights 65 applicable: 10+20+15+10.5+5 = 60.5 / 65 * 100 = 93.0769 -> 93.08
    assert recompute_final_score(vals) == 93.08
    assert recompute_final_score({k: None for k in REQUIRED_IDS}) is None


def test_model_block_modes():
    assert model_block(["anthropic:claude-opus-5-5"]) == {"mode": "external_api", "name": "claude-opus-5-5"}
    m = model_block(["anthropic:claude-opus-5-5,local:qwen3:8b"])
    assert m["mode"] == "hybrid" and "claude-opus-5-5" in m["name"]


def test_validate_accepts_good_document_and_catches_errors():
    good = build_document([build_record("a", evaluation(), n_turns=12), build_record("b", None, n_turns=5)], team_name="T", model={"mode": "external_api", "name": "m"})
    errors, warnings = validate_document(good, expected_ids=["a", "b"], expected_count=2, turn_counts={"a": 12, "b": 5})
    assert errors == []
    assert any("status error" in w for w in warnings)

    bad = copy.deepcopy(good)
    bad["results"][0]["criteria"][5]["value"] = 5  # dialogue must be 0/4/7/10
    bad["results"][0]["criteria"].append(copy.deepcopy(bad["results"][0]["criteria"][0]))  # duplicate contact
    bad["results"][0]["score"]["critical_flag"] = True
    bad["results"].append(copy.deepcopy(bad["results"][1]))  # duplicate id
    errors, _ = validate_document(bad, expected_count=2)
    text = " | ".join(errors)
    assert "dialogue value 5 not allowed" in text
    assert "exactly once" in text
    assert "critical_flag must equal" in text
    assert "duplicate conversation_id" in text
    assert "expected exactly 2 records" in text


def test_validate_flags_turns_beyond_transcript_and_unknown_ids():
    doc = build_document([build_record("a", evaluation(), n_turns=12)], team_name="T", model={"mode": "external_api", "name": "m"})
    errors, _ = validate_document(doc, expected_ids=["a", "z"], turn_counts={"a": 5})
    assert any("exceed transcript length" in e for e in errors)
    assert any("missing conversation_ids" in e for e in errors)


def test_contact_is_true_or_false_on_every_channel():
    # JSON 1.0 appendix: contact is true/false only; the organizers (2026-11-08) left the unrecorded-greeting case of
    # calls to us, and the judge rule credits it, so a null contact is an error on calls too
    ev = evaluation()
    ev["criteria"][0] = crit("C1", None, applicable=False)
    doc = build_document([build_record("call-1", ev, n_turns=40)], team_name="T", model={"mode": "external_api", "name": "m"})
    for channels in ({"call-1": "voice call"}, {"call-1": "agent chat"}, None):
        errors, _ = validate_document(doc, channels=channels)
        assert any("contact must be true or false" in e and "re-judge C1" in e for e in errors), channels
