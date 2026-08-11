"""Claude Code（Anthropic Messages API）↔ AIR（OpenAI 相容格式）之間的轉換。

**目前只覆蓋純文字對話**（`messages[]` 裡沒有 `tool_use`/`tool_result`
block）——這是 7 步計畫第 3 步「純文字最小遮蔽」的範圍。工具呼叫的遞迴處理
是下一步的事；遇到已經超出範圍的請求，呼叫端應該先用 `has_unsupported_content()`
擋下、誠實回報「還沒支援」，不要硬翻譯出錯誤結果（那比直接告訴使用者
「這個功能還沒做」更糟——參見 `docs/B_progress.md` 裡型別代碼正規化那段的
教訓：錯誤的沉默失敗比明確的失敗更危險）。

遮蔽/還原刻意沒有另外寫一套：Anthropic 的 `messages[].content`／`system`
文字 block 形狀（`{"type": "text", "text": "..."}`）與 OpenAI 多模態
content parts 重疊，`proxy.detector.extract_texts()` 本來就認得這個形狀
（含新增的 `system` 欄位支援），因此可以直接對 Anthropic 格式的 payload
呼叫 `proxy.masker.mask_payload()`，遮蔽完再由這個模組把結果轉成 AIR
聽得懂的請求。
"""

import json
from typing import Any

_UNSUPPORTED_BLOCK_TYPES = ("tool_use", "tool_result")


def has_unsupported_content(payload: dict) -> bool:
    """這個請求有沒有用到目前還沒支援的東西（目前只有工具呼叫）。

    `tools` 欄位本身不算——Claude Code 幾乎每個請求都會宣告 tools（即使
    使用者只是想單純問答），只有當對話歷史裡真的出現 `tool_use`／
    `tool_result` block，才代表這個對話已經走到本模組還不會翻譯的階段。
    """
    for message in payload.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in _UNSUPPORTED_BLOCK_TYPES:
                return True
    return False


def _block_list_text(blocks: Any) -> str:
    """把 `[{"type": "text", "text": "..."}, ...]` 接成單一字串。"""
    parts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n\n".join(parts)


def _message_text(content: Any) -> str:
    """把 Anthropic 的 message content（字串或 block 陣列）轉成單一字串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _block_list_text(content)
    return ""


def to_openai_request(payload: dict, model: str) -> dict:
    """把 Anthropic 格式的請求轉成 AIR（OpenAI 相容）格式，準備真的送出去。

    只搬純文字對話需要的欄位：`system` 併成一則 system 訊息放在最前面，
    `messages[]` 逐則轉換角色與文字內容。`tools`／`thinking`／
    `context_management`／`output_config` 這些 AIR 不認得、也還沒有對應
    轉法的欄位一律丟棄——寧可少功能，不要亂猜轉法送出格式不明的請求。

    呼叫前應先用 `has_unsupported_content()` 確認這個請求在範圍內；
    `model` 是 AIR 那邊真實存在的模型名稱（例如 `gpt-4.1-mini`），
    不是 Claude Code 宣告的 `claude-sonnet-5`（AIR 不認得這個名字）。
    """
    openai_messages: list[dict] = []

    system_text = _message_text(payload.get("system"))
    if system_text:
        openai_messages.append({"role": "system", "content": system_text})

    for message in payload.get("messages", []):
        role = message.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        text = _message_text(message.get("content"))
        if text:
            openai_messages.append({"role": role, "content": text})

    return {"model": model, "messages": openai_messages, "stream": False}


def extract_reply_text(openai_response: dict) -> str:
    """從 AIR 的（非串流）回覆裡取出助理回覆的純文字。"""
    try:
        return openai_response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def text_event_stream(text: str, model: str, message_id: str) -> str:
    """把一段完整的助理回覆文字，包成 Anthropic Messages API 的串流事件序列。

    Claude Code 送出的請求一律 `stream: true`，即使 AIR 這邊是用非串流呼叫
    拿到完整回覆（第 5 步「驗證非串流還原不用改」的前提），對 Claude Code
    還是要回一段合法的 SSE——這裡一次把完整內容包成單一個 `text_delta`
    事件送出，不是逐字元真正串流，但事件形狀符合協定，Claude Code 收得懂。
    """
    events = [
        _sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        ),
        _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        _sse_event("message_stop", {"type": "message_stop"}),
    ]
    return "".join(events)
