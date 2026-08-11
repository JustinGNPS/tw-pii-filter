"""`/v1/messages`（Claude Code 相容性，第 3 步：純文字最小遮蔽）的整合測試。

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


# ---------------------------------------------------------- 超出範圍：誠實回錯，不硬翻


@respx.mock
def test_出現tool_use時回501不轉發(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )

    payload = _anthropic_payload("hi")
    payload["messages"].append(
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
        }
    )

    response = client.post("/v1/messages", json=payload)

    assert response.status_code == 501
    assert response.json()["type"] == "error"
    assert not route.called  # 沒有翻譯邏輯可用，不該轉發出錯誤的請求


@respx.mock
def test_出現tool_result時回501不轉發(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )

    payload = _anthropic_payload("hi")
    payload["messages"].append(
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
        }
    )

    response = client.post("/v1/messages", json=payload)

    assert response.status_code == 501
    assert not route.called


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
