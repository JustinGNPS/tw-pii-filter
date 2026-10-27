"""全部測試共用的設定。"""

import pytest

from proxy import config, settings


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    """proxy 啟動時會套用設定介面的存檔（repo 根目錄的 .pii_settings.json）。

    開發者自己在介面上改過設定的話，那份檔案會讓測試結果跟著變 —— 測試一律
    指向一個不存在的暫存路徑，確保每次都從 .env／預設值開始。
    """
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "pii_settings.json")


@pytest.fixture(autouse=True)
def _no_startup_model_check(monkeypatch):
    """proxy 啟動時會向上游查模型清單（見 proxy/main.py `_check_default_model`）。

    測試裡每個 TestClient 都會觸發啟動，關掉以免測試去連網路。
    這個檢查本身的測試在 tests/test_model_check.py，直接呼叫函式。
    """
    monkeypatch.setattr(config, "CHECK_MODEL_ON_START", False)
