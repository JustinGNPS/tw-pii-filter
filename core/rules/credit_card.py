"""信用卡號（CREDIT_CARD）Luhn checksum 驗證與偵測。"""

import re

# 候選字串：13-19 碼數字，中間可用空白或連字號每隔數碼分隔
# 前後不可緊接英數字，避免截斷更長的字串
_CREDIT_CARD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])\d(?:[ -]?\d){12,18}(?![A-Za-z0-9])"
)

_STRIP_CHARS = re.compile(r"[ -]")


def _luhn_checksum(digits: str) -> int:
    """計算 Luhn 演算法的檢查總和，對合法卡號應為 0（mod 10）。"""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        value = int(ch)
        if i % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10


def is_valid_credit_card(card_str: str) -> bool:
    """驗證字串是否為合法信用卡號（13-19 碼數字，且通過 Luhn checksum）。"""
    if not isinstance(card_str, str):
        return False

    digits = _STRIP_CHARS.sub("", card_str.strip())
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False

    return _luhn_checksum(digits) == 0


def detect_credit_card(text: str) -> dict:
    """在文字中找出所有 checksum 正確的信用卡號，回傳符合
    docs/interface.md 格式的偵測結果。
    """
    spans = []
    count = 0
    for match in _CREDIT_CARD_PATTERN.finditer(text):
        candidate = match.group()
        if not is_valid_credit_card(candidate):
            continue
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "CREDIT_CARD",
            "text": candidate,
            "confidence": 0.95,
            "source": "rule",
            "replacement": f"[CREDIT_CARD_{count}]",
        })
    return {"text": text, "spans": spans}
