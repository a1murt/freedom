"""Answer matching tolerates harmless format differences (review finding 2026-11-08): the validator, the scoring engine
and the results.json exporter agree, so «15.0» or « yes » never costs a retry or turns into a failed criterion."""
from __future__ import annotations

import pytest

from cqa.scoring.engine import answer_points, normalize_answer

BINARY = [{"value": "yes", "label": "Да", "points": 1}, {"value": "no", "label": "Нет", "points": 0}]
C4 = [{"value": "20", "label": "20", "points": 20}, {"value": "15", "label": "15", "points": 15}, {"value": "10", "label": "10", "points": 10},
      {"value": "5", "label": "5", "points": 5}, {"value": "0", "label": "0", "points": 0}]
INT_SCALE = [{"value": 10, "points": 10}, {"value": 7, "points": 7}, {"value": 4, "points": 4}, {"value": 0, "points": 0}]


@pytest.mark.parametrize("answer,value,points", [
    ("yes", "yes", 1), ("Yes", "yes", 1), (" yes ", "yes", 1), ("Да", "yes", 1), ("да", "yes", 1), ("true", "yes", 1),
    ("TRUE", "yes", 1), ("no", "no", 0), ("Нет", "no", 0), ("false", "no", 0),
])
def test_binary_forms(answer, value, points):
    assert normalize_answer(BINARY, answer) == value and answer_points(BINARY, answer) == points


@pytest.mark.parametrize("answer,value,points", [("15", "15", 15), ("15.0", "15", 15), (" 15 ", "15", 15), (15, "15", 15),
                                                 ("0", "0", 0), ("0.0", "0", 0), ("20,0", "20", 20)])
def test_scale_forms(answer, value, points):
    assert normalize_answer(C4, answer) == value and answer_points(C4, answer) == points


def test_int_valued_options_and_misses():
    assert normalize_answer(INT_SCALE, "7.0") == 7 and answer_points(INT_SCALE, "7") == 7
    for bad in ("8", "maybe", "N/A", "", "1", "yes"):
        assert answer_points(C4, bad) is None and normalize_answer(C4, bad) == bad
    assert answer_points(BINARY, "1") is None and answer_points(BINARY, None) is None and normalize_answer(BINARY, None) is None
