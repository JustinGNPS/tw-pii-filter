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


def test_fullwidth_age_reduction_preserves_other_original_characters():
    from core.redact.mapping import MappingTable
    from proxy.masker import reduce_residual_text

    text = "代碼ＡＢＣ：３５歲女性"
    reduced, _, final, plan, _ = reduce_residual_text(text, [], MappingTable(), "strict")
    assert reduced == "代碼ＡＢＣ：成年年齡層人士"
    assert final["contributing_types"] == []
    assert plan["met_target"]


def test_reduction_masks_actual_span_not_an_earlier_identical_word():
    from core.redact.mapping import MappingTable
    from core.redact.restorer import restore_text
    from proxy.masker import mask_with_residual, reduce_residual_text

    text = "提到工程師；A123456789 的職稱是工程師"
    start = text.rindex("工程師")
    id_start = text.index("A123456789")
    spans = [
        {"type": "TW_ID", "text": "A123456789", "start": id_start, "end": id_start + 10},
        {"type": "POSITION", "text": "工程師", "start": start, "end": start + 3},
    ]
    table = MappingTable()
    masked, residual = mask_with_residual(text, spans, table, {"POSITION"})
    reduced, _, _, _, counts = reduce_residual_text(masked, residual, table, "strict")
    assert reduced == "提到工程師；[TW_ID_1] 的職稱是[POSITION_1]"
    assert counts == {"POSITION": 1}
    assert restore_text(reduced, table)[0] == text
