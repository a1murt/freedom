from cqa.analytics.violations import classify, label


def test_classify_three_checklist_types():
    assert classify("Грубые ошибки — Да. Оператор оскорбил клиента, обвинив его в неспособности читать.") == ["insult"]
    assert classify("Грубые ошибки — Да. Оператор предложил перевести деньги ему на карту, в обход компании.") == ["paid_bypass"]
    assert classify("Да. Оператор попросил личный номер клиента для платной консультации в частном порядке.") == ["paid_bypass"]
    assert classify("Грубые ошибки — Да. Оператор раскрыл ограничение на вывод средств по счёту без подтверждённой идентификации.") == ["disclosure"]
    assert classify("Gross errors — yes. The operator offered private advice for a fee via a personal number.") == ["paid_bypass"]


def test_classify_falls_back_to_c3_and_other():
    assert classify("Да. Нарушение.", c3_answer="no") == ["disclosure"]
    assert classify("Да. Нарушение.") == ["other"]
    assert "insult" in classify("Да.", flags=["insult_detected"])


def test_labels():
    assert label("insult") == "Оскорбление клиента"
    assert label("disclosure", "en") == "Disclosure of confidential information"
