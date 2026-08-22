"""上游「設了但連不上」時的行為。

`tests/test_upstream_config.py` 守的是 base URL **沒設定**；這裡守的是設了、
但連線失敗或逾時。兩者是同一族問題（agent 收到沒有線索的 500），只是原因不同：

- 沒設定 -> 連 URL 都組不出來，在 `upstream_url()` 就擋掉
- 連不上 -> URL 是對的，`httpx` 在送出時丟 `RequestError`

實務上第二種更常見：位址打錯、校內服務要先連 VPN、上游掛掉、逾時。
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, main

UPSTREAM = "https://upstream.test/v1"

_PAYLOAD = {
    "model": "gpt-4.1-mini",
    "messages": [{"role": "user", "content": "hi"}],
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(main, "_CAPTURE_ANTHROPIC", False)
    with TestClient(main.app) as test_client:
        yield test_client


@respx.mock
def test_連不上上游回502而不是無訊息的500(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    )

    response = client.post("/v1/chat/completions", json=_PAYLOAD)

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "upstream_unreachable"
    assert UPSTREAM in error["message"]


@respx.mock
def test_逾時回504而不是502(client):
    """連得到只是太慢，跟根本連不上是不同狀況 —— 前者值得重試。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )

    response = client.post("/v1/chat/completions", json=_PAYLOAD)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


@respx.mock
def test_逾時訊息講得出目前的秒數設定(client, monkeypatch):
    """使用者要能直接看出「是不是自己把逾時設太短」。"""
    monkeypatch.setattr(config, "CONNECT_TIMEOUT", 3.0)
    monkeypatch.setattr(config, "READ_TIMEOUT", 42.0)
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )

    message = client.post("/v1/chat/completions", json=_PAYLOAD).json()["error"]["message"]

    assert "3" in message and "42" in message
    assert "PROXY_READ_TIMEOUT" in message


@respx.mock
def test_原始錯誤訊息有保留下來(client):
    """中文說明是給人看的，原始英文訊息是拿來查問題的，兩個都要有。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    )

    message = client.post("/v1/chat/completions", json=_PAYLOAD).json()["error"]["message"]

    assert "getaddrinfo failed" in message


@respx.mock
def test_anthropic_路徑也擋得下來(client):
    """轉發有兩條路徑，處理器掛 app 層級就是為了兩條都涵蓋。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=httpx.ConnectError("nope")
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "system": [],
            "tools": [{"name": "Read", "input_schema": {}}],
            "stream": True,
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unreachable"


@respx.mock
def test_遮蔽仍然照做_不因為連不上就跳過(client, monkeypatch):
    """個資不能因為轉發失敗就漏掃 —— 守門在遮蔽之後，順序不該反過來。"""
    masked = {}
    original = main.masker.mask_payload_with_risk

    def spy(payload, table):
        result = original(payload, table)
        masked["counts"] = result[0]
        return result

    monkeypatch.setattr(main.masker, "mask_payload_with_risk", spy)
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        side_effect=httpx.ConnectError("nope")
    )

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "我的身分證是 A123456789"}],
        },
    )

    assert response.status_code == 502
    assert masked["counts"].get("TW_ID") == 1
