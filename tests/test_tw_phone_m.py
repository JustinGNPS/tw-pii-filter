"""core.rules.tw_phone_m 的 unit test。

注意：以下所有手機號碼皆為合成假資料，並非任何真實個人的手機號碼。
"""

import unittest

from core.rules.tw_phone_m import detect_tw_phone_m, is_valid_tw_phone_m


class IsValidTwPhoneMTests(unittest.TestCase):
    def test_valid_formats(self):
        valid_phones = [
            "0912345678",      # 無連字號
            "0912-345678",     # 單一連字號
            "0912-345-678",    # 兩個連字號
        ]
        for phone in valid_phones:
            with self.subTest(phone=phone):
                self.assertTrue(is_valid_tw_phone_m(phone))

    def test_invalid_formats(self):
        invalid_phones = [
            "",
            "091234567",       # 少一碼
            "09123456789",     # 多一碼
            "0812345678",      # 非 09 開頭
            None,
            912345678,
        ]
        for phone in invalid_phones:
            with self.subTest(phone=phone):
                self.assertFalse(is_valid_tw_phone_m(phone))

    def test_hyphen_position_does_not_affect_validity(self):
        # is_valid 只看移除連字號後的數字內容，不限制連字號位置
        self.assertTrue(is_valid_tw_phone_m("0912-3456-78"))


class DetectTwPhoneMTests(unittest.TestCase):
    def test_detect_single_phone(self):
        text = "請撥打我的手機 0912-345-678 聯絡我。"
        result = detect_tw_phone_m(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "0912-345-678")
        self.assertEqual(span["type"], "TW_PHONE_M")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[TW_PHONE_M_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_phones_sequential_replacement(self):
        text = "聯絡電話：0912345678 或 0987-654-321。"
        result = detect_tw_phone_m(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "0912345678")
        self.assertEqual(result["spans"][0]["replacement"], "[TW_PHONE_M_1]")
        self.assertEqual(result["spans"][1]["text"], "0987-654-321")
        self.assertEqual(result["spans"][1]["replacement"], "[TW_PHONE_M_2]")

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何手機號碼。"
        result = detect_tw_phone_m(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_number_embedded_in_longer_digit_run(self):
        text = "訂單編號 909123456789 不是手機號碼"
        result = detect_tw_phone_m(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_landline_number(self):
        text = "公司電話 02-23113731 不是手機號碼"
        result = detect_tw_phone_m(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
