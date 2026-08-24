"""全形 → 半形正規化（`core/rules/normalize.py`）與其對偵測結果的影響。

對應 issue #21（TypeScript 版抓不到全形統編/身分證）與 #27（規則層對全形
手機與信箱完全偵測不到）。TypeScript 版對應測試見
`extension/tests/known_divergence.test.ts`，兩版行為必須一致。
"""

import unittest

from core.rules import detect_all
from core.rules.normalize import has_full_width, to_half_width


class ToHalfWidthTests(unittest.TestCase):
    """正規化本身。最重要的性質是**字元數不變**——座標靠這個才能沿用到原文。"""

    def test_全形數字轉半形(self):
        self.assertEqual(to_half_width("０１２３４５６７８９"), "0123456789")

    def test_全形英文字母轉半形(self):
        self.assertEqual(to_half_width("ＡＢＣａｂｃ"), "ABCabc")

    def test_全形常見符號轉半形(self):
        self.assertEqual(to_half_width("＠．－＿"), "@.-_")

    def test_全形空格轉半形空格(self):
        self.assertEqual(to_half_width("Ａ　Ｂ"), "A B")

    def test_中文與半形字元不受影響(self):
        text = "王小明 A123456789 的資料"
        self.assertEqual(to_half_width(text), text)

    def test_字元數一律不變(self):
        """這是整個設計的關鍵前提：長度一變，後面 span 的座標就對不回原文。"""
        for text in [
            "０９１２３４５６７８",
            "ａｂｃ@ｅｘａｍｐｌｅ.ｃｏｍ",
            "混合 Ａ１２３ 與 A123 和中文",
            "　全形空格開頭",
        ]:
            with self.subTest(text=text):
                self.assertEqual(len(to_half_width(text)), len(text))

    def test_不做_NFKC_那類會改變長度的正規化(self):
        """NFKC 會把 ㍿ 展開成「株式会社」、ﬁ 展開成 fi，長度改變會讓座標錯位。"""
        for text in ["㍿", "ﬁle"]:
            with self.subTest(text=text):
                self.assertEqual(to_half_width(text), text)
                self.assertEqual(len(to_half_width(text)), len(text))

    def test_has_full_width(self):
        self.assertTrue(has_full_width("統編 １２３４５６７５"))
        self.assertTrue(has_full_width("Ａ"))
        self.assertTrue(has_full_width("有　全形空格"))
        self.assertFalse(has_full_width("統編 12345675"))
        self.assertFalse(has_full_width("純中文沒有全形英數"))


class FullWidthDetectionTests(unittest.TestCase):
    """正規化之後，四種原本抓不到的全形寫法都要抓得到（issue #21 / #27）。"""

    CASES = [
        ("全形統編", "統編 １２３４５６７５", "TW_TAX"),
        ("全形身分證", "身分證 Ａ１２３４５６７８９", "TW_ID"),
        ("全形手機", "手機 ０９１２３４５６７８", "TW_PHONE_M"),
        ("全形信箱", "信箱 ａｂｃ@ｅｘａｍｐｌｅ.ｃｏｍ", "EMAIL"),
    ]

    def test_全形寫法都偵測得到(self):
        for label, text, expected_type in self.CASES:
            with self.subTest(label):
                types = [s["type"] for s in detect_all(text)["spans"]]
                self.assertIn(expected_type, types)

    def test_span_的_text_取自原文而非正規化後的版本(self):
        """使用者該看到自己打的全形原文，不是被我們改寫過的半形版本。"""
        text = "統編 １２３４５６７５"
        spans = detect_all(text)["spans"]
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["text"], "１２３４５６７５")

    def test_座標仍符合介面約定(self):
        """docs/interface.md：text[start:end] 必須等於 span["text"]。"""
        for label, text, _ in self.CASES:
            with self.subTest(label):
                for span in detect_all(text)["spans"]:
                    self.assertEqual(text[span["start"]:span["end"]], span["text"])

    def test_半形行為完全不受影響(self):
        text = "統編 12345675 手機 0912345678"
        types = sorted(s["type"] for s in detect_all(text)["spans"])
        self.assertEqual(types, ["TW_PHONE_M", "TW_TAX"])

    def test_全形與半形混排時兩者都抓得到且座標各自正確(self):
        text = "全形 １２３４５６７５ 半形 12345675"
        spans = detect_all(text)["spans"]
        self.assertEqual([s["text"] for s in spans], ["１２３４５６７５", "12345675"])
        for span in spans:
            self.assertEqual(text[span["start"]:span["end"]], span["text"])

    def test_checksum_錯誤的全形統編一樣不該被抓(self):
        """正規化只是讓正則對得到，checksum 驗證仍然要照常生效。"""
        # 12345672 的檢查碼是錯的（12345670/1/5/6 反而是有效的，已實測確認）
        self.assertEqual(detect_all("統編 １２３４５６７２")["spans"], [])


if __name__ == "__main__":
    unittest.main()
