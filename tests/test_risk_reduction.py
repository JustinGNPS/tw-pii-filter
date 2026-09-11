"""殘餘組合風險自動降階的策略與文字泛化測試。"""

import pytest

from core.risk import reduction
from core.risk.combination_risk import compute_combination_risk


def _assessment(*types):
    return {
        "score": reduction.score_types(types),
        "contributing_types": list(types),
    }


def test_off_完整保留既有行為():
    plan = reduction.plan_reduction(
        _assessment("COMPANY", "GENDER", "POSITION"), "off"
    )

    assert plan["applied_types"] == []
    assert plan["planned_score"] == 0.5


def test_permissive_未達高風險時不額外處理():
    plan = reduction.plan_reduction(
        _assessment("COMPANY", "GENDER", "POSITION"), "permissive"
    )

    assert plan["applied_types"] == []
    assert plan["met_target"] is True


def test_permissive_只做降到警告門檻以下所需的步驟():
    plan = reduction.plan_reduction(
        _assessment("AGE", "GENDER", "POSITION"), "permissive"
    )

    assert plan["applied_types"] == ["GENDER"]
    assert plan["planned_score"] == 0.55


def test_balanced_優先省略性別再遮公司並保留職稱():
    plan = reduction.plan_reduction(
        _assessment("COMPANY", "GENDER", "POSITION"), "balanced"
    )

    assert plan["applied_types"] == ["GENDER", "COMPANY"]
    assert plan["planned_score"] == 0.0
    assert plan["met_target"] is True


def test_strict_處理所有已辨識準識別子():
    plan = reduction.plan_reduction(
        _assessment("AGE", "COMPANY", "GENDER", "POSITION"), "strict"
    )

    assert plan["applied_types"] == ["GENDER", "COMPANY", "AGE", "POSITION"]
    assert plan["planned_score"] == 0.0


def test_age_gender_泛化後不再被精確準識別子規則命中():
    text = "這位35歲的女性與民國78年次的先生需要協助。"

    generalized, actions = reduction.generalize_text_types(text, {"AGE", "GENDER"})
    risk = compute_combination_risk(generalized)

    assert "35歲" not in generalized
    assert "民國78年次" not in generalized
    assert "女性" not in generalized
    assert "先生" not in generalized
    assert "成年年齡層" in generalized
    assert {item["type"] for item in actions} == {"AGE", "GENDER"}
    assert risk["contributing_types"] == []


def test_unknown_policy_明確拒絕而非悄悄降低保護():
    with pytest.raises(ValueError, match="未知的風險降階策略"):
        reduction.normalize_policy("mystery")
