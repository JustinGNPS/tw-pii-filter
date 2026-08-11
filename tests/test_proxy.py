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
    assert response.json()["mode"] == "masking"


# ---------------------------------------------------------------- 語意層預熱


def test_ner關閉時啟動不會呼叫語意層(monkeypatch):
    """預設關閉，啟動流程不該碰語意層 —— 碰了就代表會意外 import torch/transformers。"""
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_NER", False)
    calls = []
    monkeypatch.setattr(detector, "_extra_spans", lambda text: calls.append(text) or None)

    with TestClient(main.app):
        pass

    assert calls == []


def test_ner開啟時啟動會預熱一次(monkeypatch):
    """C 在 PR #11 review 指出：`core/ner/detector.py` 的單例沒上鎖，冷啟動時
    多個並行請求可能各自載入一次模型。這裡先在啟動時跑過一次，讓單例在
    第一個真實請求進來前就建好，縮小併發撞上的窗口。"""
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_NER", True)
    calls = []
    monkeypatch.setattr(detector, "_extra_spans", lambda text: calls.append(text) or None)

    with TestClient(main.app):
        pass

    assert calls == [""]  # 啟動時預熱呼叫了一次，且不需要真的送文字進去


# ---------------------------------------------------------------- 透明轉發


@respx.mock
def test_上游收到的是遮蔽後的內容(client):
    """第二版的核心驗收條件：真實個資不得離開這台機器。"""
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-1"})
    )
    payload = {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": "客戶身分證 A123456789"}],
    }

    response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    sent = route.calls.last.request.content.decode("utf-8")
    assert "A123456789" not in sent  # 真值沒有送出去
    assert "[TW_ID_1]" in sent  # 送出去的是佔位符
    assert json.loads(sent)["model"] == "gpt-4.1-mini"  # 其餘欄位不受影響


@respx.mock
def test_沒有個資時_body_原樣轉發(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )
    payload = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "你好"}]}

    client.post("/v1/chat/completions", json=payload)

    assert json.loads(route.calls.last.request.content) == payload


@respx.mock
def test_回覆裡的佔位符會被換回真值(client):
    """端到端：遮蔽 → 上游回覆帶佔位符 → 還原 → agent 看到真值。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"id": "1"}),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "你說的 [TW_ID_1] 我看到了"}}]},
            ),
        ]
    )

    # 第一次請求建立對照：A123456789 -> [TW_ID_1]
    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "身分證 A123456789"}]},
    )
    # 第二次請求的回覆帶著佔位符
    response = client.post("/v1/chat/completions", json={"messages": []})

    content = response.json()["choices"][0]["message"]["content"]
    assert content == "你說的 A123456789 我看到了"


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


@respx.mock
def test_HEAD請求也接得住(client):
    """Claude Code CLI 啟動時會用 HEAD 做連線探測（實測 HEAD /api/hello），
    catch-all 原本只接 GET/POST/PUT/PATCH/DELETE，漏了 HEAD 會白白回
    405，即使不影響後續請求也不該讓這種探測性請求平白出錯。"""
    route = respx.head(f"{UPSTREAM}/api/hello").mock(return_value=httpx.Response(200))

    response = client.request("HEAD", "/api/hello")

    assert response.status_code == 200
    assert route.called


# ---------------------------------------------------------------- SSE 串流


@respx.mock
def test_沒有對照表時_SSE_原樣穿透(client):
    """對照表是空的就不必重新序列化，維持位元組層級的透明。"""
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


@respx.mock
def test_SSE_串流會還原被切成兩半的佔位符(client):
    """最關鍵的整合測試：跨事件的佔位符要能接回來。"""
    stream = (
        b'data: {"choices":[{"delta":{"content":"\\u4f60\\u7684 [TW"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"_ID_1] \\u5df2\\u6536\\u5230"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"id": "1"}),
            httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream
            ),
        ]
    )

    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "身分證 A123456789"}]},
    )
    response = client.post(
        "/v1/chat/completions", json={"messages": [], "stream": True}
    )

    # 模擬 agent：把所有 delta 拼回完整文字
    text = ""
    for line in response.content.decode("utf-8").split("\n"):
        if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
            continue
        for choice in json.loads(line[6:]).get("choices", []):
            text += choice.get("delta", {}).get("content", "")

    assert text == "你的 A123456789 已收到"


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
