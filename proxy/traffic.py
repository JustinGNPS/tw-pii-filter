"""「流量到底有沒有真的走 proxy」的可見度。

## 為什麼需要這個

proxy 的保護有一個前提：**agent 真的被指向它**。這個前提失敗時，沒有任何
東西會出聲 —— agent 照常運作、照常回答問題，只是內容直接送去雲端，畫面上
不會有一行字告訴使用者「你的過濾器沒有在運作」。

2026-08-22 實測時真的發生了：Codex 因為環境變數沒設成功，讀到另一份設定檔，
`base_url` 直接指著上游，整包繞過 proxy。那次是靠上游回 401 才被發現的 ——
換句話說，**如果上游金鑰剛好是對的，就不會有任何跡象**。

所以這裡做三件事，讓「有沒有走 proxy」變成看得見的事實：

1. 啟動時明說「還沒收到任何請求，該看什麼」
2. **第一個請求抵達時印一行**（一次性，不洗版）
3. `/healthz` 帶上累計次數與最後一次時間，供事後查證

這不是效能監控 —— 只數「抵達」，不管上游成不成功。使用者要回答的問題是
「我的流量有沒有經過這裡」，而不是「上游健不健康」。
"""

from __future__ import annotations

import time


class TrafficStats:
    """抵達 proxy 的請求計數。健康檢查（`/healthz`）本身不計入。"""

    def __init__(self) -> None:
        self._count = 0
        self._first_at: float | None = None
        self._last_at: float | None = None

    def record(self) -> bool:
        """記錄一次抵達；**回傳這是不是第一次**。

        回傳值讓呼叫端可以只在第一次印訊息 —— agent 每輪都會送請求，
        每次都印會把 log 洗掉，而使用者要確認的事情只需要說一次。
        """
        now = time.time()
        self._count += 1
        self._last_at = now
        if self._first_at is None:
            self._first_at = now
            return True
        return False

    @property
    def count(self) -> int:
        return self._count

    def snapshot(self) -> dict:
        """給 `/healthz` 用。時間用本地時間的 ISO 格式，與 log 的時間對得起來。"""
        return {
            "requests": self._count,
            "first_at": _iso(self._first_at),
            "last_at": _iso(self._last_at),
            "seconds_since_last": (
                None if self._last_at is None else round(time.time() - self._last_at, 1)
            ),
        }

    def reset(self) -> None:
        """測試用：把計數歸零，避免測試之間互相影響。"""
        self.__init__()


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))


# 模組層單例，與 `detector.CACHE` 同樣的用法：proxy 是單一行程，
# 這份統計本來就該是全域的。
STATS = TrafficStats()
