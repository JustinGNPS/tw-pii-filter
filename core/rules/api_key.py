"""API Key / Token（API_KEY）常見樣式判斷與偵測。"""

import re

# 涵蓋常見的 API key / token 樣式：
#   sk-...      常見 API 服務金鑰前綴（如 OpenAI 風格）
#   ghp_...     GitHub Personal Access Token
#   AKIA...     AWS Access Key ID
_TOKEN_ALTERNATION = (
    r"sk-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{30,40}"
    r"|AKIA[0-9A-Z]{16}"
)

# 前後不可緊接英數字、底線或連字號，避免截斷更長的字串
_API_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + _TOKEN_ALTERNATION + r")(?![A-Za-z0-9_-])"
)
_API_KEY_FULLMATCH = re.compile(r"(?:" + _TOKEN_ALTERNATION + r")")


def is_valid_api_key(key_str: str) -> bool:
    """判斷字串是否符合常見 API key / token 樣式。"""
    if not isinstance(key_str, str):
        return False

    candidate = key_str.strip()
    return _API_KEY_FULLMATCH.fullmatch(candidate) is not None


def detect_api_key(text: str) -> dict:
    """在文字中找出所有 API key / token，回傳符合 docs/interface.md 格式的偵測結果。"""
    spans = []
    count = 0
    for match in _API_KEY_PATTERN.finditer(text):
        candidate = match.group()
        count += 1
        spans.append({
            "start": match.start(),
            "end": match.end(),
            "type": "API_KEY",
            "text": candidate,
            "confidence": 0.9,
            "source": "rule",
            "replacement": f"[API_KEY_{count}]",
        })
    return {"text": text, "spans": spans}
