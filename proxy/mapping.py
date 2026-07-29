"""佔位符對照表：`[TW_ID_1]` ↔ `A123456789` 的雙向對應。

**只存在記憶體，絕不寫入檔案。** 真實個資不落地是本專題的核心主張之一：
不產生的東西不可能外洩，也就不需要處理加密、權限與刪除時機。
proxy 行程結束，對照表隨之消失。

## 為什麼不直接用 A 的 `replacement` 欄位

A 的 `detect_all()` 每次都從 1 重新編號，而 agent 每次請求都會重送整段對話
歷史。同一個真值在不同次請求中可能拿到不同號碼，號碼也可能被別的真值佔用：

```
第 1 次請求： A123456789 -> [TW_ID_1]
第 2 次請求： A123456789 -> [TW_ID_2]      同一人換了號碼
              F131104093 -> [TW_ID_1]      1 號被別人拿去用
```

還原時就會把兩個人的資料對調 —— 比洩漏更嚴重。因此號碼由本模組自己發，
**一個真值第一次出現時配一個號碼，之後永遠是那個號碼**。

## 已知的取捨

同一真值永遠對到同一佔位符，因此雲端 AI 可以看出「這兩處是同一個人」
（關聯性洩漏），但看不到真實身分。若每次都換不同佔位符，agent 的
diff 比對就會失敗（實測：Aider 會回報 SEARCH/REPLACE block failed to match），
因此一致性是必要的。
"""

import re
import threading

# 佔位符格式 `[TYPE_N]`，與 A 的 `replacement` 欄位一致，C 那邊共用同一格式。
# TYPE 只含大寫英文與底線，N 為正整數。
TOKEN_PATTERN = re.compile(r"\[([A-Z][A-Z_]*)_(\d+)\]")

# 佔位符的最大長度，串流還原時用來決定 buffer 要保留多少字元。
# 型別代碼最長是 TW_PHONE_M（10 字元），加上底線、序號與方括號，
# 64 是很寬鬆的上限。
MAX_TOKEN_LENGTH = 64


class MappingTable:
    """真值與佔位符的雙向對照表。

    執行緒安全：FastAPI 以非同步方式處理請求，同一個 proxy 行程可能同時
    處理多個請求，因此以 lock 保護。
    """

    def __init__(self) -> None:
        self._token_of: dict[tuple[str, str], str] = {}  # (型別, 真值) -> 佔位符
        self._value_of: dict[str, str] = {}  # 佔位符 -> 真值
        self._counter: dict[str, int] = {}  # 型別 -> 已發出的最大號碼
        self._lock = threading.Lock()

    def token_for(self, pii_type: str, value: str) -> str:
        """取得真值對應的佔位符；沒看過的真值會配一個新號碼。

        同一個 `(型別, 真值)` 永遠回傳同一個佔位符。
        """
        key = (pii_type, value)
        with self._lock:
            existing = self._token_of.get(key)
            if existing is not None:
                return existing

            number = self._counter.get(pii_type, 0) + 1
            self._counter[pii_type] = number
            token = f"[{pii_type}_{number}]"

            self._token_of[key] = token
            self._value_of[token] = value
            return token

    def value_for(self, token: str) -> str | None:
        """查佔位符對應的真值；**查不到回傳 None，絕不猜測**。

        雲端 AI 可能自己編出沒發過的佔位符（幻覺）。猜測等同於憑空捏造一筆
        個資塞進使用者的檔案，比洩漏更糟 —— 因此查不到就讓呼叫端原樣保留。
        """
        with self._lock:
            return self._value_of.get(token)

    def restore_text(self, text: str) -> tuple[str, int, int]:
        """把文字中所有已知佔位符換回真值。

        回傳 `(還原後的文字, 換回的筆數, 查不到而原樣保留的筆數)`。
        """
        restored = 0
        unknown = 0

        def replace(match: re.Match) -> str:
            nonlocal restored, unknown
            value = self.value_for(match.group(0))
            if value is None:
                unknown += 1
                return match.group(0)  # 原樣保留
            restored += 1
            return value

        return TOKEN_PATTERN.sub(replace, text), restored, unknown

    def clear(self) -> None:
        """清空對照表（例如新對話開始時，避免號碼無止盡累積）。"""
        with self._lock:
            self._token_of.clear()
            self._value_of.clear()
            self._counter.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._value_of)
