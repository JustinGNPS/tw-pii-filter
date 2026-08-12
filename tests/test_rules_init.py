"""core.rules（__init__.py 匯出項與 detect_all）的 unit test。

注意：以下所有身分證字號、統編、電話、email、卡號、金鑰皆為合成假資料，
並非任何真實個人或組織的資料。
"""

import unittest

import core.rules as rules
from core.rules import detect_all


class ExportsTests(unittest.TestCase):
    """確認所有 detect_*／is_valid_* 函式都已從套件層級匯出。"""

    def test_all_detect_functions_exported(self):
        expected_names = [
            "detect_tw_id",
            "detect_tw_tax",
            "detect_tw_nhi",
            "detect_tw_phone_m",
            "detect_tw_phone_l",
            "detect_email",
            "detect_credit_card",
            "detect_api_key",
            "detect_all",
        ]
        for name in expected_names:
            with self.subTest(name=name):
                self.assertTrue(hasattr(rules, name))
                self.assertTrue(callable(getattr(rules, name)))

    def test_all_is_valid_functions_exported(self):
        expected_names = [
            "is_valid_tw_id",
            "is_valid_tw_tax",
            "is_valid_tw_nhi",
            "is_valid_tw_phone_m",
            "is_valid_tw_phone_l",
            "is_valid_email",
            "is_valid_credit_card",
            "is_valid_api_key",
        ]
        for name in expected_names:
            with self.subTest(name=name):
                self.assertTrue(hasattr(rules, name))
                self.assertTrue(callable(getattr(rules, name)))


class DetectAllTests(unittest.TestCase):
    """detect_all 合併多規則偵測結果的行為測試。"""

    def test_detects_multiple_types_in_one_pass(self):
        text = (
            "王小明身分證 A123456789，統編 12345606，"
            "手機 0912-345-678，市話 (02)2311-3731，"
            "信箱 test@example.com，健保卡 123456789012，"
            "卡號 1234567812345670，金鑰 sk-abcd1234EFGH5678ijkl。"
        )
        result = detect_all(text)

        self.assertEqual(result["text"], text)

        types_found = {span["type"] for span in result["spans"]}
        self.assertEqual(
            types_found,
            {
                "TW_ID",
                "TW_TAX",
                "TW_PHONE_M",
                "TW_PHONE_L",
                "EMAIL",
                "TW_NHI",
                "CREDIT_CARD",
                "API_KEY",
            },
        )

    def test_span_count_matches_sum_of_individual_detectors(self):
        text = (
            "身分證 A123456789 與 B200000004，"
            "信箱 alice@example.com 與 bob@example.org。"
        )
        result = detect_all(text)

        expected_count = (
            len(rules.detect_tw_id(text)["spans"])
            + len(rules.detect_email(text)["spans"])
        )
        self.assertEqual(len(result["spans"]), expected_count)
        self.assertEqual(expected_count, 4)

    def test_spans_sorted_by_start_position(self):
        text = "信箱 test@example.com，身分證 A123456789。"
        result = detect_all(text)

        starts = [span["start"] for span in result["spans"]]
        self.assertEqual(starts, sorted(starts))

    def test_overlapping_spans_are_resolved_by_layer4_keeping_larger_range(self):
        # "A123456789" 本身是合法身分證字號（checksum 正確），
        # 同時也是這個 email 的 local-part，TW_ID 與 EMAIL 兩個規則會
        # 產生重疊的 span；經 Layer 4 解析後，範圍較大的 EMAIL 應該保留，
        # 範圍較小、被包含在內的 TW_ID 應該被移除。
        text = "帳號 A123456789@example.com 請注意"
        result = detect_all(text)

        types_found = [span["type"] for span in result["spans"]]
        self.assertEqual(types_found, ["EMAIL"])

        email_span = result["spans"][0]
        self.assertEqual(
            text[email_span["start"]:email_span["end"]],
            "A123456789@example.com",
        )

    def test_layer4_resolves_b_example_email_wins_over_embedded_phone(self):
        # B 提出的例子：手機號碼剛好是 email local-part 的一部分，
        # 範圍較大的 EMAIL 應該保留，藏在裡面的 TW_PHONE_M 應該被移除。
        text = "聯絡我 a0912345678@gmail.com"
        result = detect_all(text)

        types_found = [span["type"] for span in result["spans"]]
        self.assertEqual(types_found, ["EMAIL"])

        email_span = result["spans"][0]
        self.assertEqual(
            text[email_span["start"]:email_span["end"]],
            "a0912345678@gmail.com",
        )

        # 輸出的 spans 必須互不重疊，下游才能安全地依座標替換文字
        spans = result["spans"]
        for i in range(len(spans) - 1):
            self.assertLessEqual(spans[i]["end"], spans[i + 1]["start"])

    def test_layer4_renumbers_replacements_after_removing_a_conflicting_span(self):
        # 「A123456789」在獨立呼叫 detect_tw_id 時會編號 TW_ID_1，
        # 「B200000004」會編號 TW_ID_2；但 A123456789 其實是前面 email 的
        # local-part，仲裁後會被 EMAIL 蓋過而移除，只剩 B200000004 這個 TW_ID。
        # 若不重新編號，B200000004 會停留在過時的 [TW_ID_2]（沒有 TW_ID_1），
        # 出現跳號；detect_all 應該把它重新編為 [TW_ID_1]。
        text = "A123456789@example.com 另外 B200000004"

        raw_tw_id_spans = rules.detect_tw_id(text)["spans"]
        self.assertEqual(
            [s["replacement"] for s in raw_tw_id_spans],
            ["[TW_ID_1]", "[TW_ID_2]"],
        )

        result = detect_all(text)

        tw_id_spans = [s for s in result["spans"] if s["type"] == "TW_ID"]
        email_spans = [s for s in result["spans"] if s["type"] == "EMAIL"]

        self.assertEqual(len(tw_id_spans), 1)
        self.assertEqual(text[tw_id_spans[0]["start"]:tw_id_spans[0]["end"]], "B200000004")
        self.assertEqual(tw_id_spans[0]["replacement"], "[TW_ID_1]")

        self.assertEqual(len(email_spans), 1)
        self.assertEqual(email_spans[0]["replacement"], "[EMAIL_1]")

    def test_no_match_in_plain_text(self):
        text = "這段文字沒有任何個資。"
        result = detect_all(text)

        self.assertEqual(result["spans"], [])

    def test_detect_all_result_matches_interface_schema_keys(self):
        text = "身分證 A123456789。"
        result = detect_all(text)

        self.assertEqual(set(result.keys()), {"text", "spans", "combination_risk"})
        for span in result["spans"]:
            self.assertEqual(
                set(span.keys()),
                {"start", "end", "type", "text", "confidence", "source", "replacement"},
            )

    def test_combination_risk_is_none_without_enough_quasi_identifiers(self):
        # 只有 TW_ID 這種規則層型別，不是準識別子，共現數 < 2。
        text = "身分證 A123456789。"
        result = detect_all(text)

        self.assertIsNone(result["combination_risk"])


class DetectAllExtraSpansIntegrationTests(unittest.TestCase):
    """detect_all(text, extra_spans=...) 整合語意層（source="model"）spans 的行為測試。"""

    def _model_span(self, start, end, text, type_="PERSON_NAME", confidence=0.8):
        return {
            "start": start,
            "end": end,
            "type": type_,
            "text": text,
            "confidence": confidence,
            "source": "model",
            "replacement": f"[{type_}_1]",
        }

    def test_non_conflicting_model_span_is_merged_in(self):
        text = "王小明 身分證 A123456789"
        model_span = self._model_span(0, 3, "王小明")

        result = detect_all(text, extra_spans=[model_span])

        types_found = {span["type"] for span in result["spans"]}
        self.assertEqual(types_found, {"PERSON_NAME", "TW_ID"})

        starts = [span["start"] for span in result["spans"]]
        self.assertEqual(starts, sorted(starts))

    def test_rule_wins_over_model_when_range_and_confidence_tie(self):
        # 語意層與規則層對同一段文字判斷出完全相同的範圍與 confidence，
        # 依仲裁順序第三條，rule 應該勝出。
        text = "身分證 A123456789"
        rule_span = rules.detect_tw_id(text)["spans"][0]
        model_span = self._model_span(
            rule_span["start"], rule_span["end"], rule_span["text"],
            type_="TW_ID", confidence=rule_span["confidence"],
        )

        result = detect_all(text, extra_spans=[model_span])

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["source"], "rule")

    def test_larger_model_span_wins_over_smaller_rule_span(self):
        # 範圍大者優先於 source 優先權：即使是 model 來源，
        # 只要範圍比 rule span 大，仍然勝出。
        text = "身分證 A123456789"
        rule_span = rules.detect_tw_id(text)["spans"][0]
        larger_model_span = self._model_span(
            rule_span["start"] - 4, rule_span["end"], "身分證 A123456789",
            type_="SENSITIVE_BLOCK", confidence=0.5,
        )

        result = detect_all(text, extra_spans=[larger_model_span])

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["type"], "SENSITIVE_BLOCK")
        self.assertEqual(result["spans"][0]["source"], "model")

    def test_extra_spans_defaults_to_none_without_error(self):
        text = "身分證 A123456789"
        result = detect_all(text)

        self.assertEqual(len(result["spans"]), 1)


class CombinationRiskIntegrationTests(unittest.TestCase):
    """detect_all() 頂層 combination_risk 欄位（Layer 3，issue #20）的整合測試。

    ADDRESS/POSITION 這類準識別子來自語意層（NER），detect_all() 本身不會
    偵測，因此這裡透過 extra_spans 模擬 NER 已經產生的結果 —— 跟 proxy 端
    `detector._extra_spans()` 實際接線時的資料形狀一致。AGE 則是
    compute_combination_risk() 自己對原文做正則掃描，不需要透過 spans 帶入。
    """

    def _model_span(self, start, end, text, type_, confidence=0.85):
        return {
            "start": start,
            "end": end,
            "type": type_,
            "text": text,
            "confidence": confidence,
            "source": "model",
            "replacement": f"[{type_}_1]",
        }

    def test_age_address_position_combination_produces_high_risk(self):
        # 對應報告 4.3 節的經典例子：沒有直接 PII 字串，
        # 但「年齡 + 地區 + 職稱」組合起來能定位到特定人。
        text = "他32歲，住在新竹，是一名工程師。"
        address_span = self._model_span(
            text.index("新竹"), text.index("新竹") + 2, "新竹", "ADDRESS",
        )
        position_span = self._model_span(
            text.index("工程師"), text.index("工程師") + 3, "工程師", "POSITION",
        )

        result = detect_all(text, extra_spans=[address_span, position_span])

        risk = result["combination_risk"]
        self.assertIsNotNone(risk)
        self.assertEqual(risk["contributing_types"], ["ADDRESS", "AGE", "POSITION"])
        self.assertAlmostEqual(risk["score"], 0.85)  # 0.30 + 0.35 + 0.20
        self.assertEqual(risk["risk_level"], "高")
        self.assertTrue(risk["suggestions"])  # 三個型別都該有對應的泛化建議

    def test_single_quasi_identifier_alone_is_not_enough_for_risk(self):
        # 只有一個準識別子（POSITION）不構成組合風險，score 應為 0 -> None。
        text = "他是一名工程師。"
        position_span = self._model_span(
            text.index("工程師"), text.index("工程師") + 3, "工程師", "POSITION",
        )

        result = detect_all(text, extra_spans=[position_span])

        self.assertIsNone(result["combination_risk"])

    def test_combination_risk_uses_layer4_resolved_spans_not_raw_extra_spans(self):
        # combination_risk 的計算依據應該是仲裁後的 spans（result["spans"]），
        # 不是呼叫端傳進來的原始 extra_spans —— 這裡讓 POSITION 與規則層的
        # TW_ID 完全重疊、範圍相同、confidence 也相同，仲裁後 model 版 POSITION
        # 會被 rule 版 TW_ID 蓋掉（見 interface.md 仲裁順序第三條），
        # 因此不該再被算進 contributing_types。
        text = "A123456789"
        rule_span = detect_all(text)["spans"][0]
        fake_position_same_range = self._model_span(
            rule_span["start"], rule_span["end"], rule_span["text"], "POSITION",
            confidence=rule_span["confidence"],
        )

        result = detect_all(text, extra_spans=[fake_position_same_range])

        types_found = {span["type"] for span in result["spans"]}
        self.assertEqual(types_found, {"TW_ID"})  # POSITION 被仲裁掉了
        self.assertIsNone(result["combination_risk"])


if __name__ == "__main__":
    unittest.main()
