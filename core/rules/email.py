"""電子郵件地址（EMAIL）格式驗證與偵測。"""

import re

# 標準 email 格式：local-part @ domain.tld
# local-part 允許英數字與常見符號 . _ % + -；domain 允許英數字、. 與 -；tld 至少 2 碼英文字母
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9._%+-])"
)


def is_valid_email(email_str: str) -> bool:
    """驗證字串是否為合法 email 格式。"""
    if not isinstance(email_str, str):
        return False

    candidate = email_str.strip()
    match = _EMAIL_PATTERN.fullmatch(candidate)
    return match is not None


def detect_email(text: str) -> dict:
    """在文字中找出所有 email 地址，回傳符合 docs/interface.md 格式的偵測結果。"""
    spans = []
    count = 0
    for match in _EMAIL_PATTERN.finditer(text):
        candidate = match.group()
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "EMAIL",
            "text": candidate,
            "confidence": 0.95,
            "source": "rule",
            "replacement": f"[EMAIL_{count}]",
        })
    return {"text": text, "spans": spans}
