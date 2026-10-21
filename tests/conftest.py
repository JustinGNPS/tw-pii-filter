"""全部測試共用的設定。"""

import pytest

from proxy import settings


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    """proxy 啟動時會套用設定介面的存檔（repo 根目錄的 .pii_settings.json）。

    開發者自己在介面上改過設定的話，那份檔案會讓測試結果跟著變 —— 測試一律
    指向一個不存在的暫存路徑，確保每次都從 .env／預設值開始。
    """
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "pii_settings.json")
