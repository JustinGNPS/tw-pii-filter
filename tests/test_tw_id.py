"""core.rules.tw_id 的 unit test。

注意：以下所有身分證字號皆為手動依 checksum 演算法建構的合成假資料，
並非任何真實個人的身分證字號。
"""

import unittest

from core.rules.tw_id import detect_tw_id, is_valid_tw_id


class IsValidTwIdTests(unittest.TestCase):
    """checksum 正確 / 錯誤的合成資料測試。"""

    def test_valid_checksums(self):
        valid_ids = [
            "A123456789",  # A -> 10, 教科書常見範例格式
            "B200000004",  # B -> 11
            "O111111116",  # O -> 35，測試特殊字母對照
        ]
        for id_str in valid_ids:
            with self.subTest(id_str=id_str):
                self.assertTrue(is_valid_tw_id(id_str))

    def test_valid_checksum_is_case_insensitive(self):
        self.assertTrue(is_valid_tw_id("a123456789"))

    def test_invalid_checksums(self):
        invalid_ids = [
            "A123456780",  # 竄改 A123456789 的檢查碼
            "B200000000",  # 竄改 B200000004 的檢查碼
            "B123456789",  # 沿用有效數字但換成錯誤的首字母
            "O111111110",  # 竄改 O111111116 的檢查碼
        ]
        for id_str in invalid_ids:
            with self.subTest(id_str=id_str):
                self.assertFalse(is_valid_tw_id(id_str))

    def test_invalid_format(self):
        invalid_formats = [
            "",
            "A12345678",     # 少一碼
            "A1234567890",   # 多一碼
            "112345678A",    # 字母位置錯誤
            "A12345678X",    # 最後一碼非數字
            None,
            12345,
        ]
        for value in invalid_formats:
            with self.subTest(value=value):
                self.assertFalse(is_valid_tw_id(value))


class DetectTwIdTests(unittest.TestCase):
    """detect_tw_id 的位置、格式與 replacement 編號測試。"""

    def test_detect_single_valid_id(self):
        text = "王先生的身分證字號是 A123456789，請妥善保管。"
        result = detect_tw_id(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "A123456789")
        self.assertEqual(span["type"], "TW_ID")
        self.assertEqual(span["text"], "A123456789")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[TW_ID_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_valid_ids_sequential_replacement(self):
        text = "身分證：A123456789 和 B200000004。"
        result = detect_tw_id(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "A123456789")
        self.assertEqual(result["spans"][0]["replacement"], "[TW_ID_1]")
        self.assertEqual(result["spans"][1]["text"], "B200000004")
        self.assertEqual(result["spans"][1]["replacement"], "[TW_ID_2]")

    def test_detect_skips_invalid_checksum(self):
        text = "這組看起來像身分證但檢查碼錯誤：A123456780"
        result = detect_tw_id(text)

        self.assertEqual(result["spans"], [])

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何身分證字號。"
        result = detect_tw_id(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_id_embedded_in_longer_alphanumeric(self):
        # 若前後緊接其他英數字，不應被截斷誤判為身分證字號
        text = "序號 XA123456789Y 不是身分證字號"
        result = detect_tw_id(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
