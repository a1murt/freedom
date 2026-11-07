import json
from pathlib import Path

from cqa.scoring.engine import CriterionInput, answer_points, compute_score

SEED = json.loads((Path(__file__).resolve().parents[1] / "seeds" / "checklist_freedom_v1.json").read_text())
RULES = SEED["scoring_rules"]
SECTIONS = SEED["sections"]
CRIT = {c["code"]: c for c in SEED["criteria"]}


def make(answers: dict[str, object], na: set[str] = frozenset()) -> list[CriterionInput]:
    items = []
    for code, c in CRIT.items():
        if code in na:
            items.append(CriterionInput(code, c["weight"], c["max_points"], None, applicable=False, section=c["section"], is_critical=c["is_critical"]))
            continue
        ans = answers.get(code)
        pts = answer_points(c["options"], ans)
        hit = c["is_critical"] and ans == c.get("critical_value")
        items.append(CriterionInput(code, c["weight"], c["max_points"], pts, True, c["section"], c["is_critical"], hit))
    return items


PERFECT = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"}


def test_weights_sum_to_100():
    assert sum(c["weight"] for c in CRIT.values()) == 100


def test_perfect_is_100():
    r = compute_score(RULES, make(PERFECT), SECTIONS)
    assert r.total == 100.0 and r.critical is False


def test_all_no_is_zero():
    r = compute_score(RULES, make({"C1": "no", "C2": "no", "C3": "no", "C4": 0, "C5": "no", "C6": 0, "C7": "no", "C8": "no"}), SECTIONS)
    assert r.total == 0.0


def test_scale_proportional_to_weight():
    a = dict(PERFECT, C4=15)
    r = compute_score(RULES, make(a), SECTIONS)
    assert r.total == 95.0  # 20 * 15/20 = 15 earned of 20
    b = dict(PERFECT, C6=7)
    r2 = compute_score(RULES, make(b), SECTIONS)
    assert r2.total == 95.5  # 15 * 7/10 = 10.5 earned of 15


def test_na_redistributes_weight():
    # C3 (20) N/A: remaining weight 80 scaled to 100; C1 "no" loses 10*100/80 = 12.5
    a = dict(PERFECT, C1="no")
    r = compute_score(RULES, make(a, na={"C3"}), SECTIONS)
    assert r.total == 87.5
    assert round(r.criteria["C1"].weight_applied, 4) == 12.5
    assert r.criteria["C3"].weight_applied == 0.0


def test_multiple_na_keeps_max_100():
    r = compute_score(RULES, make(PERFECT, na={"C2", "C3", "C4", "C7"}), SECTIONS)
    assert r.total == 100.0
    assert round(r.applicable_weight, 6) == 40


def test_no_applicable_weighted_gives_no_total():
    na = {"C2", "C3", "C4", "C7"}
    items = make({"C8": "no"}, na=na)
    # make C1, C5, C6 not applicable too
    for i in items:
        if i.code in {"C1", "C5", "C6"}:
            i.applicable = False
            i.points = None
    r = compute_score(RULES, items, SECTIONS)
    assert r.total is None


def test_gross_error_flag_does_not_change_total():
    a = dict(PERFECT, C8="yes")
    r = compute_score(RULES, make(a), SECTIONS)
    assert r.total == 100.0 and r.critical is True


def test_rounding_two_decimals_half_up():
    # C3 N/A, C6 = 7: weights 80 -> factor 1.25; C6 earned = 15*1.25*0.7 = 13.125 of 18.75; total = 100 - 5.625 = 94.375 -> 94.38
    a = dict(PERFECT, C6=7)
    r = compute_score(RULES, make(a, na={"C3"}), SECTIONS)
    assert r.total == 94.38


def test_sections_reflect_redistribution():
    r = compute_score(RULES, make(PERFECT, na={"C3"}), SECTIONS)
    s2 = next(s for s in r.sections if s.code == "S2")
    assert s2.max == 25.0 and s2.score == 25.0  # C4 20 * 1.25


def test_answer_points_tolerates_strings_and_labels():
    assert answer_points(CRIT["C4"]["options"], "15") == 15
    assert answer_points(CRIT["C1"]["options"], "Да") == 1
    assert answer_points(CRIT["C1"]["options"], "nope") is None
