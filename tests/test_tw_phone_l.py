"""core.rules.tw_phone_l 的 unit test。

注意：以下所有市話號碼皆為合成假資料，並非任何真實公司或個人的電話號碼。
"""

import unittest

from core.rules.tw_phone_l import detect_tw_phone_l, is_valid_tw_phone_l


class IsValidTwPhoneLTests(unittest.TestCase):
    def test_valid_formats(self):
        valid_phones = [
            "(02)2311-3731",
            "02-23113731",
            "0223113731",
            "03-1234567",
            "049-2345678",
        ]
        for phone in valid_phones:
            with self.subTest(phone=phone):
                self.assertTrue(is_valid_tw_phone_l(phone))

    def test_invalid_formats(self):
        invalid_phones = [
            "",
            "02-23113",        # 太短（僅 7 碼）
            "02-231137312",    # 太長
            "0912-345-678",    # 手機號碼，非市話
            None,
            223113731,
        ]
        for phone in invalid_phones:
            with self.subTest(phone=phone):
                self.assertFalse(is_valid_tw_phone_l(phone))


class DetectTwPhoneLTests(unittest.TestCase):
    def test_detect_single_phone_with_parentheses(self):
        text = "公司電話 (02)2311-3731，歡迎來電。"
        result = detect_tw_phone_l(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "(02)2311-3731")
        self.assertEqual(span["type"], "TW_PHONE_L")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[TW_PHONE_L_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_phones_sequential_replacement(self):
        text = "台北 02-23113731，南投 049-2345678。"
        result = detect_tw_phone_l(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "02-23113731")
        self.assertEqual(result["spans"][0]["replacement"], "[TW_PHONE_L_1]")
        self.assertEqual(result["spans"][1]["text"], "049-2345678")
        self.assertEqual(result["spans"][1]["replacement"], "[TW_PHONE_L_2]")

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何市話號碼。"
        result = detect_tw_phone_l(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_mobile_number(self):
        text = "我的手機是 0912-345-678，不是市話。"
        result = detect_tw_phone_l(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
