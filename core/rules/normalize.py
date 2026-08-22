"""全形 ASCII 英數字正規化。"""

# 全形 ASCII 英數字→半形的對照表（U+FF10-FF19、U+FF21-FF3A、U+FF41-FF5A）。
# 特意只映射這三段，不做 NFKC 全量轉換：
# NFKC 會展開合字（ﬁ → fi）與相容字（㍿ → 株式会社），轉換前後長度改變，
# 導致後續的座標對應失效。全形 ASCII 英數字是 1:1 映射，長度不變，座標可直接沿用。
_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９"
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz",
)


def normalize_fullwidth(text: str) -> str:
    """全形 ASCII 英數字（U+FF10-FF19、U+FF21-FF3A、U+FF41-FF5A）正規化為半形。

    轉換前後字串長度不變，偵測到的 span 座標可以直接套用回原始文字。
    """
    return text.translate(_FULLWIDTH_TABLE)
