"""Chrome 擴充呼叫的 localhost 語意端點。"""

from fastapi.testclient import TestClient
import pytest

from proxy import config, detector, main
from proxy.extension_api import CLIENT_HEADER, CLIENT_VALUE, MAX_TEXT_CHARS


@pytest.fixture
def local_client(monkeypatch):
    # 避免 lifespan 預熱真模型；成功案例會在 client 啟動後個別開啟。
    monkeypatch.setattr(config, "ENABLE_NER", False)
    with TestClient(main.app, client=("127.0.0.1", 50000)) as client:
        yield client


def _headers(**extra):
    return {
        "content-type": "application/json",
        CLIENT_HEADER: CLIENT_VALUE,
        "origin": "chrome-extension://unit-test-id",
        **extra,
    }


def test_extension_ner拒絕非本機連線(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_NER", False)
    with TestClient(main.app, client=("192.0.2.10", 50000)) as client:
        response = client.post("/extension/ner", headers=_headers(), json={"text": "王小明"})
    assert response.status_code == 403
    assert response.json()["detail"] == "只接受本機連線"


def test_extension_ner要求擴充識別標頭(local_client):
    response = local_client.post(
        "/extension/ner",
        headers={"content-type": "application/json", "origin": "chrome-extension://test"},
        json={"text": "王小明"},
    )
    assert response.status_code == 403


def test_extension_ner拒絕一般網頁來源(local_client):
    response = local_client.post(
        "/extension/ner",
        headers=_headers(origin="https://evil.example"),
        json={"text": "王小明"},
    )
    assert response.status_code == 403


def test_extension_ner關閉時明確回報而非假裝零結果(local_client):
    response = local_client.post(
        "/extension/ner", headers=_headers(), json={"text": "王小明"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ner_disabled"


def test_extension_ner限制輸入長度(local_client, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_NER", True)
    response = local_client.post(
        "/extension/ner",
        headers=_headers(),
        json={"text": "測" * (MAX_TEXT_CHARS + 1)},
    )
    assert response.status_code == 413


def test_extension_ner只回傳最小語意欄位(local_client, monkeypatch):
    text = "🙂王小明住在臺北市"
    monkeypatch.setattr(config, "ENABLE_NER", True)
    monkeypatch.setattr(
        detector,
        "detect_semantic",
        lambda _text: [
            {
                "start": 1,
                "end": 4,
                "type": "NAME",
                "text": "王小明",
                "confidence": 0.98,
                "source": "model",
                "replacement": "[NAME_1]",
            }
        ],
    )

    response = local_client.post(
        "/extension/ner", headers=_headers(), json={"text": text}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["offset_encoding"] == "unicode-codepoints"
    assert data["spans"] == [
        {
            "start": 1,
            "end": 4,
            "type": "NAME",
            "confidence": 0.98,
            "source": "model",
        }
    ]
    assert text not in response.text
    assert "王小明" not in response.text
