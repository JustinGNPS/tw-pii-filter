"""`/v1/messages`（Claude Code 相容性，第 3+4 步：純文字 + 工具呼叫）的整合測試。

跟 `tests/test_proxy.py` 一樣用 respx 假造上游，差別是上游這次收到的必須是
**翻譯過的 OpenAI 相容格式**，因為 AIR 不支援 Anthropic Messages API
（見 docs/B_progress.md）。capture 模式（`PII_CAPTURE_ANTHROPIC`）刻意
不在這裡測——那是暫時的開發期工具，寫完真正的轉換層後會整段刪除。
"""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, main

UPSTREAM = "https://upstream.test/v1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(main, "_CAPTURE_ANTHROPIC", False)
    with TestClient(main.app) as test_client:
        yield test_client


def _anthropic_payload(text: str, system: list | None = None) -> dict:
    return {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        "system": system or [],
        "tools": [{"name": "Read", "input_schema": {}}],  # Claude Code 一律會帶
        "stream": True,
    }


def _sse_texts(response) -> str:
    """把回應裡所有 text_delta 接回完整文字，模擬 Claude Code 端解析 SSE。"""
    text = ""
    for block in response.text.strip().split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data: "):
                continue
            obj = json.loads(line[len("data: ") :])
            if obj.get("type") == "content_block_delta":
                text += obj["delta"]["text"]
    return text


# ---------------------------------------------------------- 遮蔽：送給上游的是佔位符


@respx.mock
def test_上游收到翻譯過的_openai格式_且個資已遮蔽(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "收到"}}]}
        )
    )

    response = client.post(
        "/v1/messages", json=_anthropic_payload("客戶身分證 A123456789")
    )

    assert response.status_code == 200
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "gpt-4.1-mini"  # 不是 Claude Code 宣告的 claude-sonnet-5
    assert sent["stream"] is False
    sent_text = json.dumps(sent, ensure_ascii=False)
    assert "A123456789" not in sent_text
    assert "[TW_ID_1]" in sent_text


@respx.mock
def test_system陣列裡的個資也會被遮蔽(client):
    """真實擷取到的案例：Claude Code 的 system-reminder 裡會夾帶使用者 email。"""
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )

    client.post(
        "/v1/messages",
        json=_anthropic_payload(
            "你好",
            system=[{"type": "text", "text": "userEmail: test@example.com"}],
        ),
    )

    sent = json.loads(route.calls.last.request.content)
    system_message = sent["messages"][0]
    assert system_message["role"] == "system"
    assert "test@example.com" not in system_message["content"]
    assert "[EMAIL_1]" in system_message["content"]


# ---------------------------------------------------------- 還原：回覆包成 Anthropic SSE


@respx.mock
def test_上游回覆的佔位符會被還原並包成anthropic_sse(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "你的 [TW_ID_1] 已收到"}}]},
        )
    )

    response = client.post(
        "/v1/messages", json=_anthropic_payload("身分證 A123456789")
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert _sse_texts(response) == "你的 A123456789 已收到"


@respx.mock
def test_回覆事件序列符合anthropic格式(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    )

    response = client.post("/v1/messages", json=_anthropic_payload("hi"))

    event_lines = [
        line[len("event: ") :]
        for line in response.text.split("\n")
        if line.startswith("event: ")
    ]
    assert event_lines == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]


# ---------------------------------------------------------- 工具呼叫：遮蔽歷史裡的真值


@respx.mock
def test_歷史裡的tool_use真值會被重新遮蔽才送出上游(client):
    """assistant 的 tool_use.input 一旦被 proxy 還原過就帶著真值；Claude Code
    重送歷史時，這則訊息會再度出現在 payload 裡，proxy 必須重新遮蔽，
    不能直接轉送（見 anthropic_adapter 模組 docstring 的說明）。"""
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )

    payload = _anthropic_payload("繼續")
    payload["messages"].insert(
        0,
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Edit",
                    "input": {"old_string": "A123456789", "new_string": "已封存"},
                }
            ],
        },
    )

    client.post("/v1/messages", json=payload)

    sent_text = json.dumps(json.loads(route.calls.last.request.content), ensure_ascii=False)
    assert "A123456789" not in sent_text
    assert "[TW_ID_1]" in sent_text


@respx.mock
def test_tool_result裡的個資會被遮蔽才送出上游(client):
    """真實案例：Read 工具讀到的檔案內容可能含有真實個資（見
    docs/B_progress.md 08-11 那筆擷取記錄，customer_export.py 裡的假資料）。"""
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )

    payload = _anthropic_payload("繼續")
    payload["messages"].insert(
        0,
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '{"id_number": "A123456789"}',
                }
            ],
        },
    )

    client.post("/v1/messages", json=payload)

    sent = json.loads(route.calls.last.request.content)
    tool_message = next(m for m in sent["messages"] if m.get("role") == "tool")
    assert "A123456789" not in tool_message["content"]
    assert "[TW_ID_1]" in tool_message["content"]


def test_tools欄位會被翻譯後送給上游(client):
    with respx.mock:
        route = respx.post(f"{UPSTREAM}/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )
        client.post("/v1/messages", json=_anthropic_payload("hi"))

        sent = json.loads(route.calls.last.request.content)
        assert sent["tools"][0]["type"] == "function"
        assert sent["tools"][0]["function"]["name"] == "Read"


# ---------------------------------------------------------- 工具呼叫：還原回覆裡的真值


@respx.mock
def test_上游決定呼叫工具時回覆會被包成tool_use_block(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "Read",
                                        "arguments": '{"file_path": "a.py"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )

    response = client.post("/v1/messages", json=_anthropic_payload("讀一下 a.py"))

    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.split("\n")
        if line.startswith("data: ")
    ]
    tool_use_start = next(e for e in events if e["type"] == "content_block_start")
    assert tool_use_start["content_block"]["type"] == "tool_use"
    assert tool_use_start["content_block"]["name"] == "Read"

    message_delta = next(e for e in events if e["type"] == "message_delta")
    assert message_delta["delta"]["stop_reason"] == "tool_use"


@respx.mock
def test_tool_calls參數裡的佔位符會被還原成真值(client):
    """上游可能在工具呼叫的參數裡回傳佔位符（例如叫 Edit 把 [TW_ID_1] 換掉），
    還原邏輯沿用既有的 restorer.restore_body()，這裡驗證它對這條新路徑也有效。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": "收到"}}]}),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "function": {
                                            "name": "Edit",
                                            "arguments": json.dumps(
                                                {"old_string": "[TW_ID_1]", "new_string": "x"}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            ),
        ]
    )

    # 第一次請求建立對照：A123456789 -> [TW_ID_1]
    client.post(
        "/v1/messages", json=_anthropic_payload("客戶身分證 A123456789")
    )
    response = client.post("/v1/messages", json=_anthropic_payload("繼續"))

    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.split("\n")
        if line.startswith("data: ")
    ]
    delta = next(e for e in events if e["type"] == "content_block_delta")
    arguments = json.loads(delta["delta"]["partial_json"])
    assert arguments["old_string"] == "A123456789"


# ---------------------------------------------------------- 超出範圍：誠實回錯，不硬翻


@respx.mock
def test_出現image時回501不轉發(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )

    payload = _anthropic_payload("hi")
    payload["messages"].append(
        {
            "role": "user",
            "content": [{"type": "image", "source": {"type": "base64", "data": "..."}}],
        }
    )

    response = client.post("/v1/messages", json=payload)

    assert response.status_code == 501
    assert response.json()["type"] == "error"
    assert not route.called  # 沒有翻譯邏輯可用，不該轉發出錯誤的請求


def test_無效json回400(client):
    response = client.post(
        "/v1/messages",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["type"] == "error"


@respx.mock
def test_上游錯誤時誠實回報不假裝成功(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )

    response = client.post("/v1/messages", json=_anthropic_payload("hi"))

    assert response.status_code == 502
    assert response.json()["type"] == "error"
