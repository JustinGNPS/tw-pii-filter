"""上游 base URL 未設定時的行為。

這條路徑是 PR #32（公開前置）把寫死的預設值拿掉之後才出現的：base URL 從
「一定有值」變成「可能是空字串」。空字串會讓 `upstream_url()` 接出
`/v1/chat/completions` 這種**相對路徑**，而 httpx 的 `build_request()`
對相對路徑不會報錯 —— 要到 `send()` 才丟 `UnsupportedProtocol`。也就是說
不擋的話，agent 收到的是一個沒有任何線索的 500，看不出問題出在自己沒設 `.env`。

repo 要轉 public，新使用者 clone 下來沒設定 `.env` 就會直接走到這條路上。
"""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, forward, main


@pytest.fixture
def unconfigured_client(monkeypatch):
    """base URL 未設定（PR #32 之後的預設狀態）。"""
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", "")
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(main, "_CAPTURE_ANTHROPIC", False)
    with TestClient(main.app) as test_client:
        yield test_client


# ---------------------------------------------------------------- 守門本身


def test_未設定時_upstream_url_直接擋下來():
    """不能讓相對路徑流到 httpx —— 那是靜默失效，要到 send() 才炸。"""
    original = config.UPSTREAM_BASE_URL
    config.UPSTREAM_BASE_URL = ""
    try:
        with pytest.raises(forward.UpstreamNotConfigured):
            forward.upstream_url("chat/completions")
    finally:
        config.UPSTREAM_BASE_URL = original


def test_有設定時行為不變(monkeypatch):
    """守門不該改動正常路徑（含開頭斜線的容錯）。"""
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", "https://upstream.test/v1")

    assert forward.upstream_url("chat/completions") == (
        "https://upstream.test/v1/chat/completions"
    )
    assert forward.upstream_url("/chat/completions") == (
        "https://upstream.test/v1/chat/completions"
    )


def test_錯誤訊息講得出該設哪個變數():
    """訊息是給人看的：要指名變數與檔案，不然使用者只知道壞了、不知道怎麼修。"""
    message = forward.MISSING_UPSTREAM_MESSAGE

    assert "UPSTREAM_BASE_URL" in message
    assert ".env" in message


# ---------------------------------------------------------------- 兩條轉發路徑


@respx.mock
def test_未設定時_openai_路徑回502而不是無訊息的500(unconfigured_client):
    upstream = respx.route(host="upstream.test").mock(
        return_value=httpx.Response(200, json={})
    )

    response = unconfigured_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "upstream_not_configured"
    assert "UPSTREAM_BASE_URL" in error["message"]
    # 沒設定就不該有任何對外連線發生
    assert not upstream.called


@respx.mock
def test_未設定時_anthropic_路徑也擋得下來(unconfigured_client):
    """轉發有兩條路徑，處理器掛在 app 層級就是為了兩條都涵蓋。"""
    upstream = respx.route(host="upstream.test").mock(
        return_value=httpx.Response(200, json={})
    )

    response = unconfigured_client.post(
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
    assert response.json()["error"]["code"] == "upstream_not_configured"
    assert not upstream.called


def test_未設定時_healthz_仍然可用(unconfigured_client):
    """診斷端點不該跟著壞 —— 使用者正是要靠它看出 upstream 是空的。"""
    response = unconfigured_client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["upstream"] == ""


def test_遮蔽仍然照做_不因為沒設上游就跳過(unconfigured_client, monkeypatch):
    """個資不能因為轉發失敗就漏掃：遮蔽在轉發之前，順序不該反過來。

    這裡驗的是「擋下來的位置對不對」—— 守門若放在遮蔽之前，日後有人改成
    fallback 直連上游時，就會是一個沒被遮蔽的請求直接送出去。
    """
    masked = {}
    original = main.masker.mask_payload_with_risk

    def spy(payload, table):
        result = original(payload, table)
        masked["counts"] = result[0]
        return result

    monkeypatch.setattr(main.masker, "mask_payload_with_risk", spy)

    response = unconfigured_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "我的身分證是 A123456789"}],
        },
    )

    assert response.status_code == 502
    assert masked["counts"].get("TW_ID") == 1
