"""本機示範介面（`proxy/demo.py`）。

兩個設計決定要靠測試釘住，否則日後很容易被「順手簡化」掉：

1. **預設關閉，關閉時回 404**（不是 403）—— 沒開就等於不存在。
2. **示範用自己的對照表**，不碰 agent 正在用的那張。共用的話，在頁面上
   試打幾段文字就會讓 agent 的佔位符跳號，更糟的是把示範資料的真值
   混進正在服務 agent 的對照表裡。
"""

import pytest
from fastapi.testclient import TestClient

from proxy import config, demo, main

SAMPLE = "客戶 A123456789 電話 0912-345678"


@pytest.fixture(autouse=True)
def clean_demo_state():
    demo.DEMO_TABLE.clear()
    demo.DEMO_CACHE.clear()
    yield
    demo.DEMO_TABLE.clear()


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_DEMO", False)
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", "https://upstream.test/v1")
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_DEMO", True)
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", "https://upstream.test/v1")
    with TestClient(main.app) as client:
        yield client


# ---------------------------------------------------------------- 開關


@pytest.mark.parametrize(
    "method,path",
    [("get", "/demo"), ("post", "/demo/scan"), ("post", "/demo/reset"), ("get", "/demo/status")],
)
def test_預設關閉時所有端點都是404(off, method, path):
    """回 404 而不是 403 —— 沒開就等於不存在，不要洩漏「這裡有東西」。"""
    kwargs = {"json": {"text": SAMPLE}} if method == "post" else {}
    response = getattr(off, method)(path, **kwargs)

    assert response.status_code == 404


def test_開啟後頁面拿得到(on):
    response = on.get("/demo")

    assert response.status_code == 200
    assert "tw-pii-filter 示範台" in response.text


def test_demo_路徑不會被萬用轉發路由接走(on):
    """`main.py` 底部有 `/{path:path}` 萬用路由。示範 router 若註冊得比它晚，
    `/demo` 會被當成要轉發給上游的請求 —— 這裡守的是註冊順序。
    """
    response = on.get("/demo")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


# ---------------------------------------------------------------- 掃描


def test_掃描會遮蔽並回報佔位符(on):
    data = on.post("/demo/scan", json={"text": SAMPLE}).json()

    assert "A123456789" not in data["masked"]
    assert "[TW_ID_1]" in data["masked"]
    assert data["counts"]["TW_ID"] == 1
    assert {"token": "[TW_ID_1]", "value": "A123456789"} in data["mapping"]


def test_回傳的佔位符與遮蔽結果一致(on):
    """號碼由 proxy 自己發，不是偵測核心建議的 `replacement` —— 兩者可能不同。

    明細裡的 token 必須是**實際換進文字裡的那個**，否則頁面會顯示一組
    對不上的對應關係。
    """
    data = on.post("/demo/scan", json={"text": SAMPLE}).json()

    for span in data["spans"]:
        if span["token"]:
            assert span["token"] in data["masked"]


def test_同一個真值重複出現只配一個佔位符(on):
    data = on.post("/demo/scan", json={"text": "A123456789 又出現 A123456789"}).json()

    assert data["masked"].count("[TW_ID_1]") == 2
    assert len(data["mapping"]) == 1


def test_不遮蔽的型別會標示出來而不是消失(on, monkeypatch):
    """`SKIP_TYPES` 的東西偵測得到但不換 —— 頁面要看得到它被偵測到了。"""
    monkeypatch.setattr(config, "SKIP_TYPES", frozenset({"TW_ID"}))

    data = on.post("/demo/scan", json={"text": SAMPLE}).json()

    tw_id = [s for s in data["spans"] if s["type"] == "TW_ID"][0]
    assert tw_id["skipped"] is True
    assert tw_id["token"] is None
    assert "A123456789" in data["masked"]  # 原文保留


def test_空字串不會爆(on):
    data = on.post("/demo/scan", json={"text": ""}).json()

    assert data["masked"] == ""
    assert data["spans"] == []


def test_帶回耗時與快取數字(on):
    data = on.post("/demo/scan", json={"text": SAMPLE}).json()

    assert data["timing"]["detect_ms"] >= 0
    assert "hit_rate" in data["cache"]


# ---------------------------------------------------------------- 隔離


def test_示範不會污染_agent_正在用的對照表(on):
    """這是最重要的一條：兩張表必須完全分開。"""
    on.post("/demo/scan", json={"text": SAMPLE})

    live = on.get("/demo/status").json()
    assert live["live_mapping_entries"] == 0
    assert live["demo_mapping_entries"] == 2


def test_重設只清示範的那張表(on):
    on.post("/demo/scan", json={"text": SAMPLE})
    assert len(demo.DEMO_TABLE) == 2

    response = on.post("/demo/reset")

    assert response.json()["mapping_entries"] == 0
    assert len(demo.DEMO_TABLE) == 0


def test_status_回報的是真實設定(on, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_MODEL", "gpt-4.1-mini")

    data = on.get("/demo/status").json()

    assert data["upstream_configured"] is True
    assert data["model"] == "gpt-4.1-mini"
    assert data["ner_enabled"] is False
    assert "requests" in data["traffic"]


# ---------------------------------------------------------------- 還原（回程）


def test_還原把佔位符換回真值(on):
    on.post("/demo/scan", json={"text": SAMPLE})

    data = on.post(
        "/demo/restore", json={"text": "客戶 [TW_ID_1] 的電話是 [TW_PHONE_M_1]"}
    ).json()

    assert data["restored_text"] == "客戶 A123456789 的電話是 0912-345678"
    assert data["restored"] == 2
    assert data["unknown"] == 0


def test_查不到的佔位符原樣保留而不是猜(on):
    """雲端 AI 可能自己編出沒發過的佔位符（幻覺）。

    猜測等同於憑空捏造一筆個資塞進使用者的內容裡 —— 一律原樣保留，
    並回報 `unknown` 讓呼叫端看得見（見 docs/B_design.md 決定 5）。
    """
    on.post("/demo/scan", json={"text": SAMPLE})

    data = on.post("/demo/restore", json={"text": "另外 [TW_ID_9] 是我編的"}).json()

    assert "[TW_ID_9]" in data["restored_text"]
    assert data["restored"] == 0
    assert data["unknown"] == 1


def test_還原用的是示範那張表(on):
    """沒掃過任何東西時什麼都還原不了 —— 證明它讀的不是 agent 那張表。"""
    data = on.post("/demo/restore", json={"text": "[TW_ID_1]"}).json()

    assert data["restored"] == 0
    assert data["unknown"] == 1


def test_關閉時還原端點也是404(off):
    assert off.post("/demo/restore", json={"text": "[TW_ID_1]"}).status_code == 404


# ---------------------------------------------------------------- 即時監看


def test_事件流會記下轉發與遮蔽(on, monkeypatch):
    """agent 的每個請求都要在事件流裡留下痕跡 —— 那是「即時監看」的資料來源。"""
    import httpx
    import respx

    from proxy import traffic

    traffic.EVENTS.clear()
    with respx.mock:
        respx.post("https://upstream.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        on.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "id A123456789"}],
            },
        )

    data = on.get("/demo/events").json()
    kinds = [e["kind"] for e in data["events"]]

    assert "mask" in kinds  # 遮蔽到新個資
    assert "done" in kinds  # 請求完成
    assert data["last_id"] >= 2


def test_事件不含任何原始個資(on):
    """紅線：這個緩衝區會透過 HTTP 端點暴露出去，記了原文就是一份個資快取。"""
    import httpx
    import respx

    from proxy import traffic

    traffic.EVENTS.clear()
    with respx.mock:
        respx.post("https://upstream.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        on.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "id A123456789 tel 0912-345678"}],
            },
        )

    body = on.get("/demo/events").text

    assert "A123456789" not in body
    assert "0912" not in body


def test_since_只拿新的部分(on):
    from proxy import traffic

    traffic.EVENTS.clear()
    for i in range(5):
        traffic.EVENTS.record("done", status=200)

    data = on.get("/demo/events?since=3").json()

    assert [e["id"] for e in data["events"]] == [4, 5]
    assert data["last_id"] == 5


def test_關閉時事件端點也是404(off):
    assert off.get("/demo/events").status_code == 404
