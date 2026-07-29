"""遮蔽測試。

核心驗收條件：遮蔽後的 payload 裡**找不到任何一個原始個資字串**。
"""

import json

from proxy import masker
from proxy.mapping import MappingTable


def test_從後往前替換_後面的座標不會偏掉():
    """一段文字有多筆個資時，前面的替換不能弄壞後面的座標。"""
    table = MappingTable()
    text = "身分證 A123456789 手機 0912345678 信箱 test@example.com"
    spans = [
        {"start": 4, "end": 14, "type": "TW_ID", "text": "A123456789"},
        {"start": 18, "end": 28, "type": "TW_PHONE_M", "text": "0912345678"},
        {"start": 32, "end": 48, "type": "EMAIL", "text": "test@example.com"},
    ]

    masked = masker.mask_text(text, spans, table)

    assert masked == "身分證 [TW_ID_1] 手機 [TW_PHONE_M_1] 信箱 [EMAIL_1]"


def test_遮蔽後的_payload_不含任何原始個資():
    table = MappingTable()
    payload = {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": "你是助理"},
            {
                "role": "user",
                "content": "客戶 A123456789 電話 0912345678 統編 12345675",
            },
        ],
    }

    counts = masker.mask_payload(payload, table)

    dumped = json.dumps(payload, ensure_ascii=False)
    for secret in ("A123456789", "0912345678", "12345675"):
        assert secret not in dumped, f"{secret} 沒有被遮蔽"
    assert counts == {"TW_ID": 1, "TW_PHONE_M": 1, "TW_TAX": 1}
    # 沒有個資的欄位不該被動到
    assert payload["messages"][0]["content"] == "你是助理"


def test_遮蔽是可逆的():
    """遮蔽 → 還原應該回到原文，一個字都不差。"""
    table = MappingTable()
    original = "客戶 A123456789 的信箱是 lihua@example.com"
    payload = {"messages": [{"role": "user", "content": original}]}

    masker.mask_payload(payload, table)
    masked = payload["messages"][0]["content"]
    restored, count, unknown = table.restore_text(masked)

    assert masked != original
    assert restored == original
    assert (count, unknown) == (2, 0)


def test_同一份個資出現多次_用同一個佔位符():
    table = MappingTable()
    payload = {
        "messages": [
            {"role": "user", "content": "A123456789 的資料"},
            {"role": "assistant", "content": "你是說 A123456789 嗎"},
        ]
    }

    masker.mask_payload(payload, table)

    assert payload["messages"][0]["content"] == "[TW_ID_1] 的資料"
    assert payload["messages"][1]["content"] == "你是說 [TW_ID_1] 嗎"
    assert len(table) == 1


def test_沒有個資時_payload_完全不動():
    table = MappingTable()
    payload = {"messages": [{"role": "user", "content": "今天天氣如何"}]}
    before = json.dumps(payload, ensure_ascii=False)

    counts = masker.mask_payload(payload, table)

    assert counts == {}
    assert json.dumps(payload, ensure_ascii=False) == before
    assert len(table) == 0


def test_多模態_content_parts_也會被遮蔽():
    table = MappingTable()
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "身分證 A123456789"},
                    {"type": "image_url", "image_url": {"url": "http://x"}},
                ],
            }
        ]
    }

    masker.mask_payload(payload, table)

    assert payload["messages"][0]["content"][0]["text"] == "身分證 [TW_ID_1]"
    # 非文字的部分不該被動到
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == "http://x"
