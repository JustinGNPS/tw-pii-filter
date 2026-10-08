r"""全形 ASCII 英數字正規化（core/rules/normalize.py）與 detect_all 整合測試。

## 這組測試在守什麼

規則層的 \d / [A-Za-z] 只比對 ASCII，全形英數字（U+FF10-FF19、U+FF21-FF3A、
U+FF41-FF5A）在輸入法環境下自然出現（從 Word/PDF/表單複製貼上），但原本
整個規則層都看不到。

修法是在 detect_all() 裡對正規化後的文字跑規則，座標不需要修正
（全形 ASCII 是 1:1 等長對應），span 的 text 欄位再換回原始全形字元。

注意：以下所有身分證字號、統編、電話、email 皆為合成假資料，
並非任何真實個人或組織的資料。
"""

import unittest

from core.rules import detect_all, normalize_fullwidth


class NormalizeFullwidthTests(unittest.TestCase):
    """normalize_fullwidth 本身的行為。"""

    def test_全形數字轉半形(self):
        self.assertEqual(normalize_fullwidth("０１２３４５６７８９"), "0123456789")

    def test_全形大寫轉半形(self):
        self.assertEqual(normalize_fullwidth("ＡＢＣＺ"), "ABCZ")

    def test_全形小寫轉半形(self):
        self.assertEqual(normalize_fullwidth("ａｂｃｚ"), "abcz")

    def test_中文字元不受影響(self):
        original = "台北市中山區"
        self.assertEqual(normalize_fullwidth(original), original)

    def test_NFKC相容字元不受影響(self):
        # NFKC 會展開這些，但 normalize_fullwidth 不應該動它們
        self.assertEqual(normalize_fullwidth("㍿"), "㍿")
        self.assertEqual(normalize_fullwidth("ﬁle"), "ﬁle")

    def test_轉換前後長度不變(self):
        original = "手機０９１２３４５６７８身分證Ａ１２３４５６７８９"
        normalized = normalize_fullwidth(original)
        self.assertEqual(len(normalized), len(original))

    def test_混合半形全形_只動全形的部分(self):
        self.assertEqual(normalize_fullwidth("abc０９１ＡＢ"), "abc091AB")


class DetectAllFullwidthTests(unittest.TestCase):
    """detect_all 能正確偵測全形英數字寫成的個資。"""

    # -------------------------------------------------- 各型別

    def test_全形手機號碼被偵測到(self):
        text = "手機：０９１２３４５６７８"
        result = detect_all(text)

        spans = [s for s in result["spans"] if s["type"] == "TW_PHONE_M"]
        self.assertEqual(len(spans), 1)

    def test_全形信箱被偵測到(self):
        text = "信箱：ｔｅｓｔ@ｅｘａｍｐｌｅ.ｃｏｍ"
        result = detect_all(text)

        spans = [s for s in result["spans"] if s["type"] == "EMAIL"]
        self.assertEqual(len(spans), 1)

    def test_全形身分證被偵測到(self):
        # Ａ１２３４５６７８９ 正規化後為 A123456789，checksum 正確
        text = "身分證：Ａ１２３４５６７８９"
        result = detect_all(text)

        spans = [s for s in result["spans"] if s["type"] == "TW_ID"]
        self.assertEqual(len(spans), 1)

    def test_全形統編被偵測到(self):
        # １２３４５６０６ 正規化後為 12345606，checksum 正確
        text = "統編：１２３４５６０６"
        result = detect_all(text)

        spans = [s for s in result["spans"] if s["type"] == "TW_TAX"]
        self.assertEqual(len(spans), 1)

    def test_全形健保卡號被偵測到(self):
        text = "健保：１２３４５６７８９０１２"
        result = detect_all(text)

        spans = [s for s in result["spans"] if s["type"] == "TW_NHI"]
        self.assertEqual(len(spans), 1)

    # -------------------------------------------------- 座標與 text 欄位

    def test_座標對回原始全形文字(self):
        text = "手機：０９１２３４５６７８"
        result = detect_all(text)

        span = next(s for s in result["spans"] if s["type"] == "TW_PHONE_M")
        self.assertEqual(text[span["start"]:span["end"]], "０９１２３４５６７８")

    def test_span_text_欄位是原始全形字元(self):
        text = "身分證：Ａ１２３４５６７８９"
        result = detect_all(text)

        span = next(s for s in result["spans"] if s["type"] == "TW_ID")
        # span["text"] 必須是原始全形字元，不能是正規化後的半形版本
        self.assertEqual(span["text"], "Ａ１２３４５６７８９")
        self.assertNotEqual(span["text"], "A123456789")

    def test_span_text_與座標一致(self):
        text = "信箱：ｔｅｓｔ@ｅｘａｍｐｌｅ.ｃｏｍ"
        result = detect_all(text)

        span = next(s for s in result["spans"] if s["type"] == "EMAIL")
        self.assertEqual(span["text"], text[span["start"]:span["end"]])

    # -------------------------------------------------- 全形偵測不到的情況不應誤報

    def test_全形數字checksum不對不報(self):
        # Ａ１２３４５６７８０ → A123456780，檢核碼錯誤
        text = "身分證：Ａ１２３４５６７８０"
        result = detect_all(text)

        spans = [s for s in result["spans"] if s["type"] == "TW_ID"]
        self.assertEqual(len(spans), 0)

    # -------------------------------------------------- 混合場景

    def test_同一段文字半形全形都被偵測到(self):
        text = "手機 0912345678 或 ０９１２３４５６７８"
        result = detect_all(text)

        phone_spans = [s for s in result["spans"] if s["type"] == "TW_PHONE_M"]
        self.assertEqual(len(phone_spans), 2)

    def test_全形個資不影響旁邊的中文座標(self):
        # 確認正規化後座標仍然正確對應到原文各個位置
        text = "聯絡人王小明，手機 ０９１２３４５６７８，地址台北"
        result = detect_all(text)

        span = next(s for s in result["spans"] if s["type"] == "TW_PHONE_M")
        self.assertEqual(text[span["start"]:span["end"]], "０９１２３４５６７８")

    def test_detect_all回傳的text欄位仍是原始文字(self):
        text = "手機：０９１２３４５６７８"
        result = detect_all(text)

        self.assertEqual(result["text"], text)



class FullwidthSymbolTests(unittest.TestCase):
    """全形**符號**（不只英數字）同樣會讓偵測失效。

    對照表原本只涵蓋英數字三段（U+FF10-FF19 / FF21-FF3A / FF41-FF5A），
    但正則裡有不少字面符號——EMAIL 的 @ 與 .、TW_PHONE_M 的 -、
    TW_PHONE_L 的括號——全形版本一律對不到，整筆偵測直接落空：

        '信箱 ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ'  -> []
        '手機 ０９１２－３４５－６７８'       -> []
        '市話 （０２）２３４５６７８９'        -> []

    既然整個 U+FF01-FF5E 區段都是 1:1 等長對應，沒有理由只映射一部分。

    注意：以下皆為合成假資料。
    """

    def test_全形符號轉半形(self):
        self.assertEqual(normalize_fullwidth("＠．－（）＿％＋"), "@.-()_%+")

    def test_全形空格轉半形空格(self):
        self.assertEqual(normalize_fullwidth("Ａ　Ｂ"), "A B")

    def test_全形符號的信箱偵測得到(self):
        text = "信箱 ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ"
        spans = detect_all(text)["spans"]
        self.assertEqual([s["type"] for s in spans], ["EMAIL"])
        self.assertEqual(spans[0]["text"], "ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ")

    def test_全形連字號的手機偵測得到(self):
        spans = detect_all("手機 ０９１２－３４５－６７８")["spans"]
        self.assertEqual([s["type"] for s in spans], ["TW_PHONE_M"])

    def test_全形括號的市話偵測得到(self):
        spans = detect_all("市話 （０２）２３４５６７８９")["spans"]
        self.assertEqual([s["type"] for s in spans], ["TW_PHONE_L"])

    def test_全形符號情境下座標仍符合介面約定(self):
        for text in [
            "信箱 ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ",
            "手機 ０９１２－３４５－６７８",
            "市話 （０２）２３４５６７８９",
        ]:
            with self.subTest(text=text):
                for span in detect_all(text)["spans"]:
                    self.assertEqual(text[span["start"]:span["end"]], span["text"])

    def test_checksum_仍照常生效(self):
        """正規化只是讓正則對得到，不該讓檢查碼錯誤的號碼變成有效。"""
        # 12345672 檢查碼錯誤（12345670/1/5/6 才是有效的，已實測確認）
        self.assertEqual(detect_all("統編 １２３４５６７２")["spans"], [])

if __name__ == "__main__":
    unittest.main()
