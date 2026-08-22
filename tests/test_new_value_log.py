"""遮蔽的 log 只報「本輪新增的個資」，不報「這包 payload 遮了幾筆」。

## 這組測試在守什麼

agent 每一輪都會重送整段對話歷史，同一批個資每輪都會被重新掃到、重新遮蔽。
若直接印該輪遮掉的筆數，數字會隨對話變長一路往上爬 —— 2026-08-22 一次真實
Codex 工作階段的 log 就是這樣：

    已遮蔽：偵測到 14 筆敏感資訊…
    已遮蔽：偵測到 15 筆…
    已遮蔽：偵測到 16 筆…
    已遮蔽：偵測到 17 筆…
    已遮蔽：偵測到 17 筆…      <- 連續三行相同，零資訊量
    已遮蔽：偵測到 17 筆…

那六行裡真正「有新個資送出去」的只有前幾輪。使用者要回答的問題是
「又有沒看過的個資離開我的機器了嗎」，不是「歷史裡總共有幾筆」。
"""

import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from proxy import config, main, masker
from proxy.mapping import MappingTable

UPSTREAM = "https://upstream.test/v1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", UPSTREAM)
    monkeypatch.setattr(config, "UPSTREAM_API_KEY", "test-key")
    with TestClient(main.app) as test_client:
        yield test_client


def _payload(content: str) -> dict:
    return {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": content}]}


# ---------------------------------------------------------------- 差分本身


def test_issued_counts_只在配新號碼時增加():
    table = MappingTable(idle_timeout=None)

    table.token_for("TW_ID", "A123456789")
    table.token_for("TW_ID", "A123456789")  # 同一個真值，不該再配號
    table.token_for("TW_ID", "F131104093")

    assert table.issued_counts() == {"TW_ID": 2}


def test_同一批個資重複出現不算新增():
    before = {"TW_ID": 3, "TW_PHONE_M": 3}

    assert masker.new_value_counts(before, dict(before)) == {}


def test_只算真正多出來的那幾個():
    assert masker.new_value_counts({"TW_ID": 3}, {"TW_ID": 5, "EMAIL": 1}) == {
        "TW_ID": 2,
        "EMAIL": 1,
    }


def test_對照表被清空後整批視為新增():
    """閒置逾時清空會讓計數歸零，相減是負的。

    這種情況代表整張表重新發過號，該型別現有的號碼全部是新配的 —— 取 after
    而不是讓它變成負數或 0，否則清空後的第一輪會靜默，剛好漏掉「個資重新
    送出去」這個真正該講的事件。
    """
    assert masker.new_value_counts({"TW_ID": 9}, {"TW_ID": 2}) == {"TW_ID": 2}


# ---------------------------------------------------------------- 接上請求


@respx.mock
def test_第一輪印出新增內容(client, caplog):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )

    with caplog.at_level(logging.WARNING, logger="proxy"):
        client.post("/v1/chat/completions", json=_payload("id A123456789 tel 0912-345678"))

    masked = [r.getMessage() for r in caplog.records if "已遮蔽" in r.getMessage()]
    assert len(masked) == 1
    assert "本輪新增 2 筆個資" in masked[0]
    assert "TW_ID x1" in masked[0] and "TW_PHONE_M x1" in masked[0]


@respx.mock
def test_重送同一批個資時完全不印(client, caplog):
    """這是 agent 每輪重送歷史的實際情況 —— 舊的 log 會在這裡洗版。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )
    client.post("/v1/chat/completions", json=_payload("id A123456789"))
    # caplog.records 蒐集整個測試期間的紀錄，at_level 只調等級不清空 ——
    # 不清掉的話會把前置這一輪的警告也算進來
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="proxy"):
        client.post("/v1/chat/completions", json=_payload("id A123456789"))
        client.post("/v1/chat/completions", json=_payload("id A123456789 又提了一次 A123456789"))

    assert [r.getMessage() for r in caplog.records if "已遮蔽" in r.getMessage()] == []


@respx.mock
def test_出現沒看過的個資時才再印一次(client, caplog):
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )
    client.post("/v1/chat/completions", json=_payload("id A123456789"))
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="proxy"):
        client.post("/v1/chat/completions", json=_payload("id A123456789 另一位 F131104093"))

    masked = [r.getMessage() for r in caplog.records if "已遮蔽" in r.getMessage()]
    assert len(masked) == 1
    assert "本輪新增 1 筆個資（TW_ID x1）" in masked[0]


@respx.mock
def test_log_不含任何原始個資(client, caplog):
    """這條是紅線：log 會被貼進 issue、PR、報告。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )

    with caplog.at_level(logging.WARNING, logger="proxy"):
        client.post("/v1/chat/completions", json=_payload("id A123456789 tel 0912-345678"))

    for record in caplog.records:
        assert "A123456789" not in record.getMessage()
        assert "0912" not in record.getMessage()


@respx.mock
def test_累計狀況看得到_在healthz(client):
    """log 只講「新增」，累計不能因此消失 —— 移到 /healthz。"""
    respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={})
    )
    client.post("/v1/chat/completions", json=_payload("id A123456789 tel 0912-345678"))
    client.post("/v1/chat/completions", json=_payload("id A123456789"))

    health = client.get("/healthz").json()
    assert health["mapping_by_type"] == {"TW_ID": 1, "TW_PHONE_M": 1}
    assert health["mapping_entries"] == 2
