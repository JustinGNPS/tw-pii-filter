"""proxy/settings.py 與 /demo/settings：執行中調整設定。"""

import json
import sys
import types

import pytest
from fastapi.testclient import TestClient

from core.redact.mapping import MappingTable
from proxy import config, main, masker, settings


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    """settings.apply() 會直接改 config 的屬性；先登記原值，測試結束時還原。"""
    for name in ("SKIP_TYPES", "ENABLE_RISK_WARNING", "RISK_REDUCTION_POLICY",
                 "IMAGE_POLICY", "DOCUMENT_POLICY", "ENABLE_NER"):
        monkeypatch.setattr(config, name, getattr(config, name))
    monkeypatch.setattr(config, "ENABLE_DEMO", True)
    monkeypatch.setattr(settings, "_ner_loading", False)
    monkeypatch.setattr(settings, "_ner_desired_enabled", config.ENABLE_NER)


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


JSON = {"content-type": "application/json"}


def test_預設狀態_職稱公司不遮其餘都遮():
    state = settings.current()
    assert "TW_ID" in state["mask_types"] and "NAME" in state["mask_types"]
    assert "POSITION" not in state["mask_types"] and "COMPANY" not in state["mask_types"]


def test_取消勾選身分證後立即不遮():
    settings.apply({"mask_types": sorted(settings.ALL_TYPES - {"TW_ID", "POSITION", "COMPANY"})}, persist=False)
    payload = {"messages": [{"role": "user", "content": "A123456789 0912345678"}]}
    masker.mask_payload_with_risk(payload, MappingTable())
    content = payload["messages"][0]["content"]
    assert "A123456789" in content  # 使用者選擇不遮
    assert "0912345678" not in content  # 其他照遮


def test_不在介面清單裡的自訂跳過型別保留不動(monkeypatch):
    monkeypatch.setattr(config, "SKIP_TYPES", frozenset({"POSITION", "COMPANY", "CUSTOM_X"}))
    settings.apply({"mask_types": sorted(settings.ALL_TYPES)}, persist=False)
    assert config.SKIP_TYPES == frozenset({"CUSTOM_X"})


def test_任何一個值不合法就整批不套用():
    before = settings.current()
    with pytest.raises(ValueError):
        settings.apply({"image_policy": "allow", "risk_reduction_policy": "亂打"}, persist=False)
    assert config.IMAGE_POLICY == before["image_policy"]


@pytest.mark.parametrize(
    "bad",
    [{"mask_types": ["NOT_A_TYPE"]}, {"mask_types": "TW_ID"}, {"risk_warning": "yes"}, {"unknown_key": 1}],
)
def test_各種不合法輸入(bad):
    with pytest.raises(ValueError):
        settings.apply(bad, persist=False)


def test_存檔再載入_語意層開關不存():
    settings.apply({"image_policy": "allow", "risk_reduction_policy": "balanced"})
    saved = json.loads(settings.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert saved["image_policy"] == "allow"
    assert "ner_enabled" not in saved  # 理由見 proxy/settings.py 模組說明

    config.IMAGE_POLICY = "remove"
    config.RISK_REDUCTION_POLICY = "off"
    assert settings.load_saved() is True
    assert config.IMAGE_POLICY == "allow" and config.RISK_REDUCTION_POLICY == "balanced"


def test_存檔壞掉不會讓proxy起不來():
    settings.SETTINGS_FILE.write_text("{這不是 json", encoding="utf-8")
    assert settings.load_saved() is False


def test_reset刪掉存檔():
    settings.apply({"image_policy": "allow"})
    settings.reset()
    assert not settings.SETTINGS_FILE.exists()


# ── 端點與防護 ───────────────────────────────────────────────────


def test_示範頁送出的設定會套用(client):
    response = client.post(
        "/demo/settings",
        content=json.dumps({"image_policy": "allow"}),
        headers={**JSON, "origin": "http://127.0.0.1:8000", "sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 200
    assert config.IMAGE_POLICY == "allow"


def test_別的網站送來的請求擋下_CSRF(client):
    response = client.post(
        "/demo/settings",
        content=json.dumps({"mask_types": []}),
        headers={**JSON, "origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert "TW_ID" in settings.current()["mask_types"]  # 遮蔽沒有被關掉


def test_只帶Origin的跨站請求也擋(client):
    response = client.post(
        "/demo/settings", content=json.dumps({"mask_types": []}), headers={**JSON, "origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_非JSON請求擋下_避開CORS預檢的那種(client):
    # text/plain 是「簡單請求」，跨站也不會觸發預檢，所以一定要擋
    response = client.post("/demo/settings", content='{"mask_types": []}', headers={"content-type": "text/plain"})
    assert response.status_code == 415


def test_不合法的值回400(client):
    response = client.post("/demo/settings", content=json.dumps({"image_policy": "x"}), headers=JSON)
    assert response.status_code == 400


def test_示範頁沒開時設定端點不存在(client, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_DEMO", False)
    assert client.get("/demo/settings").status_code == 404
    assert client.post("/demo/settings", content="{}", headers=JSON).status_code == 404


def test_語意層載入期間關閉_完成後仍維持關閉(monkeypatch):
    """回歸：開啟後立刻關閉時，舊的背景工作不得把開關重新打開。"""
    jobs = []

    class DeferredThread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target

        def start(self):
            jobs.append(self.target)

    fake_detector = types.ModuleType("core.ner.detector")
    fake_detector.detect_ner = lambda _text: []
    monkeypatch.setitem(sys.modules, "core.ner.detector", fake_detector)
    monkeypatch.setattr(settings, "ner_available", lambda: True)
    monkeypatch.setattr(settings.threading, "Thread", DeferredThread)
    monkeypatch.setattr(config, "ENABLE_NER", False)

    settings.apply({"ner_enabled": True}, persist=False)
    assert settings.current()["ner_loading"] is True
    settings.apply({"ner_enabled": False}, persist=False)

    jobs.pop()()
    assert config.ENABLE_NER is False
    assert settings.current()["ner_loading"] is False
