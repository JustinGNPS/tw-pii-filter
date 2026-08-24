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
import time

# 佔位符格式 `[TYPE_N]`，與 A 的 `replacement` 欄位一致，C 那邊共用同一格式。
# TYPE 只含大寫英文與底線，N 為正整數。
TOKEN_PATTERN = re.compile(r"\[([A-Z][A-Z_]*)_(\d+)\]")

# 佔位符的最大長度，串流還原時用來決定 buffer 要保留多少字元。
# 型別代碼最長是 TW_PHONE_M（10 字元），加上底線、序號與方括號，
# 64 是很寬鬆的上限。
MAX_TOKEN_LENGTH = 64

# 型別代碼裡不合法的字元（`TOKEN_PATTERN` 只認大寫英文與底線）
_ILLEGAL_TYPE_CHARS = re.compile(r"[^A-Z]+")

# 正規化後空掉時的退路，例如型別是空字串或純數字
FALLBACK_TYPE = "PII"

# 對照表閒置逾時的預設值（秒）。對照表是明文個資，不該無限期駐留 ——
# 見 docs/B_design.md 決定 11。實際生效值由 `proxy/config.py` 讀環境變數決定，
# 這裡的常數只是 `MappingTable()` 不帶參數建構時的退路（測試等場合常這樣用）。
DEFAULT_IDLE_TIMEOUT = 1800.0  # 30 分鐘


def normalize_type(pii_type: object) -> str:
    """把任何型別代碼轉成 `TOKEN_PATTERN` 認得的形式。

    ## 為什麼需要這個

    語意層（D 的 NER）回傳的是模型的 `entity_group`，實測是**小寫**的
    `name` / `address` / `position`，不是 `interface.md` 列的大寫代碼。
    直接拿來發號碼會產生 `[name_1]`，而 `TOKEN_PATTERN` 只認大寫 ——
    **遮蔽會成功，還原會失效**，佔位符最後被寫進使用者的檔案。

    這比沒遮到更糟：沒遮到只是那次請求沒受保護，還原失效是實際弄壞使用者的
    程式碼（第一版就是這樣讓 Aider 回報 `SEARCH/REPLACE block failed to match`）。

    正則不放寬成接受小寫，是因為 `[abc_1]` 這種寫法在程式碼裡很常見
    （見 `tests/test_mapping.py::test_不會誤抓一般的中括號`），放寬會讓
    還原去動到不該動的東西。**把入口收乾淨，比把出口放寬安全。**

    ## 只做格式正規化，不做語意改名

    `name` 會變成 `NAME`，**不會**自作主張改成 `PERSON`。對外的類別代碼要叫
    什麼是 `docs/interface.md` 的決定（已在 PR #3 請 A 裁示），proxy 只負責
    保證「不管上游送什麼進來，發出去的佔位符都還原得回去」。A 之後若統一改成
    大寫代碼，這個函式就退化成 no-op，不需要再改一次。

    ## 第二個作用：避免撞號

    `MappingTable` 的號碼是按型別分別遞增的。若 `name` 與 `NAME` 被當成兩個
    不同型別，兩邊都會從 1 開始發，產生兩個 `[NAME_1]` 指向不同的人 ——
    還原時把兩個人的個資對調。正規化後兩者共用同一個計數器。

    數字會被一併清掉（`ADDRESS2` -> `ADDRESS`），因為 `TOKEN_PATTERN` 用
    底線分隔型別與序號，型別裡含數字會讓佔位符無法反解。目前沒有含數字的
    型別代碼，真的出現時應該在 `interface.md` 正名，而不是在這裡遷就。
    """
    cleaned = _ILLEGAL_TYPE_CHARS.sub("_", str(pii_type or "").upper()).strip("_")
    return cleaned or FALLBACK_TYPE


class MappingTable:
    """真值與佔位符的雙向對照表。

    執行緒安全：FastAPI 以非同步方式處理請求，同一個 proxy 行程可能同時
    處理多個請求，因此以 lock 保護。

    ## 閒置逾時清除（決定 11，見 `docs/B_design.md`）

    對照表是明文個資，不該無限期駐留 —— 閒置超過 `idle_timeout` 秒就在
    下一次 `token_for()` 之前整張清空。只在 `token_for()`（新增遮蔽）檢查，
    **不在 `value_for()`（還原）檢查**：還原永遠緊接在同一次請求的遮蔽
    之後，只要遮蔽當下沒清空，還原就不會撲空；若還原路徑也做這個檢查，
    長時間串流中的還原可能被自己觸發的逾時清空打斷，把還沒還原完的
    佔位符變成查無對照（比不清除更糟）。

    `idle_timeout=None` 停用這個機制（永不自動清空，第一版與測試常見用法）。
    """

    def __init__(self, idle_timeout: float | None = DEFAULT_IDLE_TIMEOUT) -> None:
        self._token_of: dict[tuple[str, str], str] = {}  # (型別, 真值) -> 佔位符
        self._value_of: dict[str, str] = {}  # 佔位符 -> 真值
        self._counter: dict[str, int] = {}  # 型別 -> 已發出的最大號碼
        self._lock = threading.Lock()
        self._idle_timeout = idle_timeout
        self._last_active = time.monotonic()

    def _clear_locked(self) -> None:
        """清空內容，呼叫端必須已持有 `_lock`（本身不上鎖，避免與 `clear()`/
        `_expire_if_idle_locked()` 巢狀取鎖造成死結）。"""
        self._token_of.clear()
        self._value_of.clear()
        self._counter.clear()

    def _expire_if_idle_locked(self) -> None:
        """閒置超過門檻就清空，呼叫端必須已持有 `_lock`。"""
        now = time.monotonic()
        if self._idle_timeout and now - self._last_active > self._idle_timeout:
            self._clear_locked()
        self._last_active = now

    def token_for(self, pii_type: str, value: str) -> str:
        """取得真值對應的佔位符；沒看過的真值會配一個新號碼。

        同一個 `(型別, 真值)` 永遠回傳同一個佔位符。

        型別代碼一律先經過 `normalize_type()`，因此 `name` 與 `NAME` 視為
        同一型別、共用同一個計數器（理由見該函式說明）。
        """
        pii_type = normalize_type(pii_type)
        key = (pii_type, value)
        with self._lock:
            self._expire_if_idle_locked()

            existing = self._token_of.get(key)
            if existing is not None:
                return existing

            number = self._counter.get(pii_type, 0) + 1
            self._counter[pii_type] = number
            token = f"[{pii_type}_{number}]"

            self._token_of[key] = token
            self._value_of[token] = value
            return token

    def issued_counts(self) -> dict[str, int]:
        """每個型別**至今發出過幾個佔位符**的快照（＝看過幾個不重複的真值）。

        `_counter` 記的是每個型別已發出的最大號碼，而號碼只在配給新真值時才
        遞增，所以它天然就是「不重複真值的累計數」，不需要另外維護計數器。

        用途是讓呼叫端算得出「這一輪**新增**了什麼」：agent 每輪都會重送整段
        對話歷史，同一批個資會被反覆掃到，但只有第一次會發新號碼。前後各取
        一次快照相減，就是這一輪真正新出現的個資。

        閒置逾時清空後計數會歸零，因此相減可能出現負值 —— 呼叫端要處理，
        見 `proxy/masker.new_value_counts()`。
        """
        with self._lock:
            return dict(self._counter)

    def value_for(self, token: str) -> str | None:
        """查佔位符對應的真值；**查不到回傳 None，絕不猜測**。

        雲端 AI 可能自己編出沒發過的佔位符（幻覺）。猜測等同於憑空捏造一筆
        個資塞進使用者的檔案，比洩漏更糟 —— 因此查不到就讓呼叫端原樣保留。

        刻意不檢查閒置逾時（理由見類別 docstring）。
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
        """清空對照表（手動呼叫；自動閒置清除見 `_expire_if_idle_locked`）。"""
        with self._lock:
            self._clear_locked()
            self._last_active = time.monotonic()

    def __len__(self) -> int:
        with self._lock:
            return len(self._value_of)
