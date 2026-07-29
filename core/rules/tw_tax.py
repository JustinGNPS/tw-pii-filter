"""統一編號（TW_TAX）checksum 驗證與偵測。"""

import re

_WEIGHTS = (1, 2, 1, 2, 1, 2, 4, 1)

# 候選字串：連續 8 碼數字，前後不可緊接英數字，避免截斷更長的字串
_TW_TAX_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d{8}(?![A-Za-z0-9])")


def _digit_sum(product: int) -> int:
    """乘積若為兩位數，個位與十位相加。"""
    if product >= 10:
        return product // 10 + product % 10
    return product


def is_valid_tw_tax(tax_str: str) -> bool:
    """驗證統一編號的檢查碼是否正確。"""
    if not isinstance(tax_str, str):
        return False

    candidate = tax_str.strip()
    if len(candidate) != 8 or not candidate.isdigit():
        return False

    digits = [int(c) for c in candidate]

    # 第 7 碼（index 6）為 7 時單獨處理，其餘各位照一般規則相加
    base_sum = sum(
        _digit_sum(digit * weight)
        for i, (digit, weight) in enumerate(zip(digits, _WEIGHTS))
        if i != 6
    )

    if digits[6] == 7:
        # 4*7=28 相加為 10，非個位數，特例規定該位可算 0 或 1，
        # 兩種情況只要有一種能被 5 整除即為有效
        return any((base_sum + alt) % 5 == 0 for alt in (0, 1))

    total = base_sum + _digit_sum(digits[6] * _WEIGHTS[6])
    return total % 5 == 0


def detect_tw_tax(text: str) -> dict:
    """在文字中找出所有 checksum 正確的統一編號，回傳符合
    docs/interface.md 格式的偵測結果。
    """
    spans = []
    count = 0
    for match in _TW_TAX_PATTERN.finditer(text):
        candidate = match.group()
        if not is_valid_tw_tax(candidate):
            continue
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "TW_TAX",
            "text": candidate,
            "confidence": 0.95,
            "source": "rule",
            "replacement": f"[TW_TAX_{count}]",
        })
    return {"text": text, "spans": spans}
