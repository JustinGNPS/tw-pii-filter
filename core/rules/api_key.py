"""API Key / Token（API_KEY）常見樣式判斷與偵測。"""

import re

# 涵蓋常見的 API key / token 前綴樣式（依特定前綴優先於較泛用的前綴排列）：
#   sk-ant-...   Anthropic API key
#   sk-proj-...  OpenAI project key
#   sk-...       泛用 sk- 前綴（如舊版 OpenAI 風格）
#   ghp_...      GitHub Personal Access Token
#   AKIA...      AWS Access Key ID
#   AIza...      Google API key
#   xox[a-z]-... Slack token（xoxb-、xoxp- 等）
#   eyJ...       JWT（header.payload.signature，header 固定以 eyJ 開頭）
_TOKEN_ALTERNATION = (
    r"sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-proj-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{30,40}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|xox[a-z]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)

# 前後不可緊接英數字、底線或連字號，避免截斷更長的字串
_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + _TOKEN_ALTERNATION + r")(?![A-Za-z0-9_-])"
)
_TOKEN_FULLMATCH = re.compile(r"(?:" + _TOKEN_ALTERNATION + r")")

# 常見「標籤 = 值」樣式，如 API_KEY=xxx、token=xxx、password=xxx
# 只擷取值本身（group 1）作為偵測片段，不含標籤與等號
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:API[_-]?KEY|TOKEN|PASSWORD)\s*=\s*['\"]?([A-Za-z0-9_\-./+]{6,})['\"]?"
)


def is_valid_api_key(key_str: str) -> bool:
    """判斷字串是否符合常見 API key / token 樣式（前綴型樣式，如 sk-、AKIA、JWT 等）。"""
    if not isinstance(key_str, str):
        return False

    candidate = key_str.strip()
    return _TOKEN_FULLMATCH.fullmatch(candidate) is not None


def detect_api_key(text: str) -> dict:
    """在文字中找出所有 API key / token，回傳符合 docs/interface.md 格式的偵測結果。

    來源包含：已知前綴樣式（sk-ant-、sk-proj-、sk-、ghp_、AKIA、AIza、xox[a-z]-、JWT）
    以及「標籤 = 值」的泛用樣式（API_KEY=、token=、password=）。
    若兩種來源在文字中判定到重疊區間（例如 API_KEY=sk-xxx 這種賦值本身就是已知前綴樣式），
    只保留其中一個，避免同一段文字被重複標記。
    """
    candidates = []
    for match in _TOKEN_PATTERN.finditer(text):
        candidates.append((match.start(), match.end(), match.group()))
    for match in _ASSIGNMENT_PATTERN.finditer(text):
        candidates.append((match.start(1), match.end(1), match.group(1)))

    # 依起始位置排序，起始位置相同時優先保留較長的片段
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

    spans = []
    count = 0
    last_end = -1
    for start, end, candidate in candidates:
        if start < last_end:
            continue
        count += 1
        spans.append({
            "start": start,
            "end": end,
            "type": "API_KEY",
            "text": candidate,
            "confidence": 0.9,
            "source": "rule",
            "replacement": f"[API_KEY_{count}]",
        })
        last_end = end
    return {"text": text, "spans": spans}
