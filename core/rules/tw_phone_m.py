"""台灣手機號碼（TW_PHONE_M）格式驗證與偵測。"""

import re

# 候選字串：09 開頭 + 8 碼數字，中間可有 0~2 個連字號（如 0912-345-678、0912345678）
# 前後不可緊接數字，避免截斷更長的數字串
_TW_PHONE_M_PATTERN = re.compile(
    r"(?<!\d)09\d{2}-?\d{3}-?\d{3}(?!\d)"
)


def is_valid_tw_phone_m(phone_str: str) -> bool:
    """驗證字串是否為合法台灣手機號碼格式（09 開頭共 10 碼數字）。"""
    if not isinstance(phone_str, str):
        return False

    digits = phone_str.strip().replace("-", "")
    if len(digits) != 10 or not digits.isdigit():
        return False

    return digits.startswith("09")


def detect_tw_phone_m(text: str) -> dict:
    """在文字中找出所有台灣手機號碼，回傳符合 docs/interface.md 格式的偵測結果。"""
    spans = []
    count = 0
    for match in _TW_PHONE_M_PATTERN.finditer(text):
        candidate = match.group()
        if not is_valid_tw_phone_m(candidate):
            continue
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "TW_PHONE_M",
            "text": candidate,
            "confidence": 0.9,
            "source": "rule",
            "replacement": f"[TW_PHONE_M_{count}]",
        })
    return {"text": text, "spans": spans}
