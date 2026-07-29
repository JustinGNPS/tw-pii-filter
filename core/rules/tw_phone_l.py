"""台灣市內電話（TW_PHONE_L）格式驗證與偵測。"""

import re

# 候選字串：區碼（可用括號包住，2-4 碼，第二碼須為 2-8，避免與手機 09 開頭混淆）
# + 分隔符（連字號或空白，可省略）+ 6-8 碼號碼（中間可有一個連字號）
# 前後不可緊接數字，避免截斷更長的數字串
_TW_PHONE_L_PATTERN = re.compile(
    r"(?<!\d)\(?0[2-8]\d{0,2}\)?[-\s]?\d{3,4}-?\d{3,4}(?!\d)"
)

_STRIP_CHARS = re.compile(r"[()\-\s]")


def is_valid_tw_phone_l(phone_str: str) -> bool:
    """驗證字串是否為合法台灣市話格式（區碼 + 號碼，共 8-10 碼數字）。"""
    if not isinstance(phone_str, str):
        return False

    digits = _STRIP_CHARS.sub("", phone_str.strip())
    if not digits.isdigit() or not (8 <= len(digits) <= 10):
        return False

    # 區碼須為 0 開頭，第二碼 2-8（09 開頭為手機，非市話區碼）
    return digits[0] == "0" and digits[1] in "2345678"


def detect_tw_phone_l(text: str) -> dict:
    """在文字中找出所有台灣市話號碼，回傳符合 docs/interface.md 格式的偵測結果。"""
    spans = []
    count = 0
    for match in _TW_PHONE_L_PATTERN.finditer(text):
        candidate = match.group()
        if not is_valid_tw_phone_l(candidate):
            continue
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "TW_PHONE_L",
            "text": candidate,
            "confidence": 0.85,
            "source": "rule",
            "replacement": f"[TW_PHONE_L_{count}]",
        })
    return {"text": text, "spans": spans}
