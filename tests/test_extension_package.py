"""Chrome 擴充上架包的結構驗證。"""

import json
import zipfile

import pytest

from tools.pack_extension import package_dist


def _dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "icons").mkdir(parents=True)
    manifest = {
        "manifest_version": 3,
        "name": "test",
        "version": "0.1.0",
        "icons": {"128": "icons/icon-128.png"},
        "action": {"default_icon": {"16": "icons/icon-16.png"}},
    }
    (dist / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (dist / "icons" / "icon-128.png").write_bytes(b"png")
    (dist / "icons" / "icon-16.png").write_bytes(b"png")
    (dist / "content.js").write_text("// built", encoding="utf-8")
    return dist


def test_manifest在zip根目錄且圖示一併打包(tmp_path):
    out = tmp_path / "extension.zip"
    package_dist(_dist(tmp_path), out)

    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "icons/icon-128.png" in names
    assert "content.js" in names
    assert not any(name.startswith("tw-pii-filter-extension-") for name in names)


def test_manifest引用遺失圖示時拒絕產生上架包(tmp_path):
    dist = _dist(tmp_path)
    (dist / "icons" / "icon-128.png").unlink()

    with pytest.raises(FileNotFoundError, match="icon-128.png"):
        package_dist(dist, tmp_path / "extension.zip")
