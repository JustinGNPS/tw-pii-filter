"""core.rules.credit_card 的 unit test。

注意：以下所有卡號皆為手動依 Luhn 演算法建構的合成假資料，
並非任何真實信用卡卡號。
"""

import unittest

from core.rules.credit_card import detect_credit_card, is_valid_credit_card


class IsValidCreditCardTests(unittest.TestCase):
    """Luhn checksum 正確 / 錯誤的合成資料測試。"""

    def test_valid_16_digit_card(self):
        # 依 Luhn 演算法手動計算檢查碼建構，非真實卡號
        self.assertTrue(is_valid_credit_card("1234567812345670"))

    def test_valid_13_digit_card(self):
        # 依 Luhn 演算法手動計算檢查碼建構，非真實卡號
        self.assertTrue(is_valid_credit_card("1234567890128"))

    def test_valid_19_digit_card(self):
        self.assertTrue(is_valid_credit_card("1234567812345678905"))

    def test_valid_card_with_hyphens(self):
        self.assertTrue(is_valid_credit_card("1234-5678-1234-5670"))

    def test_valid_card_with_spaces(self):
        self.assertTrue(is_valid_credit_card("1234 5678 1234 5670"))

    def test_invalid_luhn_checksum(self):
        # 竄改 1234567812345670 的最後一碼，checksum 不成立
        self.assertFalse(is_valid_credit_card("1234567812345671"))

    def test_invalid_format(self):
        invalid_values = [
            "",
            "123456789012",     # 12 碼，太短
            "12345678901234567890",  # 20 碼，太長
            "1234567812345abc",      # 含非數字字元
            None,
            1234567812345670,
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                self.assertFalse(is_valid_credit_card(value))


class DetectCreditCardTests(unittest.TestCase):
    """detect_credit_card 的位置、格式與 replacement 編號測試。"""

    def test_detect_single_valid_card(self):
        text = "測試卡號：1234-5678-1234-5670，請勿外流。"
        result = detect_credit_card(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "1234-5678-1234-5670")
        self.assertEqual(span["type"], "CREDIT_CARD")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[CREDIT_CARD_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_cards_sequential_replacement(self):
        text = "卡號 1234567812345670 與 1234567812345678905。"
        result = detect_credit_card(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "1234567812345670")
        self.assertEqual(result["spans"][0]["replacement"], "[CREDIT_CARD_1]")
        self.assertEqual(result["spans"][1]["text"], "1234567812345678905")
        self.assertEqual(result["spans"][1]["replacement"], "[CREDIT_CARD_2]")

    def test_detect_skips_invalid_checksum(self):
        text = "這組看起來像卡號但 checksum 錯誤：1234567812345671"
        result = detect_credit_card(text)

        self.assertEqual(result["spans"], [])

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何信用卡號。"
        result = detect_credit_card(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
