"""還原測試，特別是串流被切成兩半的情況。

`StreamRestorer` 的驗收條件：**不管怎麼切，還原後的完整輸出都要一樣。**
"""

import json

import pytest

from proxy import restorer
from proxy.mapping import MappingTable


@pytest.fixture
def table():
    t = MappingTable()
    t.token_for("TW_ID", "A123456789")  # [TW_ID_1]
    t.token_for("TW_PHONE_M", "0912345678")  # [TW_PHONE_M_1]
    return t


# ---------------------------------------------------------------- 非串流


def test_還原整包_json_回覆(table):
    body = json.dumps(
        {"choices": [{"message": {"content": "客戶 [TW_ID_1] 的電話是 [TW_PHONE_M_1]"}}]},
        ensure_ascii=False,
    ).encode("utf-8")

    restored_body, count, unknown = restorer.restore_body(body, table)

    payload = json.loads(restored_body.decode("utf-8"))
    assert payload["choices"][0]["message"]["content"] == "客戶 A123456789 的電話是 0912345678"
    assert (count, unknown) == (2, 0)


def test_還原後仍是合法_json_即使真值含特殊字元():
    """真值若含引號或反斜線，直接字串取代會破壞 JSON —— 必須先做逸出。"""
    table = MappingTable()
    token = table.token_for("API_KEY", 'sk-"weird"\\key')
    body = json.dumps({"content": f"金鑰是 {token}"}, ensure_ascii=False).encode("utf-8")

    restored_body, count, _ = restorer.restore_body(body, table)

    payload = json.loads(restored_body.decode("utf-8"))  # 不能炸
    assert payload["content"] == '金鑰是 sk-"weird"\\key'
    assert count == 1


def test_查不到的佔位符原樣保留_不猜測(table):
    body = json.dumps({"content": "[TW_ID_1] 與 [TW_ID_9]"}, ensure_ascii=False).encode(
        "utf-8"
    )

    restored_body, count, unknown = restorer.restore_body(body, table)

    payload = json.loads(restored_body.decode("utf-8"))
    assert payload["content"] == "A123456789 與 [TW_ID_9]"
    assert (count, unknown) == (1, 1)


def test_無法解碼的_body_原樣回傳(table):
    body = b"\xff\xfe not utf-8"
    assert restorer.restore_body(body, table) == (body, 0, 0)


# ---------------------------------------------------------------- 串流


def _drain(chunks: list[str], table: MappingTable) -> str:
    stream = restorer.StreamRestorer(table)
    return "".join(stream.feed(c) for c in chunks) + stream.flush()


def test_佔位符被切成兩半也能還原(table):
    assert _drain(["客戶 [TW_", "ID_1] 你好"], table) == "客戶 A123456789 你好"


def test_佔位符被切成三段也能還原(table):
    assert _drain(["客戶 [", "TW_ID", "_1] 你好"], table) == "客戶 A123456789 你好"


def test_一次一個字元也能還原(table):
    """最極端的切法：逐字元送達。"""
    text = "客戶 [TW_ID_1] 電話 [TW_PHONE_M_1]"
    assert _drain(list(text), table) == "客戶 A123456789 電話 0912345678"


def test_不管怎麼切結果都一樣(table):
    """同一段文字用各種切法，還原後必須完全相同。"""
    text = "前面 [TW_ID_1] 中間 [TW_PHONE_M_1] 後面"
    expected = "前面 A123456789 中間 0912345678 後面"

    for size in range(1, len(text) + 1):
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        assert _drain(chunks, table) == expected, f"切成 {size} 字一段時不一致"


def test_未閉合的中括號不會永遠卡住輸出(table):
    """程式碼裡的 `[` 若一直等不到 `]`，超過長度上限就要放行。"""
    long_tail = "[" + "x" * 100
    assert _drain(["前面 ", long_tail], table) == f"前面 {long_tail}"


def test_一般的中括號語法原樣通過(table):
    assert _drain(["items[0] = list[i]"], table) == "items[0] = list[i]"


def test_flush_會吐出殘留的內容(table):
    stream = restorer.StreamRestorer(table)
    emitted = stream.feed("結尾是半個 [TW_")

    assert "[TW_" not in emitted  # 還沒放行
    assert stream.flush() == "[TW_"  # 收尾時原樣吐出


def test_串流會累計還原筆數(table):
    stream = restorer.StreamRestorer(table)
    stream.feed("[TW_ID_1] 和 [TW_")
    stream.feed("PHONE_M_1] 和 [TW_ID_9]")
    stream.flush()

    assert stream.restored == 2
    assert stream.unknown == 1
