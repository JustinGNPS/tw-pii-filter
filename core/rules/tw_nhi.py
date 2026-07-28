"""健保卡號（TW_NHI）格式驗證與偵測（無 checksum，僅驗證格式）。"""

import re

# 候選字串：連續 12 碼數字，前後不可緊接英數字，避免截斷更長的字串
_TW_NHI_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d{12}(?![A-Za-z0-9])")


def is_valid_tw_nhi(nhi_str: str) -> bool:
    """驗證字串是否為合法健保卡號格式（純 12 碼數字，不驗 checksum）。"""
    if not isinstance(nhi_str, str):
        return False

    candidate = nhi_str.strip()
    return len(candidate) == 12 and candidate.isdigit()


def detect_tw_nhi(text: str) -> dict:
    """在文字中找出所有健保卡號，回傳符合 docs/interface.md 格式的偵測結果。"""
    spans = []
    count = 0
    for match in _TW_NHI_PATTERN.finditer(text):
        candidate = match.group()
        if not is_valid_tw_nhi(candidate):
            continue
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "TW_NHI",
            "text": candidate,
            "confidence": 0.6,
            "source": "rule",
            "replacement": f"[TW_NHI_{count}]",
        })
    return {"text": text, "spans": spans}
