from datetime import date

from core.rules import detect_all


def test_birth_year_suggestions_use_injected_date():
    for text in ("民國78年次男性", "1989年生男性"):
        for year, age in ((2026, 37), (2027, 38)):
            risk = detect_all(text, today=date(year, 10, 8))["combination_risk"]
            assert risk["score"] == 0.5
            assert f"「{age}歲」建議泛化為「35-39歲」" in risk["suggestions"]
