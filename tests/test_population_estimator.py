"""Layer 3 v2：官方人口交叉統計上限估算測試。"""

import core.risk.combination_risk as combination_risk

from core.risk.combination_risk import compute_combination_risk
from core.risk.population_estimator import (
    estimate_population_upper_bound,
    extract_county_city,
    load_population_snapshot,
)


def _address(value: str) -> dict:
    return {"start": 0, "end": len(value), "type": "ADDRESS", "text": value}


def test_snapshot_integrity_and_period():
    snapshot = load_population_snapshot()
    assert snapshot["period"] == "2026-07"
    assert len(snapshot["regions"]) == 23  # 全國 + 22 縣市
    for by_sex in snapshot["regions"].values():
        assert len(by_sex["all"]["ages"]) == 100
        assert by_sex["male"]["total"] + by_sex["female"]["total"] == by_sex["all"]["total"]


def test_extract_county_city_normalizes_tai_variant():
    assert extract_county_city([_address("台北市信義區")]) == "臺北市"


def test_extract_county_city_rejects_ambiguous_multiple_regions():
    spans = [_address("臺北市"), _address("新竹市")]
    assert extract_county_city(spans) is None


def test_joint_age_gender_region_estimate_uses_official_cell():
    result = compute_combination_risk(
        "35歲女性住在台北市信義區。",
        [_address("台北市信義區")],
    )
    estimate = result["population_estimate"]
    assert estimate["population_upper_bound"] == 15689
    assert estimate["dimensions"] == {
        "age": 35,
        "gender": "female",
        "region": "臺北市",
    }
    assert estimate["covered_types"] == ["ADDRESS", "AGE", "GENDER"]
    assert estimate["data_period"] == "2026-07"
    assert estimate["source_dataset_id"] == "14226"


def test_generalization_simulates_one_dimension_at_a_time():
    result = compute_combination_risk(
        "35歲女性住在台北市。",
        [_address("台北市")],
    )
    estimate = result["population_estimate"]
    generalized = {item["dimension"]: item for item in estimate["generalizations"]}
    assert generalized["age"]["to"] == {"min": 35, "max": 39}
    assert generalized["age"]["population_upper_bound"] == 81826
    assert generalized["region"]["population_upper_bound"] == 154601
    assert generalized["gender"]["population_upper_bound"] == 30351
    assert all(
        item["population_upper_bound"] >= estimate["population_upper_bound"]
        for item in generalized.values()
    )


def test_uncovered_position_is_reported_instead_of_multiplied_independently():
    spans = [
        _address("台北市"),
        {"start": 10, "end": 13, "type": "POSITION", "text": "工程師"},
    ]
    estimate = compute_combination_risk("35歲女性住在台北市，是工程師。", spans)[
        "population_estimate"
    ]
    assert estimate["uncovered_types"] == ["POSITION"]
    assert estimate["method"] == "official_joint_population_upper_bound"


def test_two_supported_dimensions_can_be_estimated_directly():
    snapshot = load_population_snapshot()
    estimate = estimate_population_upper_bound(
        age=35,
        gender=None,
        region="新竹市",
        contributing_types=["ADDRESS", "AGE"],
        snapshot=snapshot,
    )
    assert estimate is not None
    assert estimate["population_upper_bound"] == 6309
    assert estimate["covered_types"] == ["ADDRESS", "AGE"]


def test_estimate_is_omitted_when_only_one_supported_dimension_is_known():
    spans = [{"start": 4, "end": 7, "type": "POSITION", "text": "工程師"}]
    result = compute_combination_risk("35歲的工程師。", spans)
    assert result["score"] > 0
    assert "population_estimate" not in result


def test_birth_year_is_not_treated_as_exact_single_age():
    result = compute_combination_risk("民國78年次的女性。")
    assert result["score"] > 0
    assert "population_estimate" not in result


def test_weighted_score_still_works_when_snapshot_is_unavailable(monkeypatch):
    def unavailable(**_kwargs):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(combination_risk, "estimate_population_upper_bound", unavailable)
    result = compute_combination_risk("35歲女性。")
    assert result["score"] == 0.5
    assert result["risk_level"] == "中"
    assert "population_estimate" not in result
def test_multiple_ages_do_not_pick_first_person():
    from core.risk.combination_risk import compute_combination_risk

    result = compute_combination_risk("35歲與65歲男性")
    assert "population_estimate" not in result
    assert result["score"] == 0.5

