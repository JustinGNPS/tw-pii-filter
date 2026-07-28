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

    def test_overlapping_spans_are_all_preserved_with_source_marked(self):
        # "A123456789" 本身是合法身分證字號（checksum 正確），
        # 同時也是這個 email 的 local-part，兩個規則會產生重疊的 span，
        # detect_all 應該兩個都保留，不做去重或合併。
        text = "帳號 A123456789@example.com 請注意"
        result = detect_all(text)

        tw_id_spans = [s for s in result["spans"] if s["type"] == "TW_ID"]
        email_spans = [s for s in result["spans"] if s["type"] == "EMAIL"]

        self.assertEqual(len(tw_id_spans), 1)
        self.assertEqual(len(email_spans), 1)

        tw_id_span = tw_id_spans[0]
        email_span = email_spans[0]

        # 兩個 span 有重疊區間（TW_ID 落在 EMAIL 範圍內）
        self.assertLess(tw_id_span["start"], email_span["end"])
        self.assertLess(email_span["start"], tw_id_span["end"])

        self.assertEqual(text[tw_id_span["start"]:tw_id_span["end"]], "A123456789")
        self.assertEqual(
            text[email_span["start"]:email_span["end"]],
            "A123456789@example.com",
        )

        # source 欄位標記來源，供 Layer 4 之後判斷衝突處理
        self.assertEqual(tw_id_span["source"], "rule")
        self.assertEqual(email_span["source"], "rule")

    def test_no_match_in_plain_text(self):
        text = "這段文字沒有任何個資。"
        result = detect_all(text)

        self.assertEqual(result["spans"], [])

    def test_detect_all_result_matches_interface_schema_keys(self):
        text = "身分證 A123456789。"
        result = detect_all(text)

        self.assertEqual(set(result.keys()), {"text", "spans"})
        for span in result["spans"]:
            self.assertEqual(
                set(span.keys()),
                {"start", "end", "type", "text", "confidence", "source", "replacement"},
            )


if __name__ == "__main__":
    unittest.main()
