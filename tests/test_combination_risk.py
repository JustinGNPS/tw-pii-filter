"""
Layer 3 組合風險評分的單元測試。
回應 A 在 PR #17 review 的要求：「補上 unit test（例如：不同準識別子組合的
分數計算、AGE 正則抓取、cap 在 1.0 的邊界情況）後再重新開 PR」。
"""

import pytest

from core.risk.combination_risk import (
    WARNING_THRESHOLD,
    compute_combination_risk,
    is_warning_worthy,
)


# ---------------------------------------------------------------------------
# 不同準識別子組合的分數計算
# ---------------------------------------------------------------------------

class TestScoreCalculation:
    def test_no_quasi_identifiers_score_zero(self):
        result = compute_combination_risk("這是一段完全不含準識別子的普通文字。")
        assert result["score"] == 0.0
        assert result["contributing_types"] == []
        assert result["risk_level"] == "低"

    def test_single_quasi_identifier_score_zero(self):
        """單一類別不構成「組合」風險，分數應為 0（即使該類別權重不低）。"""
        spans = [{"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"}]
        result = compute_combination_risk("測試公司的員工。", spans)
        assert result["score"] == 0.0
        assert result["contributing_types"] == ["COMPANY"]

    def test_two_types_score_is_sum_of_weights(self):
        spans = [
            {"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"},
            {"start": 5, "end": 8, "type": "POSITION", "text": "工程師"},
        ]
        result = compute_combination_risk("測試公司的工程師。", spans)
        # COMPANY(0.15) + POSITION(0.20) = 0.35
        assert result["score"] == pytest.approx(0.35)
        assert result["contributing_types"] == ["COMPANY", "POSITION"]

    def test_three_types_including_age(self):
        spans = [
            {"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"},
            {"start": 5, "end": 8, "type": "POSITION", "text": "工程師"},
        ]
        result = compute_combination_risk("他35歲，在測試公司當工程師。", spans)
        # AGE(0.35) + COMPANY(0.15) + POSITION(0.20) = 0.70
        assert result["score"] == pytest.approx(0.70)
        assert set(result["contributing_types"]) == {"AGE", "COMPANY", "POSITION"}

    def test_unknown_span_type_ignored(self):
        """不在 QUASI_IDENTIFIER_TYPES 裡的型別（例如 NAME、TW_ID）不計入。"""
        spans = [
            {"start": 0, "end": 3, "type": "NAME", "text": "王小明"},
            {"start": 4, "end": 14, "type": "TW_ID", "text": "A123456789"},
        ]
        result = compute_combination_risk("王小明的身分證是A123456789。", spans)
        assert result["score"] == 0.0
        assert result["contributing_types"] == []


# ---------------------------------------------------------------------------
# cap 在 1.0 的邊界情況
# ---------------------------------------------------------------------------

class TestScoreCap:
    def test_score_capped_at_one(self):
        """所有型別權重加總理論上是 0.35+0.30+0.20+0.15*4+0.10 = 1.55，
        遠超過 1.0，驗證確實被封頂。"""
        spans = [
            {"start": 0, "end": 1, "type": "ADDRESS", "text": "x"},
            {"start": 1, "end": 2, "type": "POSITION", "text": "x"},
            {"start": 2, "end": 3, "type": "COMPANY", "text": "x"},
            {"start": 3, "end": 4, "type": "ORGANIZATION", "text": "x"},
            {"start": 4, "end": 5, "type": "GOVERNMENT", "text": "x"},
            {"start": 5, "end": 6, "type": "SCENE", "text": "x"},
        ]
        result = compute_combination_risk("他35歲，男性。", spans)
        assert result["score"] == 1.0
        assert result["risk_level"] == "高"

    def test_score_never_exceeds_one_even_with_many_types(self):
        spans = [
            {"start": i, "end": i + 1, "type": t, "text": "x"}
            for i, t in enumerate(
                ["ADDRESS", "POSITION", "COMPANY", "ORGANIZATION", "GOVERNMENT", "SCENE"]
            )
        ]
        result = compute_combination_risk("民國70年次，女性。", spans)
        assert result["score"] <= 1.0


# ---------------------------------------------------------------------------
# AGE 正則抓取（四種格式：阿拉伯數字、民國年次、西元年生、中文數字）
# ---------------------------------------------------------------------------

class TestAgeExtraction:
    @pytest.mark.parametrize(
        "text",
        [
            "他今年35歲。",
            "民國78年次。",
            "1989年生。",
            "他今年三十五歲。",
        ],
    )
    def test_various_age_formats_detected(self, text):
        # 搭配另一個準識別子才能觸發非零分數，這裡只驗證 AGE 有被偵測到
        spans = [{"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"}]
        result = compute_combination_risk(text + "在測試公司上班。", spans)
        assert "AGE" in result["contributing_types"]

    def test_age_exclusion_suffix_not_matched(self):
        """「歲數」不該被當成年齡數字後綴誤判（negative lookahead 驗證）。"""
        spans = [{"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"}]
        result = compute_combination_risk("這個歲數的員工在測試公司上班。", spans)
        # 「這個」附近沒有真正的數字+歲格式，不該誤判出 AGE
        assert "AGE" not in result["contributing_types"]

    def test_chinese_number_twenty(self):
        spans = [{"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"}]
        result = compute_combination_risk("他二十歲，在測試公司上班。", spans)
        assert "AGE" in result["contributing_types"]

    def test_age_generalization_suggestion_bucket(self):
        spans = [{"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"},
                 {"start": 5, "end": 8, "type": "POSITION", "text": "工程師"}]
        result = compute_combination_risk("他32歲，在測試公司當工程師。", spans)
        suggestions_text = " ".join(result["suggestions"])
        assert "30-34歲" in suggestions_text


# ---------------------------------------------------------------------------
# risk_level 分級與 WARNING 門檻
# ---------------------------------------------------------------------------

class TestRiskLevelAndWarning:
    def test_low_risk_level_when_score_zero(self):
        result = compute_combination_risk("普通文字，沒有準識別子。")
        assert result["risk_level"] == "低"
        assert is_warning_worthy(result) is False

    def test_medium_risk_level(self):
        spans = [
            {"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"},
            {"start": 5, "end": 8, "type": "POSITION", "text": "工程師"},
        ]
        result = compute_combination_risk("測試公司的工程師。", spans)
        assert result["score"] == pytest.approx(0.35)
        assert result["risk_level"] == "中"
        assert is_warning_worthy(result) is False

    def test_high_risk_level_triggers_warning(self):
        spans = [
            {"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"},
            {"start": 5, "end": 8, "type": "POSITION", "text": "工程師"},
        ]
        result = compute_combination_risk("他35歲，在測試公司當工程師。", spans)
        assert result["score"] >= WARNING_THRESHOLD
        assert result["risk_level"] == "高"
        assert is_warning_worthy(result) is True


# ---------------------------------------------------------------------------
# 邊界輸入
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_text(self):
        result = compute_combination_risk("")
        assert result["score"] == 0.0
        assert result["contributing_types"] == []

    def test_spans_none(self):
        result = compute_combination_risk("35歲的人。", spans=None)
        # 沒有 spans 時只會用內部的 AGE/GENDER 偵測；單一 AGE 仍不構成組合
        assert result["score"] == 0.0

    def test_duplicate_spans_same_type_counted_once(self):
        spans = [
            {"start": 0, "end": 4, "type": "COMPANY", "text": "測試公司"},
            {"start": 10, "end": 14, "type": "COMPANY", "text": "測試公司"},
            {"start": 20, "end": 23, "type": "POSITION", "text": "工程師"},
        ]
        result = compute_combination_risk("重複出現測試公司兩次，工程師。", spans)
        assert result["contributing_types"] == ["COMPANY", "POSITION"]
        assert result["score"] == pytest.approx(0.35)  # 不因重複出現而加倍


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))