"""全形個資走完 B 這一側（遮蔽 → 轉發 → 還原）的整合測試。

A 在 PR #47（issue #27）替規則層加了全形→半形正規化，`detect_all()` 因此
會回報全形寫法的 span。**偵測認得出來，不等於 proxy 換得掉、還原得回去**，
這個檔案驗的是 B 這一側：

1. span 座標對全形文字仍然正確（全形 ASCII 是 1:1 等長映射，A 刻意不用
   NFKC 就是為了這件事 —— NFKC 會展開合字、長度改變、座標失效）
2. 遮蔽後的文字裡找不到原始全形個資
3. 還原之後**逐字元等於原文**，包括全形寫法本身

第 3 點是這裡最該釘住的：對照表存的是原始字面值，所以全形送進去必須全形
還原回來。如果哪天有人「順手」在遮蔽前把文字正規化成半形，使用者的原文
就會在還原後被偷偷改寫 —— 那是資料損毀，不是遮蔽。
"""

import json

from core.rules import detect_all
from proxy import masker
from proxy.mapping import MappingTable
from proxy.restorer import restore_text

# 全形與半形的同一張身分證。
FULL_ID = "Ａ１２３４５６７８９"
HALF_ID = "A123456789"
FULL_PHONE = "０９１２３４５６７８"


def test_全形個資_遮蔽後找不到原始字串():
    text = f"客戶身分證 {FULL_ID} 手機 {FULL_PHONE}"
    table = MappingTable(idle_timeout=None)

    masked = masker.mask_text(text, detect_all(text)["spans"], table)

    assert FULL_ID not in masked
    assert FULL_PHONE not in masked
    assert "[TW_ID_1]" in masked
    assert "[TW_PHONE_M_1]" in masked


def test_全形個資_還原後逐字元等於原文():
    """還原必須拿回全形寫法本身，不能悄悄變成半形。"""
    text = f"客戶身分證 {FULL_ID} 手機 {FULL_PHONE}"
    table = MappingTable(idle_timeout=None)

    masked = masker.mask_text(text, detect_all(text)["spans"], table)
    restored, count, unknown = restore_text(masked, table)

    assert restored == text
    assert count == 2
    assert unknown == 0


def test_全形與半形是不同字面值_各拿一個佔位符():
    """同一張身分證的兩種寫法會拿到兩個號碼 —— 這是刻意的。

    對照表以「字面值」為 key，因為還原時必須知道原本是哪一種寫法。
    若統一正規化成半形當 key，兩種寫法會共用一個佔位符，還原時就無法
    決定該還成 `Ａ１２３４５６７８９` 還是 `A123456789`，必然弄壞其中一邊。

    代價是計數語意：`mapping_by_type` 與「本輪新增 N 筆」算的是**不重複的
    字面值**，不是不重複的當事人。同一個人用兩種寫法會被算成 2 筆。
    這是已知取捨（見 `docs/B_design.md` 已知限制 7），不是 bug。
    """
    text = f"{FULL_ID} 與 {HALF_ID} 是同一張身分證"
    table = MappingTable(idle_timeout=None)

    masked = masker.mask_text(text, detect_all(text)["spans"], table)

    assert "[TW_ID_1]" in masked
    assert "[TW_ID_2]" in masked
    assert table.issued_counts()["TW_ID"] == 2
    # 但還原仍然無損：各自變回自己原本的寫法。
    restored, _, _ = restore_text(masked, table)
    assert restored == text


def test_全形個資_走完整_payload_路徑():
    """不是只有 mask_text，實際的請求 payload 走訪也要抓得到。"""
    payload = {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "user", "content": f"幫我查 {FULL_ID} 這位客戶"},
        ],
    }
    table = MappingTable(idle_timeout=None)

    counts = masker.mask_payload(payload, table)

    assert counts.get("TW_ID") == 1
    body = json.dumps(payload, ensure_ascii=False)
    assert FULL_ID not in body
    assert "[TW_ID_1]" in body


def test_全形混半形_座標不會互相污染():
    """全形（3 bytes/字但 1 code point）與半形混在同一段時，替換仍要對齊。

    span 是 Python 字串索引（code point），全形 ASCII 一個字元對一個
    code point，所以混排不影響 —— 這個測試是把這件事釘住，日後若有人改成
    以 byte 計算就會在這裡紅。
    """
    text = f"甲 {FULL_ID} 乙 {HALF_ID} 丙 {FULL_PHONE} 丁"
    table = MappingTable(idle_timeout=None)

    masked = masker.mask_text(text, detect_all(text)["spans"], table)

    assert masked == "甲 [TW_ID_1] 乙 [TW_ID_2] 丙 [TW_PHONE_M_1] 丁"
    restored, _, _ = restore_text(masked, table)
    assert restored == text
