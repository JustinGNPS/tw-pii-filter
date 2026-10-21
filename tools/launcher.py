"""一鍵啟動器（由 repo 根目錄的「啟動.bat」呼叫）。

給「不想記指令」的人用：決賽現場、組員、教授試用。雙擊後出現選單：

  [1] 啟動 Proxy + 示範頁   [2] 安裝或更新瀏覽器擴充
  [3] 開啟線上 Demo 網站     [4] 開啟使用說明書

帶任何參數執行（例如 `啟動.bat --port 8010`）就跳過選單、直接走 [1]。

[1] 做的事依序是：

1. 沒有 `.env` 就從 `tools/env.template` 複製一份（**從不讀取或印出 .env 的內容**）
2. 偵測語意層套件有沒有裝，有裝就問要不要開；沒裝就問要不要現在裝
3. 檢查埠號：被自己的 proxy 佔用就直接開瀏覽器，被別的程式佔用就報錯
4. 啟動 uvicorn（`PII_ENABLE_DEMO=1`），等 `/healthz` 回應後自動開示範頁面
5. 印出「怎麼讓 agent 走 proxy」的指令（cmd.exe 語法）

刻意**不**做的事：
- 不改 proxy 本身的任何預設值。這裡只設環境變數，行為與手動下指令啟動完全一樣，
  避免「用啟動器跑跟用指令跑結果不同」這種最難查的問題。
- 不綁 0.0.0.0。只綁 127.0.0.1，示範頁面與對照表不對區網開放。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ENV_TEMPLATE = REPO_ROOT / "tools" / "env.template"
NER_REQUIREMENTS = REPO_ROOT / "core" / "ner" / "requirements.txt"
HOST = "127.0.0.1"
EXT_DIR = REPO_ROOT / "extension"
EXT_DIST = EXT_DIR / "dist"
DEMO_SITE_URL = "https://justingnps.github.io/tw-pii-filter/"
# 說明書優先開 PDF（Windows 上 .md 多半會用記事本開，排版全失）
MANUAL_CANDIDATES = (REPO_ROOT / "docs" / "使用說明書.pdf", REPO_ROOT / "docs" / "使用說明書.md")

# 語意層第一次載入要讀 389 MB 權重；首次執行還可能要下載。給足時間。
READY_TIMEOUT_SECONDS = 300


def _print_header() -> None:
    print("=" * 60)
    print("  本地端敏感資訊過濾機制 ── 私密大師")
    print("=" * 60)


def _ensure_env_file() -> None:
    """沒有 .env 就從範本複製。只檢查存在與否，不讀內容。"""
    if ENV_FILE.exists():
        return
    shutil.copyfile(ENV_TEMPLATE, ENV_FILE)
    print(f"\n[設定] 找不到 .env，已從範本建立：{ENV_FILE}")
    print("       只用示範頁面的話不用填；要接 AI agent 再填 UPSTREAM_BASE_URL 與 UPSTREAM_API_KEY。")


def _ner_installed() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))


def _ask_yes_no(question: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes", "是", "要")


def _decide_ner(flag: bool | None) -> bool:
    """決定要不要開語意層。命令列有指定就照做，沒指定才問。"""
    installed = _ner_installed()
    if flag is False:
        return False
    if flag is True and installed:
        return True

    if installed:
        print("\n[語意層] 已安裝（可辨識姓名、地址、職稱、公司名）。")
        print("         開啟後啟動多約 10~30 秒，每次偵測多約 50~800 ms（隨文字長度）。")
        return _ask_yes_no("         要開啟語意層嗎？", default=True)

    print("\n[語意層] 尚未安裝。沒有它只能辨識身分證、統編、電話等固定格式的個資，")
    print("         認不出姓名與地址。安裝要下載約 1 GB 套件，模型另外約 400 MB。")
    if not _ask_yes_no("         現在安裝嗎？（可以之後再裝）", default=False):
        return False
    print("\n安裝中，請稍候（時間取決於網路速度）...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "-r", str(NER_REQUIREMENTS)],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print("[語意層] 安裝失敗，這次先只用規則層啟動。")
        return False
    return True


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def _healthz(port: int) -> dict | None:
    """取得 /healthz；不是我們的 proxy 或沒回應就回 None。"""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/healthz", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "mapping_entries" in data else None


def _print_usage(port: int, health: dict) -> None:
    base = f"http://{HOST}:{port}"
    upstream_ok = bool(health.get("upstream")) and bool(health.get("upstream_key_loaded"))
    print("\n" + "-" * 60)
    print(f"  [OK] proxy 已就緒   示範頁面：{base}/demo")
    print(f"    語意層：{'開啟' if health.get('ner_enabled') else '關閉（只跑規則層）'}")
    if upstream_ok:
        print("    雲端 AI：已設定（agent 可以接過來了）")
    else:
        print("    雲端 AI：尚未設定 —— 示範頁面照常可用；要接 agent 請填 .env 後重開")
    print("-" * 60)
    print("\n  讓 AI coding agent 走 proxy（另開一個 cmd 視窗，貼上這兩行再啟動 agent）：\n")
    print(f"    set OPENAI_API_BASE={base}/v1")
    print("    set OPENAI_API_KEY=dummy")
    print("\n  [注意] 確認流量真的有經過這裡：agent 開始工作後，下方 log 會出現")
    print("    「第一個請求已抵達 proxy」。沒出現就代表 agent 沒接過來、資料直接送去雲端了。")
    print(f"    也可以開 {base}/healthz 看 traffic.requests 是不是大於 0。")
    print("\n  要結束：在這個視窗按 Ctrl+C。")
    print("-" * 60 + "\n")


def _wait_then_open(port: int, open_browser: bool, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return  # uvicorn 已經結束（多半是啟動失敗），錯誤訊息它自己會印
        health = _healthz(port)
        if health is not None:
            _print_usage(port, health)
            if open_browser:
                webbrowser.open(f"http://{HOST}:{port}/demo")
            return
        time.sleep(0.5)
    print(f"\n[警告] 等了 {READY_TIMEOUT_SECONDS} 秒 proxy 仍未就緒，請看上方 log 的錯誤訊息。")


# proxy/requirements.txt 裡執行期需要的模組（import 名稱）。
# 「啟動.bat」只在 .venv 不存在時才安裝套件；之後 requirements.txt 新增套件
# （例如 pypdf），已經建好的 .venv 不會自己長出來，所以每次啟動前補一次。
_RUNTIME_MODULES = ("fastapi", "uvicorn", "httpx", "dotenv", "pypdf")


def _ensure_requirements() -> None:
    missing = [m for m in _RUNTIME_MODULES if importlib.util.find_spec(m) is None]
    if not missing:
        return
    print(f"\n[更新] 補裝缺少的套件：{'、'.join(missing)}（只有這次需要）...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
         "-r", str(REPO_ROOT / "proxy" / "requirements.txt")],
        cwd=REPO_ROOT,
        check=False,
    )


def run_proxy(port: int, ner_flag: bool | None, open_browser: bool) -> int:
    """選單 [1]：啟動 proxy 與示範頁面，前景執行到使用者按 Ctrl+C。"""
    _ensure_env_file()
    _ensure_requirements()

    if _port_in_use(port):
        if _healthz(port) is not None:
            print(f"\n[提示] proxy 已經在埠 {port} 執行中，直接開啟示範頁面。")
            if open_browser:
                webbrowser.open(f"http://{HOST}:{port}/demo")
            return 0
        print(f"\n[錯誤] 埠 {port} 被其他程式佔用。可以改用：啟動.bat --port 8010")
        return 1

    use_ner = _decide_ner(ner_flag)

    env = os.environ.copy()
    env["PII_ENABLE_DEMO"] = "1"
    env["PII_ENABLE_NER"] = "1" if use_ner else "0"
    env.setdefault("PYTHONIOENCODING", "utf-8")

    print(f"\n啟動中（語意層：{'開啟，載入模型需要一點時間' if use_ner else '關閉'}）...\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "proxy.main:app",
         "--host", HOST, "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
    )
    threading.Thread(
        target=_wait_then_open, args=(port, open_browser, proc), daemon=True
    ).start()

    try:
        return proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C 會同時送給子行程，uvicorn 自己會優雅關閉；這裡只等它結束。
        try:
            return proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            return 1


def _copy_to_clipboard(text: str) -> bool:
    # 不用 clip.exe：它要靠 BOM 才認得 UTF-16，而 BOM 會跟著被貼上，
    # 路徑前面多一個看不見的字元，選資料夾的視窗就找不到路徑。
    # 值走環境變數傳給 PowerShell，避開中文與括號的引號跳脫問題。
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $env:TW_PII_CLIP"],
            env={**os.environ, "TW_PII_CLIP": text},
            check=True,
            timeout=15,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def install_extension() -> None:
    """選單 [2]：build 擴充，然後把「載入未封裝項目」要用到的東西都準備好。

    Chrome 的安全設計不允許任何程式替使用者按下「載入」，所以最後一步一定要
    自己點；這裡能做的是把其餘步驟全部省掉。
    """
    npm = shutil.which("npm")
    if npm is None:
        print("\n[錯誤] 找不到 Node.js。請到 https://nodejs.org/ 安裝 LTS 版後再選一次。")
        print("       （或請組員用 tools/pack_extension.py 打包 zip 給你，解壓後直接載入）")
        return
    try:
        if not (EXT_DIR / "node_modules").exists():
            print("\n首次建置：安裝相依套件（約 30 秒）...")
            subprocess.run([npm, "ci"], cwd=EXT_DIR, check=True)
        print("\n建置擴充中...")
        subprocess.run([npm, "run", "build"], cwd=EXT_DIR, check=True)
    except subprocess.CalledProcessError:
        print("\n[錯誤] 建置失敗，請把上面的訊息截圖給開發者。")
        return

    copied = _copy_to_clipboard(str(EXT_DIST))
    os.startfile(EXT_DIST)  # 開檔案總管，讓使用者看到要選的是哪個資料夾
    # chrome:// 網址不能用 webbrowser 開（不是一般網址），交給 Windows 的 start 指令
    subprocess.run(["cmd", "/c", "start", "", "chrome", "chrome://extensions"], check=False)

    print("\n" + "-" * 60)
    print("  [OK] 擴充已建置完成。已幫你打開 Chrome 的擴充功能頁面，接下來：\n")
    print("  第一次安裝：")
    print("    1. 打開右上角「開發人員模式」")
    print("    2. 按左上角「載入未封裝項目」")
    print(f"    3. 選這個資料夾：{EXT_DIST}")
    if copied:
        print("       （路徑已複製到剪貼簿，在選資料夾視窗上方的網址列按 Ctrl+V 即可）")
    print("\n  已經裝過、只是更新：在擴充卡片上按「重新載入」（圓形箭頭）就好。")
    print("\n  裝好後打開 ChatGPT / Claude / Gemini，貼上含個資的文字就會跳出確認面板。")
    print("  （Chrome 沒有自動打開的話，手動在網址列輸入 chrome://extensions ）")
    print("-" * 60)


def open_manual() -> None:
    """選單 [4]。"""
    for path in MANUAL_CANDIDATES:
        if path.exists():
            os.startfile(path)
            print(f"\n已開啟：{path}")
            return
    print("\n[提示] 說明書還沒產生（docs/使用說明書.pdf）。")


MENU = """
  [1] 啟動 Proxy + 示範頁（試打台／即時監看／現況總覽）
      ─ 保護 AI coding agent，也是現場展示用的主畫面
  [2] 安裝或更新瀏覽器擴充
      ─ 讓 ChatGPT / Claude / Gemini 貼上時自動攔截、回覆自動還原
  [3] 開啟線上 Demo 網站
  [4] 開啟使用說明書
  [Q] 離開
"""


def menu() -> int:
    while True:
        print(MENU)
        try:
            choice = input("請選擇：").strip().lower()
        except EOFError:
            return 0
        if choice == "1":
            return run_proxy(8000, None, True)
        if choice == "2":
            install_extension()
        elif choice == "3":
            webbrowser.open(DEMO_SITE_URL)
            print(f"\n已開啟：{DEMO_SITE_URL}")
        elif choice == "4":
            open_manual()
        elif choice in ("q", "0"):
            return 0
        else:
            print("\n請輸入 1、2、3、4 或 Q。")


def main() -> int:
    parser = argparse.ArgumentParser(description="一鍵啟動器；不帶參數會顯示選單")
    parser.add_argument("--port", type=int, default=8000)
    ner = parser.add_mutually_exclusive_group()
    ner.add_argument("--ner", dest="ner", action="store_true", default=None, help="開啟語意層，不詢問")
    ner.add_argument("--no-ner", dest="ner", action="store_false", help="關閉語意層，不詢問")
    parser.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器")
    args = parser.parse_args()

    # 輸出被導向檔案時 Windows 會用 cp950 且整批緩衝：無法編碼的字元不要讓啟動器當掉，
    # 也逐行送出，否則行程被強制結束時提示訊息會整批遺失
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace", line_buffering=True)

    _print_header()
    if len(sys.argv) > 1:
        return run_proxy(args.port, args.ner, not args.no_browser)
    return menu()


if __name__ == "__main__":
    sys.exit(main())
