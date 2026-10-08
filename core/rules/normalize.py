"""全形 ASCII 字元正規化。"""

# 全形 ASCII 字元→半形的對照表。
#
# 涵蓋整個全形 ASCII 區段 U+FF01-FF5E（對半形 U+0021-U+007E 是固定 offset
# 0xFEE0），外加全形空格 U+3000。
#
# 一開始只映射了英數字三段（U+FF10-FF19、U+FF21-FF3A、U+FF41-FF5A），
# 但實測發現**全形符號一樣會讓偵測整個失效**，而且都是真實會出現的寫法：
#
#     '信箱 ａｂｃ＠ｅｘａｍｐｌｅ．ｃｏｍ'   -> []   全形 ＠ 與 ．
#     '手機 ０９１２－３４５－６７８'        -> []   全形連字號
#     '市話 （０２）２３４５６７８９'         -> []   全形括號
#
# 這些字元在正則裡是字面值（EMAIL 的 @ 與 .、TW_PHONE_M 的 -、TW_PHONE_L 的
# 括號），全形版本一律對不到。既然整段都是 1:1 等長對應，就沒有理由只映射一部分。
#
# 特意不做 NFKC 全量轉換：
# NFKC 會展開合字（ﬁ → fi）與相容字（㍿ → 株式会社），轉換前後長度改變，
# 導致後續的座標對應失效。全形 ASCII 是 1:1 映射，長度不變，座標可直接沿用。
_FULLWIDTH_TABLE = {
    code: code - 0xFEE0 for code in range(0xFF01, 0xFF5E + 1)
}
_FULLWIDTH_TABLE[0x3000] = 0x20  # 全形空格


def normalize_fullwidth(text: str) -> str:
    """全形 ASCII 字元（U+FF01-FF5E）與全形空格（U+3000）正規化為半形。

    轉換前後字串長度不變，偵測到的 span 座標可以直接套用回原始文字。

    TypeScript 版對應實作見 ``extension/src/core/normalize.ts``，兩版行為必須一致。
    """
    return text.translate(_FULLWIDTH_TABLE)
