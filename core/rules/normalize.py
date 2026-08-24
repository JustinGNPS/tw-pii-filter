"""全形 → 半形正規化（偵測前置處理）。

## 為什麼需要

全形英數在中文輸入環境是**很自然會出現**的寫法——注音輸入法的全形模式、
從 Word / PDF / 網頁表單複製貼上的內容都可能帶全形英數。但規則層的正則
用的是 ``[0-9]`` / ``[A-Za-z]``，對不到 U+FF10–FF19 / U+FF21–FF5A。

實測（issue #21、#27）：

    '我的手機是0912345678'            -> [('TW_PHONE_M', '0912345678')]
    '我的手機是０９１２３４５６７８'    -> []                              ❌

後果是使用者貼一份含全形統編的合約，面板顯示「未偵測到敏感資訊」，
個資原封不動送出去——**使用者以為已遮蔽、實際沒有**，比不做更危險。

## 為什麼是定點映射，不是 NFKC

``unicodedata.normalize('NFKC', text)`` 看起來更省事，但**不保證長度不變**：

    '０９１２３４５６７８'  10 -> '0912345678'      10   等長 ✅
    '㍿'                    1 -> '株式会社'         4   長度改變 ⚠️
    'ﬁle'                   3 -> 'file'            4   長度改變 ⚠️

長度一變，後面所有 span 的 start/end 就對不回原文，而 B 的遮蔽是
「從後往前依座標替換」，座標錯位會直接把文字切壞。

因此這裡只對**全形英數與全形空格**做定點映射。這個範圍是嚴格 1:1、
字元數保證不變，所以正規化後的座標可以直接沿用到原文，不需要維護對應表。

TypeScript 版對應實作見 ``extension/src/core/normalize.ts``，兩版行為必須一致。
"""

# 全形英數與符號（U+FF01–U+FF5E）對半形（U+0021–U+007E）是固定 offset 0xFEE0。
# 這一段完整涵蓋全形的「！」到「～」，含數字、大小寫英文與常見符號（＠、．、－）。
_FULLWIDTH_START = 0xFF01
_FULLWIDTH_END = 0xFF5E
_FULLWIDTH_OFFSET = 0xFEE0

# 全形空格（表意空格）
_IDEOGRAPHIC_SPACE = "　"

_TRANSLATION_TABLE = {
    code: code - _FULLWIDTH_OFFSET
    for code in range(_FULLWIDTH_START, _FULLWIDTH_END + 1)
}
_TRANSLATION_TABLE[ord(_IDEOGRAPHIC_SPACE)] = ord(" ")


def to_half_width(text: str) -> str:
    """把全形英數/符號轉成半形，**保證字元數不變**。

    不做其他 Unicode 正規化——見模組說明關於 NFKC 的部分。
    """
    return text.translate(_TRANSLATION_TABLE)


def has_full_width(text: str) -> bool:
    """這段文字含有需要正規化的全形字元嗎（用來略過不必要的處理）。"""
    return any(
        _FULLWIDTH_START <= ord(char) <= _FULLWIDTH_END or char == _IDEOGRAPHIC_SPACE
        for char in text
    )
