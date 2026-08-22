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

import threading
import time
from collections import deque


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


class EventLog:
    """最近發生的事件（環形緩衝），供示範頁面的「即時監看」讀取。

    ## 為什麼要記事件而不只是計數

    `TrafficStats` 回答的是「有沒有流量」，這裡回答的是「**剛剛發生了什麼**」。
    agent 工作時，遮蔽與還原的細節目前只存在於終端機的 log 裡，一洗過去就沒了
    —— 而那正是開發期最需要盯著看的東西（2026-08-22 兩次「agent 靜默繞過
    proxy」都是事後翻 log 才發現的）。

    ## 紅線：只記型別與數量，絕不記原始內容

    跟 log 同一個規則。這個緩衝區若記了原文，它就變成一份躺在記憶體裡的
    個資快取 —— 而且會透過 `/demo/events` 端點暴露出去。

    ## 為什麼有 id

    頁面用輪詢的方式讀（每隔幾秒問一次），帶上「我看到哪一筆了」就只會拿到
    新的部分，不必每次重傳整份。id 單調遞增，不因緩衝區淘汰而重來。
    """

    def __init__(self, max_events: int = 100) -> None:
        self._events: deque[dict] = deque(maxlen=max_events)
        self._next_id = 1
        self._lock = threading.Lock()

    def record(self, kind: str, **fields: object) -> None:
        """記一筆事件。`kind` 目前有 `mask`（遮蔽到新個資）與 `done`（請求完成）。"""
        with self._lock:
            event = {
                "id": self._next_id,
                "at": time.strftime("%H:%M:%S", time.localtime()),
                "kind": kind,
                **fields,
            }
            self._next_id += 1
            self._events.append(event)

    def since(self, last_id: int = 0) -> list[dict]:
        """回傳 id 大於 `last_id` 的事件。`last_id=0` 代表要目前緩衝區的全部。"""
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._events[-1]["id"] if self._events else 0

    def clear(self) -> None:
        """測試用。"""
        with self._lock:
            self._events.clear()
            self._next_id = 1


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))


# 模組層單例，與 `detector.CACHE` 同樣的用法：proxy 是單一行程，
# 這份統計本來就該是全域的。
STATS = TrafficStats()

# 事件緩衝只在示範頁面開啟時才有人讀，但一律記錄 —— 成本是一個上限 100 筆的
# deque，而「出事之後才想開來看」是來不及的。
EVENTS = EventLog()
