"""Proxy 測試。

用 respx 假造上游 LLM，**不會打真實 API、不需要金鑰**，因此可以安全地在 CI
與任何組員的機器上跑。

第一版的核心驗收條件：**轉發必須完全透明** —— 上游收到的 body 要跟 agent
送出的 body 位元組完全相同，即使裡面有個資（第一版只警告不遮蔽）。
"""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, detector, main

UPSTREAM = "https://upstream.test/v1"


@pytest.fixture
def client(monkeypatch):
    """指向假上游的 TestClient。金鑰也是假的，不碰真實 .env 設定。"""
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    with TestClient(main.app) as test_client:
        yield test_client


# ---------------------------------------------------------------- 健康檢查


def test_healthz_不會轉發到上游(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mode"] == "transparent"


# ---------------------------------------------------------------- 透明轉發


@respx.mock
def test_轉發後上游收到的_body_與送出的完全相同(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-1"})
    )
    payload = {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": "客戶身分證 A123456789"}],
    }

    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    assert response.json() == {"id": "chatcmpl-1"}
    # 第一版只警告不遮蔽：個資必須原樣送出去
    assert json.loads(route.calls.last.request.content) == payload


@respx.mock
def test_金鑰被換成_proxy_自己的(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )

    client.post(
        "/v1/chat/completions",
        json={"messages": []},
        headers={"authorization": "Bearer agent-fake-key"},  # HTTP 標頭只能是 ASCII
    )

    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
def test_上游的錯誤狀態碼原樣傳回(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "no"}})
    )

    response = client.post("/v1/chat/completions", json={"messages": []})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "no"


@respx.mock
def test_query_參數會被帶到上游(client):
    route = respx.get(f"{UPSTREAM}/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    client.get("/v1/models?limit=5")

    assert route.calls.last.request.url.params["limit"] == "5"


@respx.mock
def test_沒有_v1_前綴的路徑也接得住(client):
    """有些 agent 的 base URL 不帶 /v1。"""
    route = respx.get(f"{UPSTREAM}/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    response = client.get("/models")

    assert response.status_code == 200
    assert route.called


# ---------------------------------------------------------------- SSE 串流


@respx.mock
def test_SSE_串流原樣穿透(client):
    body = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    response = client.post(
        "/v1/chat/completions", json={"messages": [], "stream": True}
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert response.content == body


# ---------------------------------------------- detector：payload 欄位萃取


def test_萃取_messages_的字串_content():
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    assert detector.extract_texts(payload) == [(("messages", 0, "content"), "hi")]


def test_萃取多模態_content_parts():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "第一段"},
                    {"type": "image_url", "image_url": {"url": "http://x"}},
                    {"type": "text", "text": "第二段"},
                ],
            }
        ]
    }
    assert detector.extract_texts(payload) == [
        (("messages", 0, "content", 0, "text"), "第一段"),
        (("messages", 0, "content", 2, "text"), "第二段"),
    ]


def test_萃取_tool_call_參數():
    """agent 讀檔的結果常以 tool call 參數的形式回到對話歷史裡。"""
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "a.py"}',
                        }
                    }
                ],
            }
        ]
    }
    assert detector.extract_texts(payload) == [
        (
            ("messages", 0, "tool_calls", 0, "function", "arguments"),
            '{"path": "a.py"}',
        )
    ]


def test_萃取_prompt_與_input_的字串陣列():
    assert detector.extract_texts({"prompt": ["a", "b"]}) == [
        (("prompt", 0), "a"),
        (("prompt", 1), "b"),
    ]
    assert detector.extract_texts({"input": "c"}) == [(("input",), "c")]


def test_萃取遇到壞掉的結構不會爆炸():
    assert detector.extract_texts({"messages": "不是陣列"}) == []
    assert detector.extract_texts({"messages": [None, 42]}) == []
    assert detector.extract_texts("根本不是 dict") == []


def test_set_at_能照路徑寫回去():
    """第二版做遮蔽時要靠這條路徑把替換後的文字寫回原位。"""
    payload = {"messages": [{"role": "user", "content": "原文"}]}
    path, _ = detector.extract_texts(payload)[0]
    detector.set_at(payload, path, "遮蔽後")
    assert payload["messages"][0]["content"] == "遮蔽後"
    assert detector.get_at(payload, path) == "遮蔽後"


# ---------------------------------------------- detector：偵測與摘要


def test_掃描整包_payload_能找出個資():
    payload = {
        "messages": [
            {"role": "system", "content": "你是助理"},
            {"role": "user", "content": "客戶 A123456789 的資料"},
        ]
    }

    results = detector.scan_payload(payload)

    assert len(results) == 1  # system message 沒有個資
    assert results[0]["path"] == ("messages", 1, "content")
    assert [span["type"] for span in results[0]["spans"]] == ["TW_ID"]


def test_摘要只有型別與筆數_不含原始個資():
    payload = {
        "messages": [
            {"role": "user", "content": "A123456789 與 F131104093 和 0912345678"}
        ]
    }

    counts = detector.summarize(detector.scan_payload(payload))
    warning = detector.format_warning(counts)

    assert counts["TW_ID"] == 2
    assert counts["TW_PHONE_M"] == 1
    # 警告訊息會被寫進 log，絕不能帶出原始個資
    assert "A123456789" not in warning
    assert "0912345678" not in warning
    assert "3 筆" in warning


def test_沒偵測到東西時不產生警告():
    assert detector.format_warning({}) == ""
    assert detector.scan_payload({"messages": [{"role": "user", "content": "hi"}]}) == []


def test_spans_保證不重疊_layer4_由_A_負責():
    """A 的 detect_all() 內部已做重疊仲裁，proxy 不需要自己再仲裁一次。"""
    spans = detector.detect("聯絡 a0912345678@gmail.com")["spans"]

    assert [span["type"] for span in spans] == ["EMAIL"]  # 內含的手機號被丟掉
    for earlier, later in zip(spans, spans[1:]):
        assert earlier["end"] <= later["start"]
