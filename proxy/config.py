"""Proxy 設定。

所有設定一律從環境變數讀取（`.env` 由 python-dotenv 載入），
**程式碼中絕對不出現金鑰字面值，也不會把金鑰寫進 log**。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from proxy.mapping import DEFAULT_IDLE_TIMEOUT, normalize_type

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

# 偵測得到、但**不遮蔽**的型別 ------------------------------------------------
#
# 語意層會回傳 `position`（客服／客戶／工程師這類職稱、角色詞），信心分數往往
# 很高，但職稱本身不是個資。全部遮掉會讓 agent 看不懂上下文，還會在畫面上塞滿
# 沒有意義的佔位符 —— 遮錯東西沒有任何隱私上的好處，只有可用性的代價。
#
# 已與 D 在 PR #3 取得共識：預設不遮蔽，但保留設定空間（不同專案對「什麼算敏感」
# 的認定可能不同，例如組織內部職稱表也可能是不想外流的資訊）。
#
# 設定方式：`.env` 或環境變數 `PII_SKIP_TYPES`，以逗號分隔，例如
#     PII_SKIP_TYPES=POSITION,ORG
# 設成空字串代表**什麼都不跳過**（連 position 也遮）。
DEFAULT_SKIP_TYPES = "POSITION"


def _parse_skip_types(raw: str) -> frozenset[str]:
    """把逗號分隔的設定字串轉成正規化後的型別集合。

    正規化是必要的：使用者可能寫小寫的 `position`，而遮蔽時比對的是
    `normalize_type()` 之後的代碼，兩邊不一致就會靜默地不生效。
    """
    return frozenset(
        normalize_type(item) for item in raw.split(",") if item.strip()
    )


_skip_raw = os.getenv("PII_SKIP_TYPES")
SKIP_TYPES = _parse_skip_types(
    DEFAULT_SKIP_TYPES if _skip_raw is None else _skip_raw
)

# 語意層（D 的 NER）是否啟用 -------------------------------------------------
#
# 語意層單次推論（CPU）約 742 ms，是規則層的一百多倍，且是同步阻塞呼叫。
# 預設關閉：規則層永遠開（快、確定性，是本專題的核心賣點），語意層是
# 選配的加強，需要展示姓名/地址遮蔽效果時才手動開啟。
#
# 設定方式：`.env` 或環境變數 `PII_ENABLE_NER=1`（或 `true`/`yes`，大小寫不拘）。
ENABLE_NER = os.getenv("PII_ENABLE_NER", "").strip().lower() in ("1", "true", "yes")

# 對照表閒置逾時（秒）-------------------------------------------------------
#
# 對照表是明文個資，不該無限期駐留 —— 見 docs/B_design.md 決定 11。閒置超過
# 這段時間，下一次遮蔽動作前會整張清空重來（不影響正在進行中的還原，見
# `proxy/mapping.py::MappingTable` 的說明）。
#
# 設定方式：`.env` 或環境變數 `PROXY_MAPPING_IDLE_TIMEOUT`（秒）。設成 `0`
# 代表停用（僅供除錯／demo 時觀察數字持續累積用，正式使用不建議關閉）。
MAPPING_IDLE_TIMEOUT = float(
    os.getenv("PROXY_MAPPING_IDLE_TIMEOUT", str(DEFAULT_IDLE_TIMEOUT))
)


def startup_summary() -> str:
    """啟動時印的設定摘要 —— 只印變數名稱與 base URL，**不印金鑰內容**。"""
    key_status = (
        f"已載入（來自 {UPSTREAM_API_KEY_ENV}）" if UPSTREAM_API_KEY else "**未設定**"
    )
    skipped = "、".join(sorted(SKIP_TYPES)) if SKIP_TYPES else "（無）"
    return (
        f"上游 base URL：{UPSTREAM_BASE_URL}\n"
        f"上游金鑰：{key_status}\n"
        f"預設模型：{DEFAULT_MODEL}\n"
        f"偵測到但不遮蔽的型別：{skipped}\n"
        f"語意層（NER）：{'已啟用' if ENABLE_NER else '未啟用（僅規則層）'}\n"
        f"對照表閒置逾時：{f'{MAPPING_IDLE_TIMEOUT:.0f} 秒' if MAPPING_IDLE_TIMEOUT else '停用'}"
    )
