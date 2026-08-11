"""`proxy/anthropic_adapter.py` 的純函式測試，不碰網路、不碰 FastAPI。

涵蓋範圍是 7 步計畫第 3 步「純文字最小遮蔽」+ 第 4 步「工具呼叫遞迴處理」：
Anthropic Messages API 請求／回覆格式，與 AIR（OpenAI 相容格式）之間的
轉換，包含 `tool_use`/`tool_result`。圖片／文件附件（`image`/`document`）
尚未支援，相關測試只驗證「會被正確擋下」，不驗證轉換結果。
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
    """Claude Code 幾乎每個請求都宣告 tools，即使使用者只是單純問答。"""
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "Read", "input_schema": {}}],
    }
    assert anthropic_adapter.has_unsupported_content(payload) is False


def test_tool_use不算超出範圍():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
            }
        ]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is False


def test_tool_result不算超出範圍():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
            }
        ]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is False


def test_出現image算超出範圍():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image", "source": {"type": "base64", "data": "..."}}],
            }
        ]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is True


def test_tool_result裡藏著image也算超出範圍():
    """螢幕截圖類工具的結果可能是圖片，藏在 tool_result.content 裡。"""
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "image", "source": {}}],
                    }
                ],
            }
        ]
    }
    assert anthropic_adapter.has_unsupported_content(payload) is True


def test_content_是純字串時不會誤判():
    payload = {"messages": [{"role": "assistant", "content": "純文字回覆"}]}
    assert anthropic_adapter.has_unsupported_content(payload) is False


# ---------------------------------------------------------- to_openai_request：純文字


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
        "thinking": {"type": "adaptive"},
        "context_management": {"edits": []},
        "output_config": {"effort": "high"},
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert set(request.keys()) == {"model", "messages", "stream"}


# ---------------------------------------------------------- to_openai_request：工具呼叫


def test_assistant的tool_use會轉成openai的tool_calls():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "a.py"},
                    }
                ],
            }
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert request["messages"] == [
        {
            "role": "assistant",
            # 官方 OpenAI 規格允許 null，但 AIR 實測會拒絕；用空字串相容性更好
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"file_path": "a.py"}, ensure_ascii=False),
                    },
                }
            ],
        }
    ]


def test_assistant同時有文字前言與tool_use():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "我先讀一下檔案"},
                    {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
                ],
            }
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    message = request["messages"][0]
    assert message["content"] == "我先讀一下檔案"
    assert message["tool_calls"][0]["function"]["name"] == "Read"


def test_user的tool_result會轉成獨立的tool角色訊息():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "檔案內容",
                    }
                ],
            }
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert request["messages"] == [
        {"role": "tool", "tool_call_id": "toolu_1", "content": "檔案內容"}
    ]


def test_tool_result的content是文字block陣列時會接成字串():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "text", "text": "第一段"},
                            {"type": "text", "text": "第二段"},
                        ],
                    }
                ],
            }
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert request["messages"][0]["content"] == "第一段\n\n第二段"


def test_文字與tool_result混在同一則裡會拆成兩則且保留順序():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "先看這個"},
                    {"type": "tool_result", "tool_use_id": "t1", "content": "結果"},
                ],
            }
        ]
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert [m["role"] for m in request["messages"]] == ["user", "tool"]
    assert request["messages"][0]["content"] == "先看這個"
    assert request["messages"][1]["tool_call_id"] == "t1"


def test_tools會轉成openai的function_calling格式():
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "name": "Read",
                "description": "read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
            }
        ],
    }

    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")

    assert request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
            },
        }
    ]


def test_沒有tools時request裡不會出現tools欄位():
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    request = anthropic_adapter.to_openai_request(payload, model="gpt-4.1-mini")
    assert "tools" not in request


# ---------------------------------------------------------- response_to_event_stream


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


def test_純工具呼叫回覆會包成tool_use_block且stop_reason是tool_use():
    openai_response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "function": {
                                "name": "Read",
                                "arguments": '{"file_path": "a.py"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    raw = anthropic_adapter.response_to_event_stream(
        openai_response, model="claude-sonnet-5", message_id="msg_1"
    )
    events = _parse_sse_events(raw)

    starts = [e for e in events if e["type"] == "content_block_start"]
    assert len(starts) == 1
    assert starts[0]["content_block"]["type"] == "tool_use"
    assert starts[0]["content_block"]["id"] == "call_abc"
    assert starts[0]["content_block"]["name"] == "Read"

    deltas = [e for e in events if e["type"] == "content_block_delta"]
    assert deltas[0]["delta"]["type"] == "input_json_delta"
    assert json.loads(deltas[0]["delta"]["partial_json"]) == {"file_path": "a.py"}

    message_delta = next(e for e in events if e["type"] == "message_delta")
    assert message_delta["delta"]["stop_reason"] == "tool_use"


def test_文字前言加工具呼叫都會被翻譯且index依序遞增():
    openai_response = {
        "choices": [
            {
                "message": {
                    "content": "我先讀一下",
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "Read", "arguments": "{}"}}
                    ],
                }
            }
        ]
    }

    raw = anthropic_adapter.response_to_event_stream(
        openai_response, model="claude-sonnet-5", message_id="msg_1"
    )
    events = _parse_sse_events(raw)

    starts = [e for e in events if e["type"] == "content_block_start"]
    assert starts[0]["index"] == 0
    assert starts[0]["content_block"]["type"] == "text"
    assert starts[1]["index"] == 1
    assert starts[1]["content_block"]["type"] == "tool_use"


def test_多個工具呼叫時index各自遞增不互相覆蓋():
    openai_response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "Read", "arguments": "{}"}},
                        {"id": "call_2", "function": {"name": "Read", "arguments": "{}"}},
                    ],
                }
            }
        ]
    }

    raw = anthropic_adapter.response_to_event_stream(
        openai_response, model="claude-sonnet-5", message_id="msg_1"
    )
    events = _parse_sse_events(raw)

    starts = [e for e in events if e["type"] == "content_block_start"]
    assert [s["index"] for s in starts] == [0, 1]
    assert [s["content_block"]["id"] for s in starts] == ["call_1", "call_2"]


def test_沒有id時會給fallback_id避免claude_code收到空id():
    openai_response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"name": "Read", "arguments": "{}"}}],
                }
            }
        ]
    }

    raw = anthropic_adapter.response_to_event_stream(
        openai_response, model="claude-sonnet-5", message_id="msg_1"
    )
    events = _parse_sse_events(raw)

    start = next(e for e in events if e["type"] == "content_block_start")
    assert start["content_block"]["id"]  # 非空


# ---------------------------------------------------------- extract_reply_text


def test_extract_reply_text_正常情況():
    response = {"choices": [{"message": {"content": "你好"}}]}
    assert anthropic_adapter.extract_reply_text(response) == "你好"


def test_extract_reply_text_格式不對時回傳空字串():
    assert anthropic_adapter.extract_reply_text({}) == ""
    assert anthropic_adapter.extract_reply_text({"choices": []}) == ""
    assert anthropic_adapter.extract_reply_text({"choices": [{}]}) == ""


# ---------------------------------------------------------- AnthropicStreamTranslator（第 6 步：真串流）


def _openai_delta_event(delta: dict, finish_reason: str | None = None) -> str:
    obj = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _openai_stream(deltas: list[dict], finish_reason: str = "stop") -> bytes:
    """組一段假的 OpenAI 格式 SSE 位元組流，模擬 AIR 用 stream: true 回覆。"""
    body = "".join(_openai_delta_event(d) for d in deltas)
    body += _openai_delta_event({}, finish_reason=finish_reason)
    body += "data: [DONE]\n\n"
    return body.encode("utf-8")


def _translate(
    raw: bytes, chunk_size: int, model: str = "claude-sonnet-5", message_id: str = "msg_1"
) -> list[dict]:
    """模擬 proxy 逐段餵位元組進翻譯器，回傳 Claude Code 端會收到的事件陣列。"""
    translator = anthropic_adapter.AnthropicStreamTranslator(model=model, message_id=message_id)
    out = b""
    for i in range(0, len(raw), chunk_size):
        out += translator.feed(raw[i : i + chunk_size])
    out += translator.flush()
    return _parse_sse_events(out.decode("utf-8"))


def _delta_texts(events: list[dict]) -> str:
    return "".join(
        e["delta"]["text"] for e in events if e["type"] == "content_block_delta" and "text" in e["delta"]
    )


def test_純文字回覆會被翻譯成text_delta事件():
    raw = _openai_stream([{"content": "你"}, {"content": "好"}])
    events = _translate(raw, chunk_size=4096)

    types = [e["type"] for e in events]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert _delta_texts(events) == "你好"


def test_不管位元組怎麼切結果都一樣():
    raw = _openai_stream(
        [{"content": "客戶身分證 "}, {"content": "A123456789"}, {"content": " 已收到"}]
    )
    expected = "客戶身分證 A123456789 已收到"

    for size in (1, 2, 3, 7, 16, 64, 4096):
        events = _translate(raw, chunk_size=size)
        assert _delta_texts(events) == expected, f"切成 {size} 位元組時不一致"


def test_工具呼叫的id與name只在第一個delta出現():
    raw = _openai_stream(
        [
            {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "Read", "arguments": ""}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_path"'}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": ': "a.py"}'}}]},
        ],
        finish_reason="tool_calls",
    )
    events = _translate(raw, chunk_size=4096)

    start = next(e for e in events if e["type"] == "content_block_start")
    assert start["content_block"]["id"] == "call_1"
    assert start["content_block"]["name"] == "Read"

    partial_json = "".join(
        e["delta"]["partial_json"] for e in events if e["type"] == "content_block_delta"
    )
    assert json.loads(partial_json) == {"file_path": "a.py"}

    message_delta = next(e for e in events if e["type"] == "message_delta")
    assert message_delta["delta"]["stop_reason"] == "tool_use"


def test_多個平行工具呼叫不會互相插斷():
    raw = _openai_stream(
        [
            {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "Read", "arguments": ""}}]},
            {"tool_calls": [{"index": 1, "id": "call_b", "function": {"name": "Read", "arguments": ""}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]},
            {"tool_calls": [{"index": 1, "function": {"arguments": "{}"}}]},
        ],
        finish_reason="tool_calls",
    )
    events = _translate(raw, chunk_size=4096)

    starts = [e for e in events if e["type"] == "content_block_start"]
    assert [s["content_block"]["id"] for s in starts] == ["call_a", "call_b"]
    assert [s["index"] for s in starts] == [0, 1]

    deltas = [e for e in events if e["type"] == "content_block_delta"]
    assert [d["index"] for d in deltas] == [0, 1]


def test_文字後面接著工具呼叫時文字block會先收尾():
    raw = _openai_stream(
        [
            {"content": "我先讀一下"},
            {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "Read", "arguments": "{}"}}]},
        ],
        finish_reason="tool_calls",
    )
    events = _translate(raw, chunk_size=4096)

    stops = [e for e in events if e["type"] == "content_block_stop"]
    starts = [e for e in events if e["type"] == "content_block_start"]
    assert [s["index"] for s in starts] == [0, 1]
    assert starts[0]["content_block"]["type"] == "text"
    assert starts[1]["content_block"]["type"] == "tool_use"
    # 文字 block（index 0）必須在工具呼叫 block（index 1）開始前就先收尾
    assert stops[0]["index"] == 0


def test_finish_reason轉換成正確的stop_reason():
    cases = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
    for finish_reason, expected in cases.items():
        raw = _openai_stream([{"content": "hi"}], finish_reason=finish_reason)
        events = _translate(raw, chunk_size=4096)
        message_delta = next(e for e in events if e["type"] == "message_delta")
        assert message_delta["delta"]["stop_reason"] == expected, finish_reason


def test_沒有finish_reason時flush會補上收尾事件():
    """上游若因為某種原因沒送 finish_reason 就斷線，flush() 要保證還是有
    合法的 message_delta/message_stop，不能讓 Claude Code 收到沒收尾的串流。"""
    translator = anthropic_adapter.AnthropicStreamTranslator(model="claude-sonnet-5", message_id="msg_1")
    out = translator.feed(_openai_delta_event({"content": "hi"}).encode("utf-8"))
    out += translator.flush()

    events = _parse_sse_events(out.decode("utf-8"))
    assert events[-1]["type"] == "message_stop"
    assert events[-2]["type"] == "message_delta"


def test_完全空的回覆也有合法的最小事件序列():
    translator = anthropic_adapter.AnthropicStreamTranslator(model="claude-sonnet-5", message_id="msg_1")
    out = translator.flush()

    events = _parse_sse_events(out.decode("utf-8"))
    assert [e["type"] for e in events] == ["message_start", "message_delta", "message_stop"]


def test_DONE標記會觸發收尾即使沒有明確的finish_reason():
    raw = b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    events = _translate(raw, chunk_size=4096)
    assert events[-1]["type"] == "message_stop"
    assert _delta_texts(events) == "hi"
