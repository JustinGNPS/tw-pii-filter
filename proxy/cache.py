"""偵測結果快取。

## 為什麼需要

agent 每次請求都會重送整段對話歷史，同一份檔案內容因此會被重複送到
proxy 十幾次，每次都掃出一模一樣的結果：

```
第 1 輪：billing.py 全文
第 2 輪：billing.py 全文 + 第 1 輪對話
第 3 輪：billing.py 全文 + 前兩輪對話
```

偵測是純函式（同樣的文字進去，永遠同樣的結果出來），因此可以安全地快取。
規則層目前只要 2〜4 ms，浪費還不明顯；但 Layer 2（D 的 NER 模型）接進來
之後，這筆重複成本會被放大好幾個數量級，而且**對話越長越嚴重**。

## 為什麼有大小上限

對照表可以無限成長（一個專案裡的個資數量有限），但快取存的是**整份檔案的
偵測結果**，不設上限記憶體會失控。採 LRU：滿了就丟掉最久沒被用到的那筆。

## 隱私

key 是文字的 SHA-256 **指紋，不存原文**。但 value（spans）裡的 `text`
欄位本來就含有偵測到的個資 —— 與對照表相同，**只存在記憶體，不落地**，
行程結束即消失。
"""

import hashlib
import threading
from collections import OrderedDict
from typing import Callable

DEFAULT_MAX_ENTRIES = 256


class DetectionCache:
    """以文字指紋為 key 的 LRU 快取。"""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries 至少要是 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, list[dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def fingerprint(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_or_compute(self, text: str, compute: Callable[[str], list[dict]]) -> list[dict]:
        """查快取；沒命中就呼叫 `compute(text)` 並記住結果。

        回傳的是**複本** —— 呼叫端就算改動了 spans，也不會汙染快取。
        """
        key = self.fingerprint(text)

        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)  # 標記為最近使用
                self.hits += 1
                return _copy_spans(cached)
            self.misses += 1

        # 在 lock 之外計算：偵測可能很慢（Layer 2 的模型推論），
        # 不該擋住其他請求查快取
        spans = compute(text)

        with self._lock:
            self._entries[key] = _copy_spans(spans)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)  # 丟掉最久沒用到的

        return spans

    @property
    def hit_rate(self) -> float:
        """命中率，0.0〜1.0。沒有任何查詢時回傳 0.0。"""
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict[str, float | int]:
        return {
            "entries": len(self),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 3),
        }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __bool__(self) -> bool:
        """快取物件永遠為真。

        有 `__len__` 的物件在布林判斷時會以長度決定真假，空快取因此會是
        False —— `cache or fallback` 這種寫法就會在快取還沒暖起來時默默
        走錯分支。這裡明確覆寫，避免下一個人踩到。
        """
        return True


def _copy_spans(spans: list[dict]) -> list[dict]:
    """淺複製每個 span，避免快取內容被呼叫端就地修改。"""
    return [dict(span) for span in spans]
