"""core.rules.tw_nhi 的 unit test。

注意：以下所有健保卡號皆為合成假資料，並非任何真實個人的健保卡號。
健保卡號無公開 checksum 演算法，本規則僅驗證格式（12 碼數字）。
"""

import unittest

from core.rules.tw_nhi import detect_tw_nhi, is_valid_tw_nhi


class IsValidTwNhiTests(unittest.TestCase):
    def test_valid_format(self):
        valid_ids = [
            "123456789012",
            "000000000000",
        ]
        for nhi in valid_ids:
            with self.subTest(nhi=nhi):
                self.assertTrue(is_valid_tw_nhi(nhi))

    def test_invalid_format(self):
        invalid_ids = [
            "",
            "12345678901",     # 少一碼
            "1234567890123",   # 多一碼
            "12345678901A",    # 含非數字字元
            None,
            123456789012,
        ]
        for nhi in invalid_ids:
            with self.subTest(nhi=nhi):
                self.assertFalse(is_valid_tw_nhi(nhi))


class DetectTwNhiTests(unittest.TestCase):
    def test_detect_single_id(self):
        text = "健保卡號 123456789012，請妥善保管。"
        result = detect_tw_nhi(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "123456789012")
        self.assertEqual(span["type"], "TW_NHI")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[TW_NHI_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_ids_sequential_replacement(self):
        text = "健保卡：123456789012 與 000000000000。"
        result = detect_tw_nhi(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "123456789012")
        self.assertEqual(result["spans"][0]["replacement"], "[TW_NHI_1]")
        self.assertEqual(result["spans"][1]["text"], "000000000000")
        self.assertEqual(result["spans"][1]["replacement"], "[TW_NHI_2]")

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何健保卡號。"
        result = detect_tw_nhi(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_id_embedded_in_longer_digit_run(self):
        text = "帳號 9123456789012 不是健保卡號"
        result = detect_tw_nhi(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
