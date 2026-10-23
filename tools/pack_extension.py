"""建置瀏覽器擴充並打包成 zip，方便發給別人安裝。

    python tools/pack_extension.py

產出 `release/tw-pii-filter-extension-v<版本>.zip`（`release/` 不進 git）。
收到 zip 的人解壓縮後，到 chrome://extensions 開「開發人員模式」→
「載入未封裝項目」→ 選解壓出來的資料夾即可，**不需要裝 Node.js**。

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


def main() -> int:
    if not (EXT_DIR / "node_modules").exists():
        print("首次建置：安裝相依套件（npm ci，依 package-lock.json 鎖定版本）...")
        _npm("ci")
    _npm("run", "build")

    manifest = json.loads((DIST_DIR / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]

    RELEASE_DIR.mkdir(exist_ok=True)
    out = RELEASE_DIR / f"tw-pii-filter-extension-v{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(DIST_DIR.rglob("*")):
            if path.is_file():
                # 解壓後要直接是可載入的資料夾，所以包一層固定的資料夾名稱
                zf.write(path, Path(f"tw-pii-filter-extension-v{version}") / path.relative_to(DIST_DIR))

    print(f"\n完成：{out}（{out.stat().st_size / 1024:.1f} KB）")
    print("安裝方式：解壓縮 → chrome://extensions → 開「開發人員模式」→「載入未封裝項目」→ 選解壓出來的資料夾")
    return 0


if __name__ == "__main__":
    sys.exit(main())
