"""偵測快取測試。

核心驗收條件：**快取不能改變偵測結果**，只能改變花費的時間。
"""

import copy

import pytest

from proxy import detector, masker
from proxy.cache import DetectionCache
from core.redact.mapping import MappingTable


@pytest.fixture
def cache():
    return DetectionCache()


def test_相同文字第二次不會重新計算(cache):
    calls = []

    def compute(text):
        calls.append(text)
        return [{"type": "TW_ID"}]

    first = cache.get_or_compute("同一段文字", compute)
    second = cache.get_or_compute("同一段文字", compute)

    assert calls == ["同一段文字"]  # 只算了一次
    assert first == second
    assert (cache.hits, cache.misses) == (1, 1)


def test_不同文字各自計算(cache):
    calls = []
    cache.get_or_compute("A", lambda t: calls.append(t) or [])
    cache.get_or_compute("B", lambda t: calls.append(t) or [])

    assert calls == ["A", "B"]
    assert (cache.hits, cache.misses) == (0, 2)


def test_回傳的是複本_改動不會汙染快取(cache):
    spans = cache.get_or_compute("文字", lambda t: [{"type": "TW_ID", "start": 0}])
    spans[0]["start"] = 999
    spans.append({"type": "亂加的"})

    again = cache.get_or_compute("文字", lambda t: [])

    assert again == [{"type": "TW_ID", "start": 0}]


def test_超過上限會丟掉最久沒用到的():
    cache = DetectionCache(max_entries=2)
    cache.get_or_compute("A", lambda t: [])
    cache.get_or_compute("B", lambda t: [])
    cache.get_or_compute("A", lambda t: [])  # A 變成最近使用
    cache.get_or_compute("C", lambda t: [])  # 該被丟掉的是 B

    assert len(cache) == 2
    recomputed = []
    cache.get_or_compute("A", lambda t: recomputed.append(t) or [])  # 還在
    cache.get_or_compute("B", lambda t: recomputed.append(t) or [])  # 被丟了
    assert recomputed == ["B"]


def test_key_是指紋而非原文():
    """快取的 key 不應該存下個資原文。"""
    fingerprint = DetectionCache.fingerprint("客戶 A123456789")

    assert "A123456789" not in fingerprint
    assert len(fingerprint) == 64  # SHA-256 十六進位


def test_key_extra不同時視為不同筆快取(cache):
    """C 在 PR #11 review 提過：`PII_ENABLE_NER` 開關若不併入 key，開關切換後
    可能誤用切換前算出的快取結果。用 `key_extra` 模擬這種會影響偵測結果的
    執行期設定。
    """
    calls = []
    compute = lambda t: calls.append(t) or [{"type": "TW_ID"}]

    cache.get_or_compute("同一段文字", compute, key_extra="True")
    cache.get_or_compute("同一段文字", compute, key_extra="False")

    assert calls == ["同一段文字", "同一段文字"]  # 兩種設定各算一次，不互相借用
    assert (cache.hits, cache.misses) == (0, 2)

    cache.get_or_compute("同一段文字", compute, key_extra="True")  # 這次才是真的命中
    assert (cache.hits, cache.misses) == (1, 2)


def test_命中率統計(cache):
    assert cache.hit_rate == 0.0  # 還沒查過

    cache.get_or_compute("A", lambda t: [])  # miss
    cache.get_or_compute("A", lambda t: [])  # hit
    cache.get_or_compute("A", lambda t: [])  # hit

    assert cache.hit_rate == pytest.approx(2 / 3)
    assert cache.stats()["entries"] == 1


def test_clear_會重置內容與統計(cache):
    cache.get_or_compute("A", lambda t: [])
    cache.get_or_compute("A", lambda t: [])

    cache.clear()

    assert len(cache) == 0
    assert (cache.hits, cache.misses) == (0, 0)


def test_上限至少要是_1():
    with pytest.raises(ValueError):
        DetectionCache(max_entries=0)


def test_空快取在布林判斷時仍為真(cache):
    """有 __len__ 的物件預設會以長度決定真假，空快取會變成 False。

    `cache or fallback` 這種寫法會因此在快取還沒暖起來時默默走錯分支
    （實際在效能量測腳本裡踩過）。
    """
    assert len(cache) == 0
    assert bool(cache) is True
    assert (cache or "走錯分支了") is cache


# ------------------------------------------------ 與偵測器整合


def test_快取不改變偵測結果(cache):
    text = "客戶 A123456789 電話 0912345678"

    first = detector.detect(text, cache)
    second = detector.detect(text, cache)
    no_cache = detector.detect(text, DetectionCache())

    assert first == second == no_cache
    assert cache.hits == 1


def test_遮蔽走快取後結果仍正確(cache):
    """模擬 agent 重送對話歷史：同一份內容送兩次，結果必須一致。"""
    content = "客戶 A123456789 電話 0912345678"
    table = MappingTable()

    first = {"messages": [{"role": "user", "content": content}]}
    masker.mask_payload(first, table, cache)

    second = {"messages": [{"role": "user", "content": content}]}
    masker.mask_payload(second, table, cache)

    assert first == second
    assert "A123456789" not in str(first)
    assert cache.hits == 1  # 第二次沒有重新偵測


def test_重送對話歷史時大部分欄位都命中快取(cache):
    """Aider 每輪都重送整段歷史，越到後面命中率應該越高。

    每輪用 deepcopy 重建 payload —— agent 送出的一律是**原始未遮蔽**的內容，
    它並不知道 proxy 做過遮蔽。
    """
    table = MappingTable()
    history = []

    for turn in range(5):
        history.append({"role": "user", "content": f"第 {turn} 輪：客戶 A123456789"})
        masker.mask_payload(copy.deepcopy({"messages": history}), table, cache)

    # 5 輪共掃 1+2+3+4+5 = 15 個欄位，其中只有 5 個是新的
    assert cache.misses == 5
    assert cache.hits == 10
    assert cache.hit_rate == pytest.approx(10 / 15)
