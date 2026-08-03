"""`MappingTable` 的併發安全測試。

## 為什麼現在要測這個

`proxy/main.py` 的 `_mask_request`（呼叫 `mapping.token_for()` 的地方）原本是
在 async event loop 裡直接同步呼叫，FastAPI/uvicorn 單執行緒跑 event loop，
「同時處理多個請求」實際上是交錯執行，不會有兩個請求的 Python bytecode
真的同時跑。

語意層接線那次改動（PR #11）把 `_mask_request` 改成 `asyncio.to_thread(...)`，
丟到背景執行緒池執行 —— 這之後，多個並發請求的 `_mask_request` **有可能真的
在不同 OS thread 上同時執行**，同時打同一個 `app.state.mapping`（`MappingTable`
單一共用實例）。`MappingTable` 內部本來就有 `threading.Lock()`，但這個鎖
一直沒有被真正的併發測過 —— 這份測試補上這一塊。

## 這份測試「不」驗證什麼

只驗證併發下 `MappingTable` 本身的資料結構不會壞（不會把兩個人的個資對調、
不會漏發號碼）。**不驗證**「對照表該不該跨請求共用」這個設計選擇本身
（那是 `test_table_scope_experiment.py` 要回答的問題）。
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from proxy.mapping import MappingTable


def test_大量執行緒同時搶第一次配號_只會配出一個號碼():
    """經典的 check-then-act race：多個執行緒同時對同一個「沒看過的真值」
    呼叫 token_for()，理論上沒鎖保護的話會重複配號、甚至覆蓋彼此的結果。
    用 Barrier 讓所有執行緒盡量同一瞬間衝進去，放大競爭窗口。
    """
    table = MappingTable()
    n_threads = 64
    barrier = threading.Barrier(n_threads)
    results = [None] * n_threads

    def worker(i: int) -> None:
        barrier.wait()  # 讓所有執行緒盡量同時起跑
        results[i] = table.token_for("TW_ID", "A123456789")

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(worker, range(n_threads)))

    # 全部執行緒查的是同一個真值，必須全部拿到一模一樣的佔位符
    assert len(set(results)) == 1
    assert results[0] == "[TW_ID_1]"
    # 只有一個真值被配過號，長度必須是 1（不能因為競爭多發了幾個號碼）
    assert len(table) == 1


def test_大量不同真值同時配號_不會撞號也不會漏發():
    """N 個「不同」真值同時湧入，驗證每個都拿到獨一無二的號碼，
    且號碼是連續的 1..N（沒有漏發、沒有重複）。
    """
    table = MappingTable()
    n_values = 200
    values = [f"09{str(i).zfill(8)}" for i in range(n_values)]  # 200 個不同手機號碼
    barrier = threading.Barrier(n_values)
    results: list[str] = [None] * n_values

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = table.token_for("TW_PHONE_M", values[i])

    with ThreadPoolExecutor(max_workers=n_values) as pool:
        list(pool.map(worker, range(n_values)))

    # 每個真值都拿到獨一無二的佔位符（沒有兩個不同真值撞到同一個號碼——
    # 這是最嚴重的錯誤：還原時會把兩個人的個資對調）
    assert len(set(results)) == n_values

    # 反查每個佔位符，必須查回原本對應的真值，不能有一個對錯
    for value, token in zip(values, results):
        assert table.value_for(token) == value

    # 號碼必須是連續的 1..N，不能因為競爭漏發或跳號
    numbers = sorted(int(t.rsplit("_", 1)[1].rstrip("]")) for t in results)
    assert numbers == list(range(1, n_values + 1))


def test_混合讀寫同時發生_value_for查詢不會看到半寫入的狀態():
    """一半執行緒在寫入新真值（token_for），另一半在查詢已知佔位符
    （value_for），確認鎖有正確保護讀寫互斥，查詢端不會讀到不一致的中間狀態
    （例如 _value_of 已經寫入但 _token_of 還沒寫入之類的半套資料）。
    """
    table = MappingTable()
    known_token = table.token_for("EMAIL", "known@example.com")  # 先準備一筆已知資料

    n_writers = 50
    n_readers = 50
    errors: list[str] = []
    lock = threading.Lock()

    def writer(i: int) -> None:
        table.token_for("TW_ID", f"WRITER{i:03d}")

    def reader(_i: int) -> None:
        for _ in range(20):  # 每個 reader 多查幾次，增加撞見寫入中狀態的機會
            value = table.value_for(known_token)
            if value != "known@example.com":
                with lock:
                    errors.append(f"讀到不一致的值：{value!r}")

    with ThreadPoolExecutor(max_workers=n_writers + n_readers) as pool:
        futures = [pool.submit(writer, i) for i in range(n_writers)]
        futures += [pool.submit(reader, i) for i in range(n_readers)]
        for f in futures:
            f.result()

    assert errors == []
    assert len(table) == 1 + n_writers  # 已知那筆 + 所有 writer 各自的一筆
