"""Claude Code（Anthropic Messages API）↔ AIR（OpenAI 相容格式）之間的轉換。

**第 4 步：工具呼叫遞迴處理**——涵蓋純文字對話與工具呼叫（`tool_use`／
`tool_result`）。仍未支援的是圖片／文件附件（`image`／`document` block），
遇到就用 `has_unsupported_content()` 擋下、誠實回報「還沒支援」，不要硬翻
出格式不明的請求（比對外承認「這個功能還沒做」更糟——參見
`docs/B_progress.md` 型別代碼正規化那段的教訓：沉默失敗比明確失敗更危險）。

遮蔽/還原刻意沒有另外寫一套：
- `proxy.detector.extract_texts()` 已擴充到能挖出 `messages[].content`
  文字 block、`system` 欄位、`tool_use.input`（任意深度巢狀 JSON 裡的每個
  字串葉節點）、`tool_result.content`（字串或文字 block 陣列）——遮蔽時直接
  對 Anthropic 格式的 payload 呼叫既有的 `proxy.masker.mask_payload()`。
- 還原沿用既有的 `proxy.restorer.restore_body()`，在 AIR 回覆的原始 JSON
  位元組上做文字取代，不需要為 Anthropic 格式另外寫還原邏輯（第 5 步
  「驗證非串流還原不用改」，這次連工具呼叫的參數也一併驗證到）。

## 為什麼一定要遮蔽 `tool_use.input`

`assistant` 的 `tool_use` block 一旦被 proxy 還原、送回 Claude Code，
裡面就是**真值**（例如「把 A123456789 寫進 mobile 欄位」這種工具參數）。
Claude Code 每次請求都會重送整段對話歷史，因此下一輪請求裡，這個歷史
assistant 訊息會**帶著真值**再度出現在 payload 裡，若不重新遮蔽就直接
轉送給 AIR，等於真值又外洩了一次——跟 OpenAI 格式的 `tool_calls[].function.arguments`
是同一類風險（`proxy/restorer.py` 的 `SSERestorer` docstring 記錄過
OpenCode 曾經因為這條路徑沒被處理而把佔位符寫進使用者檔案；這裡是反過來
的風險：真值沒被重新遮蔽而送出去）。
"""

import json
from typing import Any

# 目前還不會翻譯的內容類型：圖片／文件附件。工具呼叫（tool_use/tool_result）
# 已經支援，不在這個名單裡。
_UNSUPPORTED_BLOCK_TYPES = ("image", "document")


def _blocks_have_unsupported_type(blocks: Any) -> bool:
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in _UNSUPPORTED_BLOCK_TYPES:
            return True
        if block.get("type") == "tool_result" and _blocks_have_unsupported_type(
            block.get("content")
        ):
            return True  # 圖片也可能藏在 tool_result 裡（例如螢幕截圖工具）
    return False


def has_unsupported_content(payload: dict) -> bool:
    """這個請求有沒有用到目前還不會翻譯的東西（圖片／文件附件）。"""
    for message in payload.get("messages", []):
        if _blocks_have_unsupported_type(message.get("content")):
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
    """把 Anthropic 的 content（字串或 block 陣列）轉成單一字串，只取文字。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _block_list_text(content)
    return ""


def _tool_result_text(content: Any) -> str:
    """把 `tool_result` block 的 `content`（字串或文字 block 陣列）轉成字串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _block_list_text(content)
    return ""


def _assistant_message_to_openai(content: Any) -> list[dict]:
    """把 Anthropic 的 assistant content 轉成 OpenAI 格式的訊息（0 或 1 則）。

    一則 assistant 訊息裡可能同時有文字前言與一或多個 `tool_use`——OpenAI
    格式把這兩者放在同一則訊息裡（`content` + `tool_calls`），形狀是對的，
    不需要拆成多則。
    """
    if isinstance(content, str):
        return [{"role": "assistant", "content": content}] if content else []
    if not isinstance(content, list):
        return []

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(
                            block.get("input", {}), ensure_ascii=False
                        ),
                    },
                }
            )

    text = "\n\n".join(text_parts)
    if not text and not tool_calls:
        return []
    # 官方 OpenAI 規格允許只有 tool_calls 時 content 是 null，但實測 AIR
    # 的驗證比規格嚴格，null 會被直接拒絕（400 "Input should be a valid
    # string"）——用空字串相容性更好，兩種上游都吃。
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return [message]


def _user_message_to_openai(role: str, content: Any) -> list[dict]:
    """把 Anthropic 的 user/system content 轉成 OpenAI 格式的訊息（0 或多則）。

    `tool_result` 在 OpenAI 格式裡必須是**獨立一則** `role: "tool"` 訊息
    （一個 tool_call 對一則），不能跟其他內容混在同一則裡；純文字則照原樣
    包成一則。同一個 user 回合裡可能兩者都有（雖然 Claude Code 實測目前
    觀察到的都是純 tool_result 或純文字），照原本出現順序拆開，順序不能亂。
    """
    if isinstance(content, str):
        return [{"role": role, "content": content}] if content else []
    if not isinstance(content, list):
        return []

    messages: list[dict] = []
    text_parts: list[str] = []

    def _flush_text() -> None:
        if text_parts:
            messages.append({"role": role, "content": "\n\n".join(text_parts)})
            text_parts.clear()

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_result":
            _flush_text()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": _tool_result_text(block.get("content")),
                }
            )
    _flush_text()
    return messages


def to_openai_tools(anthropic_tools: Any) -> list[dict]:
    """把 Anthropic 的 `tools[]`（`input_schema` 已經是解析好的 JSON Schema）
    轉成 OpenAI 的 function-calling 格式。"""
    if not isinstance(anthropic_tools, list):
        return []
    tools = []
    for tool in anthropic_tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema")
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return tools


def to_openai_request(payload: dict, model: str) -> dict:
    """把 Anthropic 格式的請求轉成 AIR（OpenAI 相容）格式，準備真的送出去。

    `system` 併成一則 system 訊息放在最前面，`messages[]` 逐則轉換
    （純文字／`tool_use`／`tool_result` 皆涵蓋），`tools[]` 一併轉換過去讓
    AIR 真的能決定要不要呼叫工具。`thinking`／`context_management`／
    `output_config` 這些 AIR 不認得、也沒有對應轉法的欄位一律丟棄。

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
        content = message.get("content")
        if role == "assistant":
            openai_messages.extend(_assistant_message_to_openai(content))
        elif role in ("user", "system"):
            openai_messages.extend(_user_message_to_openai(role, content))
        # 其餘角色目前沒有真實樣本觀察到，先忽略而不是亂猜轉法

    request: dict = {"model": model, "messages": openai_messages, "stream": False}
    tools = to_openai_tools(payload.get("tools"))
    if tools:
        request["tools"] = tools
    return request


def _first_choice_message(openai_response: dict) -> dict:
    try:
        message = openai_response["choices"][0]["message"]
        return message if isinstance(message, dict) else {}
    except (KeyError, IndexError, TypeError):
        return {}


def extract_reply_text(openai_response: dict) -> str:
    """從 AIR 的（非串流）回覆裡取出助理回覆的純文字（不含工具呼叫）。"""
    return _first_choice_message(openai_response).get("content") or ""


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _text_block_events(index: int, text: str) -> list[str]:
    return [
        _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        _sse_event("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def _tool_use_block_events(index: int, call: dict, fallback_id: str) -> list[str]:
    """把 OpenAI 回覆裡的一個 `tool_calls[]` 項目包成 Anthropic 的
    `tool_use` content block 事件序列。

    `partial_json` 一次給完整的參數 JSON（不是逐字元真串流），跟
    `_text_block_events` 的作法一致——事件形狀符合協定，Claude Code 收到
    `content_block_stop` 就會把 buffer 裡累積的 JSON 當完整參數解析。
    """
    function = call.get("function") or {}
    call_id = call.get("id") or fallback_id
    return [
        _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": call_id,
                    "name": function.get("name", ""),
                    "input": {},
                },
            },
        ),
        _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": function.get("arguments") or "{}",
                },
            },
        ),
        _sse_event("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def response_to_event_stream(openai_response: dict, model: str, message_id: str) -> str:
    """把 AIR 的（非串流）回覆包成 Anthropic Messages API 的串流事件序列。

    涵蓋純文字回覆、純工具呼叫、以及「文字前言 + 工具呼叫」混合的情況——
    OpenAI 的 `message.content` 與 `message.tool_calls` 可以同時存在，
    翻譯過去時文字排 index 0，工具呼叫依序排在後面。

    Claude Code 一律 `stream: true`，即使 AIR 這邊是非串流呼叫拿到完整
    回覆，對 Claude Code 還是要回一段合法的 SSE（見 `text_event_stream`
    docstring 的說明，此函式取代並涵蓋了原本的用途）。
    """
    message = _first_choice_message(openai_response)
    text = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []

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
        )
    ]

    index = 0
    if text:
        events.extend(_text_block_events(index, text))
        index += 1
    for i, call in enumerate(tool_calls):
        events.extend(_tool_use_block_events(index, call, f"toolu_{message_id}_{i}"))
        index += 1

    stop_reason = "tool_use" if tool_calls else "end_turn"
    events.append(
        _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        )
    )
    events.append(_sse_event("message_stop", {"type": "message_stop"}))
    return "".join(events)


def text_event_stream(text: str, model: str, message_id: str) -> str:
    """純文字版的 `response_to_event_stream`，capture 模式（`proxy/main.py`）
    用來包偽造的收尾回覆——語意上等同呼叫一個沒有工具呼叫的 OpenAI 回覆。
    """
    return response_to_event_stream(
        {"choices": [{"message": {"content": text}}]}, model=model, message_id=message_id
    )
