"""「流量有沒有真的走 proxy」的可見度（`proxy/traffic.py`）。

這組測試守的是一個**安全性質**而不是功能：agent 沒指到 proxy 時，
使用者必須有辦法發現。2026-08-22 實測時 Codex 因為設定沒吃到而整包繞過
proxy，當下沒有任何跡象 —— 那次是靠上游回 401 才被發現，如果金鑰剛好是
對的就什麼都不會發生。
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, main, traffic

UPSTREAM = "https://upstream.test/v1"


@pytest.fixture(autouse=True)
def clean_stats():
    """模組層單例會跨測試累加，每個測試都從零開始。"""
    traffic.STATS.reset()
    yield
    traffic.STATS.reset()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(main, "_CAPTURE_ANTHROPIC", False)
    with TestClient(main.app) as test_client:
        yield test_client


# ---------------------------------------------------------------- 計數本身


def test_只有第一次_record_回傳_True():
    """回傳值決定要不要印訊息：每輪都印會把 log 洗掉。"""
    stats = traffic.TrafficStats()

    assert stats.record() is True
    assert stats.record() is False
    assert stats.record() is False
    assert stats.count == 3


def test_還沒收到請求時_snapshot_是空的():
    snapshot = traffic.TrafficStats().snapshot()

    assert snapshot["requests"] == 0
    assert snapshot["first_at"] is None
    assert snapshot["last_at"] is None
    assert snapshot["seconds_since_last"] is None


def test_snapshot_帶得出時間():
    stats = traffic.TrafficStats()
    stats.record()

    snapshot = stats.snapshot()
    assert snapshot["requests"] == 1
    assert snapshot["first_at"] is not None
    assert snapshot["seconds_since_last"] >= 0


# ---------------------------------------------------------------- 接上路由


@respx.mock
def test_轉發過的請求會被計入(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert client.get("/healthz").json()["traffic"]["requests"] == 1


def test_healthz_自己不計入(client):
    """使用者自己 curl 健康檢查不代表 agent 走了 proxy。

    把它算進去，這個數字就失去意義了 —— 永遠不會是 0，也就永遠無法用來
    判斷「agent 到底有沒有連過來」。
    """
    client.get("/healthz")
    client.get("/healthz")

    assert client.get("/healthz").json()["traffic"]["requests"] == 0


@respx.mock
def test_anthropic_路徑也計入(client):
    """`/v1/messages` 走的是另一條路徑，不經過 `_proxy()`，容易漏掉。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )

    client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "system": [],
            "tools": [{"name": "Read", "input_schema": {}}],
            "stream": False,
        },
    )

    assert client.get("/healthz").json()["traffic"]["requests"] == 1


@respx.mock
def test_上游失敗的請求仍然計入(client):
    """使用者要回答的是「流量有沒有經過這裡」，不是「上游健不健康」。

    上游 401 的請求同樣代表 agent 有連過來 —— 不計入的話，正好在最需要
    診斷的情況下失去線索。
    """
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "Invalid API key"})
    )

    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert client.get("/healthz").json()["traffic"]["requests"] == 1
