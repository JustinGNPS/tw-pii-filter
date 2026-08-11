"""`proxy/anthropic_adapter.py` 的純函式測試，不碰網路、不碰 FastAPI。

涵蓋範圍是 7 步計畫第 3 步「純文字最小遮蔽」：Anthropic Messages API
請求／回覆格式，與 AIR（OpenAI 相容格式）之間的轉換。工具呼叫
（`tool_use`/`tool_result`）尚未支援，相關測試只驗證「會被正確擋下」，
不驗證轉換結果。
"""

import json

from proxy import anthropic_adapter


# ---------------------------------------------------------- has_unsupported_content


def test_純文字對話沒有超出範圍():
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is False


def test_宣告_tools_不算超出範圍():
    """Claude Code 幾乎每個請求都宣告 tools，即使使用者只是單純問答 ——
    只有對話歷史裡真的出現 tool_use/tool_result 才算超出目前的翻譯範圍。"""
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "Read", "input_schema": {}}],
    }
    assert anthropic_adapter.has_unsupported_content(payload) is False


def test_出現_tool_use_算超出範圍():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
            }
        ]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is True


def test_出現_tool_result_算超出範圍():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
            }
        ]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is True


def test_content_是純字串時不會誤判():
    payload = {"messages": [{"role": "assistant", "content": "純文字回覆"}]}
    assert anthropic_adapter.has_unsupported_content(payload) is False


# ---------------------------------------------------------- to_openai_request


def test_system陣列會接成一則_system_訊息():
    payload = {
        "system": [
            {"type": "text", "text": "第一段"},
            {"type": "text", "text": "第二段", "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert request["model"] == "gpt-4.1-mini"
    assert request["stream"] is False
    assert request["messages"][0] == {
        "role": "system",
        "content": "第一段\n\n第二段",
    }
    assert request["messages"][1] == {"role": "user", "content": "hi"}


def test_content是block陣列時會接成一段文字():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "第一段"},
                    {"type": "text", "text": "第二段"},
                ],
            }
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert request["messages"] == [
        {"role": "user", "content": "第一段\n\n第二段"}
    ]


def test_mid_conversation_system角色訊息會被保留():
    """Claude Code 真實觀察到的行為：`messages[]` 裡會出現 role="system" 的
    訊息（不只是最外層的 system 欄位），見 docs/B_progress.md 08-11 那筆記錄。"""
    payload = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "有哪些 agent 可以用……"},
            {"role": "assistant", "content": "好的"},
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    roles = [m["role"] for m in request["messages"]]
    assert roles == ["user", "system", "assistant"]


def test_不合法角色會被跳過():
    payload = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "不支援的角色"},
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert len(request["messages"]) == 1
    assert request["messages"][0]["role"] == "user"


def test_沒有文字內容的訊息不會產生空白訊息():
    payload = {"messages": [{"role": "user", "content": ""}]}
    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")
    assert request["messages"] == []


def test_丟掉_AIR_不認得的欄位():
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "Read"}],
        "thinking": {"type": "adaptive"},
        "context_management": {"edits": []},
        "output_config": {"effort": "high"},
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert set(request.keys()) == {"model", "messages", "stream"}


# ---------------------------------------------------------- extract_reply_text


def test_extract_reply_text_正常情況():
    response = {"choices": [{"message": {"content": "你好"}}]}
    assert anthropic_adapter.extract_reply_text(response) == "你好"


def test_extract_reply_text_格式不對時回傳空字串():
    assert anthropic_adapter.extract_reply_text({}) == ""
    assert anthropic_adapter.extract_reply_text({"choices": []}) == ""
    assert anthropic_adapter.extract_reply_text({"choices": [{}]}) == ""


# ---------------------------------------------------------- text_event_stream


def _parse_sse_events(raw: str) -> list[dict]:
    """把 `event: x\\ndata: {...}\\n\\n` 格式的字串解析回事件物件陣列，
    模擬 Claude Code 那端會怎麼讀。"""
    events = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        data_line = next(line for line in lines if line.startswith("data: "))
        events.append(json.loads(data_line[len("data: ") :]))
    return events


def test_text_event_stream事件序列可以被還原成完整文字():
    raw = anthropic_adapter.text_event_stream(
        "你的身分證 A123456789 已收到", model="claude-sonnet-5", message_id="msg_1"
    )
    events = _parse_sse_events(raw)

    types = [e["type"] for e in events]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[0]["message"]["id"] == "msg_1"
    assert events[0]["message"]["model"] == "claude-sonnet-5"
    assert events[2]["delta"]["text"] == "你的身分證 A123456789 已收到"
    assert events[4]["delta"]["stop_reason"] == "end_turn"
