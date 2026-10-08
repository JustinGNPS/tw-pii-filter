"""Codex CLI 相容性：Responses API（`POST /v1/responses`）的欄位萃取與遮蔽。

Codex 不走 `/v1/chat/completions`，而是走 Responses API，payload 形狀完全不同：
系統提示在頂層 `instructions`，對話內容在 `input` **物件陣列**（不是字串陣列）。
`extract_texts()` 原本只認字串／字串陣列，整包 47,497 字元的請求**抓到 0 個欄位**，
真值原樣送給雲端，而 log 只顯示「偵測到 0 筆」——跟「這次剛好沒個資」長得一模一樣。

這些測試就是釘住那個洞，形狀取自 08-14 用透明錄製轉發器捕捉到的**真實** Codex
請求（樣本不進 git：含 Codex 的完整系統提示與本機路徑）。

用 respx 假造上游，不會打真實 API、不需要金鑰。
"""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from core.redact import restorer
from proxy import config, detector, main
from core.redact.mapping import MappingTable

UPSTREAM = "https://upstream.test/v1"

# 真實 Codex 請求的最小骨架：三種 input 項目型態各一。
# 個資是專案慣用的假資料（A123456789 通過檢核碼、0912-345678 是格式合法的假號）。
CODEX_PAYLOAD = {
    "model": "gpt-4.1-mini",
    "instructions": "You are Codex. 使用者的聯絡電話是 0912-345678。",
    "input": [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "幫我看 customer_export.py"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "我讀一下這個檔案"}],
        },
        {
            "type": "function_call",
            "name": "shell",
            "call_id": "call_1",
            "arguments": '{"command":"Get-Content customer_export.py -Raw"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": 'Exit code: 0\nOutput:\n{"id": "A123456789"}',
        },
    ],
    "stream": True,
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    with TestClient(main.app) as test_client:
        yield test_client


# ------------------------------------------------------- 欄位萃取（純函式）


def test_萃取_Responses_payload_的每一種項目型態():
    """三種項目型態加頂層 instructions，一個都不能漏。"""
    found = dict(detector.extract_texts(CODEX_PAYLOAD))

    assert found[("instructions",)].startswith("You are Codex.")
    assert found[("input", 0, "content", 0, "text")] == "幫我看 customer_export.py"
    assert found[("input", 1, "content", 0, "text")] == "我讀一下這個檔案"
    assert found[("input", 2, "arguments")].startswith('{"command"')
    assert found[("input", 3, "output")].endswith('{"id": "A123456789"}')
    assert len(found) == 5


def test_修好之前抓到的是零個欄位():
    """把這包 payload 的字元數算出來，釘住「不是沒東西可掃」這件事。

    修好之前 `extract_texts()` 對同一包 payload 回傳空清單，log 會顯示
    「偵測到 0 筆」——本測試的存在就是為了讓那個狀態不可能悄悄回來。
    """
    texts = detector.extract_texts(CODEX_PAYLOAD)

    assert len(texts) > 0
    assert sum(len(text) for _, text in texts) > 100


def test_input_物件陣列不會影響_embeddings_的字串陣列():
    """`input` 這個欄位最早是為 embeddings 寫的，舊行為不能被破壞。"""
    assert detector.extract_texts({"input": ["a", "b"]}) == [
        (("input", 0), "a"),
        (("input", 1), "b"),
    ]
    assert detector.extract_texts({"input": "c"}) == [(("input",), "c")]


def test_output_是_block_陣列時也收得到():
    """實測到的 `output` 都是字串，但規格允許 block 陣列。"""
    payload = {
        "input": [
            {
                "type": "function_call_output",
                "output": [{"type": "output_text", "text": "身分證 A123456789"}],
            }
        ]
    }

    assert detector.extract_texts(payload) == [
        (("input", 0, "output", 0, "text"), "身分證 A123456789")
    ]


def test_壞掉的_input_項目不會爆炸():
    payload = {"input": [None, 42, {}, {"content": None}, {"arguments": 5}]}

    assert detector.extract_texts(payload) == []


def test_萃取出來的路徑寫得回去():
    """遮蔽要靠同一條路徑把替換後的文字寫回原位。"""
    payload = json.loads(json.dumps(CODEX_PAYLOAD))  # 深拷貝，不動到模組層常數

    detector.set_at(payload, ("input", 3, "output"), "遮蔽後")
    detector.set_at(payload, ("instructions",), "遮蔽後的系統提示")

    assert payload["input"][3]["output"] == "遮蔽後"
    assert payload["instructions"] == "遮蔽後的系統提示"


# ------------------------------------------------------- 端到端（假上游）


@respx.mock
def test_送到上游的_Responses_請求已經遮蔽(client):
    """最關鍵的一項：上游收到的 body 裡不能有真值。

    08-14 的實測反例是問 AI「`A123456789` 有幾個字元」，它答 10（`[TW_ID_1]`
    是 9 個字元）——代表它看到的是真實身分證字號。
    """
    route = respx.post(f"{UPSTREAM}/responses").mock(
        return_value=httpx.Response(200, json={"id": "resp_1"})
    )

    client.post("/v1/responses", json=CODEX_PAYLOAD)

    sent = route.calls.last.request.content.decode("utf-8")
    assert "A123456789" not in sent
    assert "0912-345678" not in sent
    assert "[TW_ID_1]" in sent
    assert "[TW_PHONE_M_1]" in sent

    # 遮蔽只換掉個資本身，其餘內容原樣送達（agent 才讀得懂）
    body = json.loads(sent)
    assert body["input"][2]["arguments"].startswith('{"command"')
    assert body["input"][0]["content"][0]["text"] == "幫我看 customer_export.py"


@respx.mock
def test_工具執行結果與系統提示都被掃到(client):
    """`function_call_output.output`（檔案內容）與 `instructions` 是兩個
    原本完全不在掃描範圍內的欄位，各自釘一個斷言。"""
    route = respx.post(f"{UPSTREAM}/responses").mock(
        return_value=httpx.Response(200, json={})
    )

    client.post("/v1/responses", json=CODEX_PAYLOAD)

    body = json.loads(route.calls.last.request.content.decode("utf-8"))
    assert "[TW_ID_1]" in body["input"][3]["output"]
    assert "[TW_PHONE_M_1]" in body["instructions"]


@respx.mock
def test_Responses_的_SSE_回覆會被還原(client):
    """Responses API 的串流事件形狀跟 Chat Completions 不同（`response.output_text.delta`），
    但還原是位元組層級的，跟事件形狀無關——這裡連「佔位符被切成兩半」一起驗。
    """
    stream = (
        b'data: {"type":"response.output_text.delta","delta":"\\u4f60\\u7684 [TW"}\n\n'
        b'data: {"type":"response.output_text.delta","delta":"_ID_1] \\u5df2\\u6536\\u5230"}\n\n'
        b'data: {"type":"response.completed"}\n\n'
    )
    respx.post(f"{UPSTREAM}/responses").mock(
        side_effect=[
            httpx.Response(200, json={"id": "resp_1"}),
            httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream
            ),
        ]
    )

    client.post("/v1/responses", json=CODEX_PAYLOAD)  # 先建立對照
    response = client.post("/v1/responses", json={"input": [], "stream": True})

    text = ""
    for line in response.content.decode("utf-8").split("\n"):
        if not line.startswith("data: "):
            continue
        text += json.loads(line[6:]).get("delta", "")

    assert text == "你的 A123456789 已收到"


# ------------------------------------------- Responses 串流事件（單元層）


@pytest.fixture
def table():
    t = MappingTable()
    t.token_for("TW_ID", "A123456789")  # [TW_ID_1]
    return t


def _feed(raw: str, table: MappingTable, chunk_size: int = 4096) -> str:
    """把整串 SSE 餵進 SSERestorer，回傳 proxy 實際送給 agent 的內容。"""
    sse = restorer.SSERestorer(table)
    data = raw.encode("utf-8")
    out = b""
    for i in range(0, len(data), chunk_size):
        out += sse.feed(data[i : i + chunk_size])
    out += sse.flush()
    return out.decode("utf-8")


def _deltas(stream: str, event_type: str) -> str:
    """把某一種事件的 delta 依序拼回來（模擬 agent 端的累加）。"""
    text = ""
    for line in stream.split("\n"):
        if not line.startswith("data: "):
            continue
        obj = json.loads(line[6:])
        if obj.get("type") == event_type:
            text += obj.get("delta", "")
    return text


def test_工具參數的_delta_也會還原(table):
    """最嚴重的一條路徑：`function_call_arguments` 是 agent 要寫進檔案的內容。

    漏掉的話佔位符會原封不動被寫進使用者的程式碼——比沒遮到更糟（OpenCode
    那次踩過的坑，這裡是同一個坑的 Responses 版）。
    """
    raw = (
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1",'
        '"delta":"{\\"content\\":\\"id = [TW"}\n\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1",'
        '"delta":"_ID_1]\\"}"}\n\n'
    )

    out = _feed(raw, table)

    assert _deltas(out, "response.function_call_arguments.delta") == (
        '{"content":"id = A123456789"}'
    )


def test_多個_output_item_的_buffer_不會互相插斷(table):
    """一個回覆可能同時串流文字與多個工具呼叫，共用 buffer 會拼錯。"""
    raw = (
        'data: {"type":"response.output_text.delta","item_id":"msg_1","delta":"文字 [TW"}\n\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"參數 [TW"}\n\n'
        'data: {"type":"response.output_text.delta","item_id":"msg_1","delta":"_ID_1] 結束"}\n\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"_ID_1] 結束"}\n\n'
    )

    out = _feed(raw, table)

    assert _deltas(out, "response.output_text.delta") == "文字 A123456789 結束"
    assert _deltas(out, "response.function_call_arguments.delta") == "參數 A123456789 結束"


def test_response_completed_的完整內容也會還原(table):
    """Codex 是從 `response.completed` 取最終結果的，只還原 delta 不夠。"""
    raw = (
        'data: {"type":"response.completed","response":{"output":[{"type":"message",'
        '"content":[{"type":"output_text","text":"查到 [TW_ID_1] 了"}]}]}}\n\n'
    )

    out = _feed(raw, table)

    obj = json.loads(out.split("data: ", 1)[1].strip())
    text = obj["response"]["output"][0]["content"][0]["text"]
    assert text == "查到 A123456789 了"


def test_半個佔位符在結束事件之前就被送出(table):
    """`.done` 事件之前必須把 buffer 清空，否則靠累加 delta 的 agent 會少收尾巴。

    這裡的 `[TW_ID_1` 缺了 `]`，永遠等不到 —— 必須原樣吐回去，不能吞掉。
    """
    raw = (
        'data: {"type":"response.output_text.delta","item_id":"msg_1","delta":"前面 [TW_ID_1"}\n\n'
        'data: {"type":"response.output_text.done","item_id":"msg_1","text":"前面 [TW_ID_1"}\n\n'
    )

    out = _feed(raw, table)

    # agent 累加所有 delta 之後，一個字都沒少（`feed()` 會先送安全的前段
    # 「前面 」，剩下的 `[TW_ID_1` 由殘留事件補上）
    assert _deltas(out, "response.output_text.delta") == "前面 [TW_ID_1"
    # 而且殘留事件排在結束事件之前
    types = [
        json.loads(line[6:])["type"]
        for line in out.split("\n")
        if line.startswith("data: ")
    ]
    assert types == [
        "response.output_text.delta",  # 安全的前段
        "response.output_text.delta",  # 殘留的半個佔位符
        "response.output_text.done",
    ]


def test_殘留內容帶著原本的_item_id(table):
    """殘留片段要沿用該 item 最後一個事件的外框，agent 才接得回正確位置。"""
    raw = 'data: {"type":"response.output_text.delta","item_id":"msg_7","output_index":2,"delta":"尾巴 [TW_ID_1"}\n\n'

    out = _feed(raw, table)

    events = [json.loads(line[6:]) for line in out.split("\n") if line.startswith("data: ")]
    leftover = events[-1]
    assert leftover["item_id"] == "msg_7"
    assert leftover["output_index"] == 2
    assert leftover["delta"] == "[TW_ID_1"  # 「尾巴 」已經在前一個事件送出去了
    assert _deltas(out, "response.output_text.delta") == "尾巴 [TW_ID_1"


def test_chat_completions_的串流不受影響(table):
    """兩種格式共用同一個類別，舊格式的行為不能被新分流改掉。"""
    raw = (
        'data: {"choices":[{"index":0,"delta":{"content":"你的 [TW"}}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{"content":"_ID_1] 已收到"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    out = _feed(raw, table)

    text = ""
    for line in out.split("\n"):
        if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
            continue
        for choice in json.loads(line[6:]).get("choices", []):
            text += choice.get("delta", {}).get("content", "")
    assert text == "你的 A123456789 已收到"


@respx.mock
def test_非串流的_Responses_回覆也會還原(client):
    """Codex 預設 `stream: true`，但 `--json` 之類的用法會拿非串流回覆。"""
    respx.post(f"{UPSTREAM}/responses").mock(
        side_effect=[
            httpx.Response(200, json={"id": "resp_1"}),
            httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "查到 [TW_ID_1] 了"}
                            ],
                        }
                    ]
                },
            ),
        ]
    )

    client.post("/v1/responses", json=CODEX_PAYLOAD)
    response = client.post("/v1/responses", json={"input": []})

    text = response.json()["output"][0]["content"][0]["text"]
    assert text == "查到 A123456789 了"
