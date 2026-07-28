"""台灣身分證字號（TW_ID）checksum 驗證與偵測。"""

import re

# 首字母對應兩位數字（標準對照表）
_LETTER_MAP = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17,
    "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23,
    "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28, "V": 29, "W": 32, "X": 30,
    "Y": 31, "Z": 33,
}

# 依序對應 [n1, n2, d1..d9] 共 11 位的權重
_WEIGHTS = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)

# 候選字串：1 個英文字母 + 9 碼數字，前後不可緊接英數字，避免截斷更長的字串
_TW_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\d{9}(?![A-Za-z0-9])")


def is_valid_tw_id(id_str: str) -> bool:
    """驗證台灣身分證字號的檢查碼是否正確。"""
    if not isinstance(id_str, str):
        return False

    candidate = id_str.strip().upper()
    if len(candidate) != 10:
        return False

    letter, digits = candidate[0], candidate[1:]
    if letter not in _LETTER_MAP or not digits.isdigit():
        return False

    n1, n2 = divmod(_LETTER_MAP[letter], 10)
    values = [n1, n2] + [int(d) for d in digits]

    total = sum(value * weight for value, weight in zip(values, _WEIGHTS))
    return total % 10 == 0


def detect_tw_id(text: str) -> dict:
    """在文字中找出所有 checksum 正確的台灣身分證字號，回傳符合
    docs/interface.md 格式的偵測結果。
    """
    spans = []
    count = 0
    for match in _TW_ID_PATTERN.finditer(text):
        candidate = match.group()
        if not is_valid_tw_id(candidate):
            continue
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "TW_ID",
            "text": candidate,
            "confidence": 0.99,
            "source": "rule",
            "replacement": f"[TW_ID_{count}]",
        })
    return {"text": text, "spans": spans}
