"""建置瀏覽器擴充並打包成 Chrome Web Store 可上傳的 zip。

    python tools/pack_extension.py

產出 `release/tw-pii-filter-extension-v<版本>-chrome-web-store.zip`
（`release/` 不進 git）。zip 根目錄直接是 manifest.json，既能上傳 Chrome
Web Store，也能解壓後用「載入未封裝項目」測試，**不需要裝 Node.js**。

版本號取自 `extension/public/manifest.json`，不另外維護一份，避免兩邊對不起來。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "extension"
DIST_DIR = EXT_DIR / "dist"
RELEASE_DIR = REPO_ROOT / "release"


def _npm(*args: str) -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("找不到 npm。請先安裝 Node.js（https://nodejs.org/ 的 LTS 版）。")
    subprocess.run([npm, *args], cwd=EXT_DIR, check=True)


def package_dist(dist_dir: Path, out: Path) -> None:
    """把已建置的 dist 打成 manifest 位於 zip 根目錄的上架包。"""
    manifest_path = dist_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"找不到擴充 manifest：{manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = [*manifest.get("icons", {}).values()]
    action_icon = manifest.get("action", {}).get("default_icon", {})
    if isinstance(action_icon, dict):
        required.extend(action_icon.values())
    missing = sorted({item for item in required if not (dist_dir / item).is_file()})
    if missing:
        raise FileNotFoundError(f"manifest 引用的圖示不存在：{', '.join(missing)}")

    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(dist_dir))


def main() -> int:
    if not (EXT_DIR / "node_modules").exists():
        print("首次建置：安裝相依套件（npm ci，依 package-lock.json 鎖定版本）...")
        _npm("ci")
    _npm("run", "build")

    manifest = json.loads((DIST_DIR / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]

    RELEASE_DIR.mkdir(exist_ok=True)
    out = RELEASE_DIR / f"tw-pii-filter-extension-v{version}-chrome-web-store.zip"
    package_dist(DIST_DIR, out)

    print(f"\n完成：{out}（{out.stat().st_size / 1024:.1f} KB）")
    print("上架：把這個 zip 直接上傳 Chrome Web Store Developer Dashboard")
    print("本機測試：解壓縮 → chrome://extensions → 開「開發人員模式」→「載入未封裝項目」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
