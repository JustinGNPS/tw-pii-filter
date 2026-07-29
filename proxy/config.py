"""Proxy 設定。

所有設定一律從環境變數讀取（`.env` 由 python-dotenv 載入），
**程式碼中絕對不出現金鑰字面值，也不會把金鑰寫進 log**。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# repo 根目錄的 .env（proxy/config.py -> proxy/ -> repo 根）
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", encoding="utf-8")


def _env(names: tuple[str, ...], default: str = "") -> tuple[str, str]:
    """依序尋找第一個有值的環境變數，回傳 (值, 命中的變數名稱)。

    接受多個別名是為了容錯：`.env` 內的變數名稱可能與此處預設不同。
    回傳「命中的名稱」是為了讓啟動時能 log 出用了哪個變數（只印名稱，不印值）。
    """
    for name in names:
        value = os.getenv(name)
        if value:
            return value, name
    return default, ""


UPSTREAM_BASE_URL, UPSTREAM_BASE_URL_ENV = _env(
    ("UPSTREAM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE", "AIR_BASE_URL"),
    "https://air.cgu.edu.tw/cgullmapi/v1",
)

UPSTREAM_API_KEY, UPSTREAM_API_KEY_ENV = _env(
    ("UPSTREAM_API_KEY", "OPENAI_API_KEY", "AIR_API_KEY", "API_KEY"),
)

DEFAULT_MODEL, DEFAULT_MODEL_ENV = _env(
    ("DEFAULT_MODEL", "OPENAI_MODEL", "AIR_MODEL"),
    "gpt-4.1-mini",
)

# base URL 結尾不留斜線，方便後面直接字串相接
UPSTREAM_BASE_URL = UPSTREAM_BASE_URL.rstrip("/")

# 轉發逾時（秒）。LLM 回應可能很慢，read timeout 放寬。
CONNECT_TIMEOUT = float(os.getenv("PROXY_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("PROXY_READ_TIMEOUT", "600"))


def startup_summary() -> str:
    """啟動時印的設定摘要 —— 只印變數名稱與 base URL，**不印金鑰內容**。"""
    key_status = (
        f"已載入（來自 {UPSTREAM_API_KEY_ENV}）" if UPSTREAM_API_KEY else "**未設定**"
    )
    return (
        f"上游 base URL：{UPSTREAM_BASE_URL}\n"
        f"上游金鑰：{key_status}\n"
        f"預設模型：{DEFAULT_MODEL}"
    )
