"""core.rules.tw_tax 的 unit test。

注意：以下所有統一編號皆為手動依 checksum 演算法建構的合成假資料，
並非任何真實公司的統一編號。
"""

import unittest

from core.rules.tw_tax import detect_tw_tax, is_valid_tw_tax


class IsValidTwTaxTests(unittest.TestCase):
    """checksum 正確 / 錯誤的合成資料測試（一般情況）。"""

    def test_valid_checksums(self):
        valid_ids = [
            "12345606",  # 一般情況，第 7 碼非 7
            "55555555",  # 另一組一般情況
        ]
        for tax_id in valid_ids:
            with self.subTest(tax_id=tax_id):
                self.assertTrue(is_valid_tw_tax(tax_id))

    def test_invalid_checksums(self):
        invalid_ids = [
            "12345600",  # 竄改 12345606 的最後一碼
            "23456789",  # 另一組錯誤數字（第 7 碼非 7，一般情況）
            "11111111",
        ]
        for tax_id in invalid_ids:
            with self.subTest(tax_id=tax_id):
                self.assertFalse(is_valid_tw_tax(tax_id))

    def test_invalid_format(self):
        invalid_formats = [
            "",
            "1234560",     # 少一碼
            "123456070",   # 多一碼
            "1234560A",    # 含非數字字元
            None,
            12345606,
        ]
        for value in invalid_formats:
            with self.subTest(value=value):
                self.assertFalse(is_valid_tw_tax(value))


class SeventhDigitSevenSpecialCaseTests(unittest.TestCase):
    """第 7 碼為 7 的特例：該位乘積可算 0 或 1，兩者之一符合即為有效。"""

    def test_valid_via_alt_zero(self):
        # base_sum = 5，取 alt=0 -> 5 % 5 == 0 成立；alt=1 -> 6 % 5 != 0
        tax_id = "10000074"
        self.assertEqual(tax_id[6], "7")
        self.assertTrue(is_valid_tw_tax(tax_id))

    def test_valid_via_alt_one(self):
        # base_sum = 4，取 alt=1 -> 5 % 5 == 0 成立；alt=0 -> 4 % 5 != 0
        tax_id = "10000073"
        self.assertEqual(tax_id[6], "7")
        self.assertTrue(is_valid_tw_tax(tax_id))

    def test_invalid_when_neither_alt_works(self):
        # base_sum = 1，alt=0 -> 1、alt=1 -> 2，皆非 5 的倍數
        tax_id = "10000070"
        self.assertEqual(tax_id[6], "7")
        self.assertFalse(is_valid_tw_tax(tax_id))


class DetectTwTaxTests(unittest.TestCase):
    """detect_tw_tax 的位置、格式與 replacement 編號測試。"""

    def test_detect_single_valid_id(self):
        text = "本公司統一編號為 12345606，請留存收據。"
        result = detect_tw_tax(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "12345606")
        self.assertEqual(span["type"], "TW_TAX")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[TW_TAX_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_valid_ids_sequential_replacement(self):
        text = "統編：12345606 與 10000074。"
        result = detect_tw_tax(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "12345606")
        self.assertEqual(result["spans"][0]["replacement"], "[TW_TAX_1]")
        self.assertEqual(result["spans"][1]["text"], "10000074")
        self.assertEqual(result["spans"][1]["replacement"], "[TW_TAX_2]")

    def test_detect_skips_invalid_checksum(self):
        text = "這組看起來像統編但檢查碼錯誤：12345600"
        result = detect_tw_tax(text)

        self.assertEqual(result["spans"], [])

    def test_detect_skips_seventh_digit_seven_invalid_case(self):
        text = "統編 10000070 checksum 不成立"
        result = detect_tw_tax(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_id_embedded_in_longer_digit_run(self):
        # 前後緊接其他數字時，不應被截斷誤判為統一編號
        text = "帳號 9123456060 不是統一編號"
        result = detect_tw_tax(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
