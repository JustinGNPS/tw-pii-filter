"""SSE 串流還原測試。

驗收條件：**不管上游怎麼切位元組、AI 怎麼把佔位符拆進不同事件，
agent 最後拼起來看到的文字都要是還原後的正確內容。**
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


def _event(content: str) -> str:
    obj = {"id": "x", "choices": [{"index": 0, "delta": {"content": content}}]}
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _stream(contents: list[str]) -> bytes:
    return ("".join(_event(c) for c in contents) + "data: [DONE]\n\n").encode("utf-8")


def _agent_sees(raw: bytes, table: MappingTable, chunk_size: int) -> str:
    """模擬 agent：把 proxy 吐出來的串流收完，拼回完整文字。"""
    sse = restorer.SSERestorer(table)
    out = b""
    for i in range(0, len(raw), chunk_size):
        out += sse.feed(raw[i : i + chunk_size])
    out += sse.flush()

    text = ""
    for block in out.decode("utf-8").split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                continue
            for choice in json.loads(data).get("choices", []):
                text += choice.get("delta", {}).get("content", "")
    return text


def test_佔位符完整落在單一事件裡(table):
    raw = _stream(["客戶 ", "[TW_ID_1]", " 你好"])
    assert _agent_sees(raw, table, 4096) == "客戶 A123456789 你好"


def test_佔位符跨兩個事件(table):
    """AI 逐 token 產生時最常見的情況。"""
    raw = _stream(["客戶 [TW", "_ID_1] 你好"])
    assert _agent_sees(raw, table, 4096) == "客戶 A123456789 你好"


def test_佔位符被拆成逐字元的事件(table):
    raw = _stream(list("客戶 [TW_ID_1] 電話 [TW_PHONE_M_1]"))
    assert _agent_sees(raw, table, 4096) == "客戶 A123456789 電話 0912345678"


def test_不管位元組怎麼切結果都一樣(table):
    """上游的 TCP 分段是任意的，中文字也可能被切在位元組之間。"""
    raw = _stream(["客戶 [TW", "_ID_1] 的電話是 [TW_PHONE", "_M_1]，請確認"])
    expected = "客戶 A123456789 的電話是 0912345678，請確認"

    for size in (1, 2, 3, 7, 16, 64, 4096):
        assert _agent_sees(raw, table, size) == expected, f"切成 {size} 位元組時不一致"


def test_結尾是半個佔位符也不會遺失(table):
    """[DONE] 之前必須把 buffer 清乾淨，否則結尾內容會消失。"""
    raw = _stream(["結尾 [TW_ID_1]"])
    assert _agent_sees(raw, table, 4096) == "結尾 A123456789"


def test_查不到的佔位符原樣送達(table):
    raw = _stream(["[TW_ID_1] 與 [TW_ID_9]"])
    assert _agent_sees(raw, table, 4096) == "A123456789 與 [TW_ID_9]"


def test_一般中括號語法不受影響(table):
    raw = _stream(["arr[0] = ", "lst[i]"])
    assert _agent_sees(raw, table, 4096) == "arr[0] = lst[i]"


def test_DONE_標記會被保留(table):
    sse = restorer.SSERestorer(table)
    out = sse.feed(_stream(["[TW_ID_1]"])) + sse.flush()
    assert "data: [DONE]" in out.decode("utf-8")


def test_不是_JSON_的事件原樣通過(table):
    sse = restorer.SSERestorer(table)
    out = sse.feed(b": keep-alive\n\ndata: not-json\n\n") + sse.flush()
    text = out.decode("utf-8")
    assert ": keep-alive" in text
    assert "data: not-json" in text


def test_會累計還原筆數(table):
    sse = restorer.SSERestorer(table)
    sse.feed(_stream(["[TW_ID_1] [TW_", "PHONE_M_1] [TW_ID_9]"]))
    sse.flush()

    assert sse.restored == 2
    assert sse.unknown == 1
