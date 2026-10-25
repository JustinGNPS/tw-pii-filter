"""預設模型的啟動檢查，以及上游拒絕時錯誤訊息要帶上原因。

2026-10-25 實測：學校端點下架 gpt-4.1 系列，當時的預設值 gpt-4.1-mini 讓 Claude Code
每個請求都失敗，畫面上只有「上游拒絕請求（400）」，看不出是模型不存在。
"""

import asyncio
import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, main

UPSTREAM = "https://upstream.test/v1"


@pytest.fixture(autouse=True)
def _upstream(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "gpt-oss-120b")


def _run_check() -> None:
    async def go():
        async with httpx.AsyncClient() as client:
            await main._check_default_model(client)

    asyncio.run(go())


def _models(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": i} for i in ids]})


# ── 啟動檢查 ─────────────────────────────────────────────────────


@respx.mock
def test_模型存在_只印確認不警告(caplog):
    respx.get(f"{UPSTREAM}/models").mock(return_value=_models("gpt-oss-120b", "gemma-4"))

    with caplog.at_level(logging.INFO, logger="proxy"):
        _run_check()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "有提供 gpt-oss-120b" in caplog.text


@respx.mock
def test_模型不存在_警告並列出可用模型(caplog):
    respx.get(f"{UPSTREAM}/models").mock(return_value=_models("gemma-4", "mistral-small-4"))

    with caplog.at_level(logging.INFO, logger="proxy"):
        _run_check()

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "gpt-oss-120b" in warnings[0]
    assert "gemma-4" in warnings[0] and "mistral-small-4" in warnings[0]
    assert "DEFAULT_MODEL" in warnings[0]


@respx.mock
def test_查詢用的是proxy自己的金鑰(caplog):
    route = respx.get(f"{UPSTREAM}/models").mock(return_value=_models("gpt-oss-120b"))

    _run_check()

    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
@pytest.mark.parametrize(
    "mock",
    [
        {"return_value": httpx.Response(404)},
        {"return_value": httpx.Response(200, text="not json")},
        {"side_effect": httpx.ConnectError("getaddrinfo failed")},
    ],
    ids=["端點不提供", "回應不是JSON", "連不上"],
)
def test_查詢失敗_只印提示不警告也不丟例外(caplog, mock):
    respx.get(f"{UPSTREAM}/models").mock(**mock)

    with caplog.at_level(logging.INFO, logger="proxy"):
        _run_check()  # 不丟例外

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "略過模型檢查" in caplog.text


@respx.mock
def test_啟動時會在背景跑檢查(monkeypatch, caplog):
    monkeypatch.setattr(config, "CHECK_MODEL_ON_START", True)
    monkeypatch.setattr(main, "_CAPTURE_ANTHROPIC", False)
    route = respx.get(f"{UPSTREAM}/models").mock(return_value=_models("other-model"))

    with caplog.at_level(logging.INFO, logger="proxy"):
        with TestClient(main.app) as client:
            client.get("/healthz")  # 讓事件迴圈跑一下，背景檢查才有機會執行

    assert route.called
    assert "沒有提供 gpt-oss-120b" in caplog.text


def test_設定關閉時啟動不連上游(monkeypatch):
    monkeypatch.setattr(config, "CHECK_MODEL_ON_START", False)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{UPSTREAM}/models").mock(return_value=_models("gpt-oss-120b"))
        with TestClient(main.app) as client:
            client.get("/healthz")
    assert not route.called


# ── 上游拒絕時的錯誤訊息 ─────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_CAPTURE_ANTHROPIC", False)
    with TestClient(main.app) as test_client:
        yield test_client


_CLAUDE_CODE_REQUEST = {
    "model": "claude-x",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "hi"}],
}


@respx.mock
@pytest.mark.parametrize("stream", [True, False], ids=["串流", "非串流"])
def test_Claude_Code看得到上游拒絕的原因(client, stream):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Model 'gpt-4.1-mini' is not available on this gateway",
                            "code": "model_not_found"}},
        )
    )

    response = client.post("/v1/messages", json={**_CLAUDE_CODE_REQUEST, "stream": stream})

    assert response.status_code == 502
    message = response.json()["error"]["message"]
    assert "400" in message
    assert "is not available" in message
    assert "DEFAULT_MODEL" in message  # 跟模型有關時，指出該改哪裡


@respx.mock
def test_上游回的不是JSON時_訊息仍可讀(client):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(401, text="<html>Unauthorized</html>")
    )

    response = client.post("/v1/messages", json={**_CLAUDE_CODE_REQUEST, "stream": False})

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "上游拒絕請求（401）"

