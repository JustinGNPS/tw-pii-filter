"""包住 A 的 `core.rules.detect_all()`，並負責「從 payload 挖出該掃的欄位」。

分成兩件事：

1. **介面隔離**：A 的 `docs/interface.md` 尚未完全定案，proxy 其他檔案一律
   只呼叫本模組，介面若變動只需要改這裡。
2. **欄位萃取**：LLM 請求是一包巢狀 JSON，個資可能藏在 `messages[i].content`、
   content parts、tool call 參數等位置。本模組把這些文字欄位連同「路徑」一起
   取出，讓第二版可以照同一條路徑把遮蔽後的文字寫回去。

**第一版只警告、不改文字**（依 PDF §7.3）。遮蔽與還原是下一版的事。

隱私原則：本模組的回傳與 log 一律**不含偵測到的原始個資內容**，只有型別、
數量與位置。log 檔本身也是個資外洩管道。
"""

from typing import Any

from core.rules import detect_all as _detect_all

# JSON 路徑：dict 的 key 用 str，list 的 index 用 int
Path = tuple[Any, ...]


def detect(text: str) -> dict:
    """呼叫 A 的偵測核心。回傳格式見 `docs/interface.md`。

    A 的 `detect_all()` 內部已做 Layer 4 重疊仲裁（`core/rules/conflict_resolver.py`），
    因此回傳的 spans 保證互不重疊 —— proxy 之後做替換時不需要自己再仲裁。
    """
    return _detect_all(text)


def _extract_from_message(message: Any, base: Path) -> list[tuple[Path, str]]:
    """從單一 message 取出所有文字欄位。"""
    found: list[tuple[Path, str]] = []
    if not isinstance(message, dict):
        return found

    content = message.get("content")
    if isinstance(content, str):
        found.append((base + ("content",), content))
    elif isinstance(content, list):
        # 多模態格式：content 是 [{"type": "text", "text": "..."}, ...]
        for i, part in enumerate(content):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                found.append((base + ("content", i, "text"), part["text"]))

    # agent 讀檔的結果常常是以 tool call 參數的形式回到對話歷史裡
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for i, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            arguments = call.get("function", {}).get("arguments")
            if isinstance(arguments, str):
                found.append(
                    (base + ("tool_calls", i, "function", "arguments"), arguments)
                )

    return found


def extract_texts(payload: Any) -> list[tuple[Path, str]]:
    """從 OpenAI 相容的請求 payload 中，取出所有應該被掃描的文字欄位。

    回傳 `[(路徑, 文字), ...]`，路徑可餵給 `get_at()` / `set_at()`。

    涵蓋範圍：
    - `messages[i].content`（字串或多模態 parts）
    - `messages[i].tool_calls[j].function.arguments`
    - `prompt`（舊版 completions，字串或字串陣列）
    - `input`（embeddings，字串或字串陣列）
    """
    found: list[tuple[Path, str]] = []
    if not isinstance(payload, dict):
        return found

    messages = payload.get("messages")
    if isinstance(messages, list):
        for i, message in enumerate(messages):
            found.extend(_extract_from_message(message, ("messages", i)))

    for key in ("prompt", "input"):
        value = payload.get(key)
        if isinstance(value, str):
            found.append(((key,), value))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    found.append(((key, i), item))

    return found


def get_at(payload: Any, path: Path) -> Any:
    """依路徑取值。"""
    node = payload
    for key in path:
        node = node[key]
    return node


def set_at(payload: Any, path: Path, value: Any) -> None:
    """依路徑寫值（就地修改）。第二版做遮蔽時會用到。"""
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def scan_payload(payload: Any) -> list[dict]:
    """掃描整包 payload，回傳每個文字欄位的偵測結果。

    回傳 `[{"path": Path, "text": str, "spans": [...]}, ...]`，只包含有偵測到
    東西的欄位。第一版僅供警告與統計使用，不修改 payload。
    """
    results = []
    for path, text in extract_texts(payload):
        spans = detect(text)["spans"]
        if spans:
            results.append({"path": path, "text": text, "spans": spans})
    return results


def summarize(results: list[dict]) -> dict[str, int]:
    """把掃描結果壓成「型別 -> 筆數」，**不含任何原始個資內容**，可安全寫進 log。"""
    counts: dict[str, int] = {}
    for result in results:
        for span in result["spans"]:
            counts[span["type"]] = counts.get(span["type"], 0) + 1
    return counts


def format_warning(counts: dict[str, int]) -> str:
    """把摘要格式化成一行警告訊息。無偵測結果時回傳空字串。"""
    if not counts:
        return ""
    detail = "、".join(f"{t} x{n}" for t, n in sorted(counts.items()))
    total = sum(counts.values())
    return f"偵測到 {total} 筆敏感資訊（{detail}）"
