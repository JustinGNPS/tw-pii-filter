"""把 docs/使用說明書.md 轉成可列印的 PDF（docs/使用說明書.pdf）。

    python tools/build_manual.py

改了說明書的 .md 之後重跑一次，讓 PDF 跟著更新。PDF 進 git，是為了讓
「下載 ZIP 解壓縮就雙擊啟動.bat」的人不需要任何工具就能看說明書（啟動器 [4]）。

需要：
- Python 的 `markdown` 套件（Anaconda 內建；不加進 proxy/requirements.txt，
  因為只有產文件的人需要，使用者不需要）
- Microsoft Edge（Windows 內建），用它的 headless 列印功能轉 PDF
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import markdown
except ImportError:
    sys.exit("需要 markdown 套件：python -m pip install markdown")

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "docs" / "使用說明書.md"
OUT = REPO_ROOT / "docs" / "使用說明書.pdf"
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)

CSS = """
@page { size: A4; margin: 16mm 15mm; }
body { font-family: "Microsoft JhengHei", "Noto Sans TC", sans-serif; font-size: 10.5pt;
       line-height: 1.6; color: #1e293b; }
h1 { font-size: 20pt; border-bottom: 3px solid #0f766e; padding-bottom: 6px; }
h2 { font-size: 14pt; color: #0f766e; border-bottom: 1px solid #cbd5e1; padding-bottom: 3px;
     margin-top: 22px; break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 16px; break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; break-inside: avoid; font-size: 9.5pt; }
th, td { border: 1px solid #cbd5e1; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #f1f5f9; }
code { font-family: "Cascadia Mono", Consolas, monospace; font-size: 9pt; background: #f1f5f9;
       padding: 0 3px; border-radius: 3px; }
pre { background: #f8fafc; border: 1px solid #e2e8f0; padding: 8px 10px; border-radius: 5px;
      white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { margin: 8px 0; padding: 4px 12px; border-left: 4px solid #99f6e4; background: #f0fdfa; }
a { color: #0f766e; text-decoration: none; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 18px 0; }
"""


def main() -> int:
    edge = next((p for p in EDGE_CANDIDATES if p.exists()), None) or (
        Path(shutil.which("msedge")) if shutil.which("msedge") else None
    )
    if edge is None:
        sys.exit("找不到 Microsoft Edge。")

    text = SRC.read_text(encoding="utf-8")
    # PDF 裡點不到同目錄的 .md，把相對連結改成 GitHub 上的網址
    text = re.sub(
        r"\]\((?!http|#)([^)]+\.md)\)",
        r"](https://github.com/JustinGNPS/tw-pii-filter/blob/main/docs/\1)",
        text,
    )
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])
    html = (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        f"<title>使用說明書</title><style>{CSS}</style></head><body>{body}</body></html>"
    )

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "manual.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run(
            [str(edge), "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={OUT}", page.as_uri()],
            check=True,
            capture_output=True,
            timeout=120,
        )
    print(f"完成：{OUT}（{OUT.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
