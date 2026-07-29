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
