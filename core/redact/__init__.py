"""遮蔽與還原的共用邏輯（兩個載體共用，issue #23）。

原本住在 `proxy/` 底下，但那是 B 的 API Proxy 專屬位置，而同一套佔位符
格式與還原規則，C 的瀏覽器擴充也要用。搬到中性套件是為了讓「唯一的權威
定義」只有一份，不會兩邊各自演化到對不起來。

## 這裡放什麼、不放什麼

**放**：拿到文字與 span 之後的事 —— 配佔位符、替換、還原、對照表管理。
純字串與資料結構操作，不讀環境變數、不認得任何 API 協定。

**不放**：怎麼從 OpenAI／Anthropic 的請求 payload 裡挖出該掃的欄位
（`proxy/detector.py`）、proxy 的設定與政策預設（`proxy/config.py`）、
組合風險警示（`proxy/risk.py`）。那些是載體專屬的，搬進來會讓 `core/`
反過來相依 `proxy/`。

## 給 C 的提醒

這是 Python，擴充是獨立的 TypeScript 實作，**不會直接 import 這份程式碼**。
它的角色是「唯一的權威參考實作」：佔位符格式、發號規則、查不到時的行為，
以這裡為準。
"""

from core.redact.mapping import (
    MAX_TOKEN_LENGTH,
    TOKEN_PATTERN,
    MappingTable,
    normalize_type,
)
from core.redact.masker import mask_text
from core.redact.restorer import (
    SSERestorer,
    StreamRestorer,
    restore_body,
    restore_text,
)

__all__ = [
    "MAX_TOKEN_LENGTH",
    "TOKEN_PATTERN",
    "MappingTable",
    "SSERestorer",
    "StreamRestorer",
    "mask_text",
    "normalize_type",
    "restore_body",
    "restore_text",
]
