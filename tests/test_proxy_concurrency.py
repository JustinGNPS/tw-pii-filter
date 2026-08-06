"""proxy 端到端的併發測試：多個請求真的同時打進 `_proxy()`，共用同一個
`app.state.mapping`，確認不會互相污染。

跟 `tests/test_mapping_concurrency.py` 的差別：那份只測 `MappingTable` 這個
資料結構本身；這份測的是**整條路徑**（`_mask_request` 的 `asyncio.to_thread`
+ 遮蔽 + 轉發 + 還原）在併發下是否每個請求都拿回屬於自己的那筆真值，
不會因為共用同一張表就把不同請求的個資搞混。
"""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from core.rules.tw_id import _LETTER_MAP, _WEIGHTS
from proxy import config, main

UPSTREAM = "https://upstream.test/v1"


def _valid_tw_id(letter: str, first_8_digits: str) -> str:
    """算出 checksum 正確的身分證字號，供測試產生大量『格式合法、彼此不同』
    的假號碼——不能隨便湊數字，`detect_tw_id` 會把 checksum 錯的濾掉。
    """
    n1, n2 = divmod(_LETTER_MAP[letter], 10)
    values = [n1, n2] + [int(d) for d in first_8_digits]
    partial_total = sum(v * w for v, w in zip(values, _WEIGHTS[:10]))
    check_digit = (10 - (partial_total % 10)) % 10
    return f"{letter}{first_8_digits}{check_digit}"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    with TestClient(main.app) as test_client:
        yield test_client


@respx.mock
def test_併發請求_各自拿回自己的真值_不會被別人的請求污染(client):
    """N 個請求各帶一個獨一無二的身分證字號同時發出。上游假裝把佔位符
    原封不動echo回來（模擬 LLM 在回覆裡提到這筆個資），驗證每個請求
    收到的還原結果都是**自己送出去的那個真值**，不是別人的。
    """
    n_requests = 30
    # 30 個 checksum 正確、彼此不同的身分證字號（隨便湊數字會被 is_valid_tw_id 濾掉）
    tw_ids = [_valid_tw_id("A", str(i).zfill(8)) for i in range(n_requests)]

    def upstream_echo(request: httpx.Request) -> httpx.Response:
        # 上游看到的一定是佔位符，把它原樣塞進回覆內容裡echo回去
        sent = request.content.decode("utf-8")
        assert "[TW_ID_1]" in sent or any(
            f"[TW_ID_{n}]" in sent for n in range(1, n_requests + 1)
        )
        body = __import__("json").loads(sent)
        content = body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-echo",
                "choices": [{"message": {"role": "assistant", "content": content}}],
            },
        )

    respx.post(f"{UPSTREAM}/chat/completions").mock(side_effect=upstream_echo)

    def make_request(tw_id: str) -> tuple[str, str]:
        payload = {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": f"客戶身分證 {tw_id}"}],
        }
        response = client.post("/v1/chat/completions", json=payload)
        restored_content = response.json()["choices"][0]["message"]["content"]
        return tw_id, restored_content

    with ThreadPoolExecutor(max_workers=n_requests) as pool:
        results = list(pool.map(make_request, tw_ids))

    # 每個請求都必須拿回「自己送出去的那個身分證字號」，一個字都不能錯、
    # 更不能是別的請求的號碼（那就是把兩個人的個資對調了）
    for original_tw_id, restored_content in results:
        assert restored_content == f"客戶身分證 {original_tw_id}", (
            f"送出 {original_tw_id}，但還原拿回的是：{restored_content!r}"
        )
