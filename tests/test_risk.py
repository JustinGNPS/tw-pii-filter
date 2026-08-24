"""組合風險提示（Layer 3 接線）測試。

評分演算法本身是 D 的責任，已有 `tests/test_combination_risk.py` 覆蓋。
這裡只驗 B 這一側的接線決定：

1. 餵給評分器的是「遮蔽後的文字 + 沒被遮掉的 spans」，不是原文
2. 一包 payload 只回報分數最高的那一個欄位
3. 遮蔽結果完全不受 Layer 3 影響（只提示、不遮蔽）
4. 警示訊息不外洩被遮蔽掉的真值
"""

import json
import logging

from proxy import config, main, masker, risk
from core.redact.mapping import MappingTable

# 這段文字四個準識別子齊聚（年齡／性別／地址／職稱），是報告 4.3 節那個
# 「明碼個資全遮掉仍能指認到人」的典型例子。
RISKY_TEXT = "這位 35 歲的女性住在新竹市東區，是我們公司的資深後端工程師"


def _spans_for_risky_text():
    """模擬語意層對 RISKY_TEXT 的偵測結果（ADDRESS 會遮、POSITION 不遮）。"""
    address = "新竹市東區"
    position = "資深後端工程師"
    return [
        {
            "start": RISKY_TEXT.index(address),
            "end": RISKY_TEXT.index(address) + len(address),
            "type": "ADDRESS",
            "text": address,
        },
        {
            "start": RISKY_TEXT.index(position),
            "end": RISKY_TEXT.index(position) + len(position),
            "type": "POSITION",
            "text": position,
        },
    ]


# ---------------------------------------------------------------------------
# residual_spans：只留「沒被遮掉」的
# ---------------------------------------------------------------------------


def test_residual_spans_只留下不會被遮蔽的型別():
    """會被遮掉的型別不算殘餘風險 —— AI 只看得到佔位符，指認不到人。"""
    residual = risk.residual_spans(_spans_for_risky_text(), frozenset({"POSITION"}))

    assert [span["type"] for span in residual] == ["POSITION"]


def test_residual_spans_會正規化小寫型別代碼():
    """語意層可能吐小寫代碼，不正規化會靜默地一筆都對不上 D 的權重表。"""
    spans = [{"start": 0, "end": 3, "type": "position", "text": "工程師"}]

    residual = risk.residual_spans(spans, frozenset({"POSITION"}))

    assert [span["type"] for span in residual] == ["POSITION"]


def test_residual_spans_全部都會被遮時回傳空清單():
    spans = [{"start": 0, "end": 10, "type": "TW_ID", "text": "A123456789"}]

    assert risk.residual_spans(spans, frozenset({"POSITION"})) == []


# ---------------------------------------------------------------------------
# assess：遮蔽後的文字才是評分依據
# ---------------------------------------------------------------------------


def test_遮蔽掉的地址不計入分數():
    """關鍵設計決定：用原文算會虛報已經被自己擋掉的風險。

    同一段文字，地址遮掉後分數必須比沒遮時低 —— 否則等於在警告一個
    已經不存在的洩漏。
    """
    spans = _spans_for_risky_text()
    all_types = risk.assess(RISKY_TEXT, spans)

    masked_text = RISKY_TEXT.replace("新竹市東區", "[ADDRESS_1]")
    residual_only = risk.assess(masked_text, risk.residual_spans(spans, frozenset({"POSITION"})))

    assert "ADDRESS" in all_types["contributing_types"]
    assert "ADDRESS" not in residual_only["contributing_types"]
    assert residual_only["score"] < all_types["score"]


def test_佔位符不會被誤判成年齡或性別():
    """佔位符是英數字加底線，不該觸發 D 的中文正則。"""
    masked = "客戶 [NAME_1] 住在 [ADDRESS_1]，電話 [TW_PHONE_M_1]"

    assert risk.assess(masked, [])["contributing_types"] == []


def test_停用時不評分():
    original = config.ENABLE_RISK_WARNING
    config.ENABLE_RISK_WARNING = False
    try:
        assert risk.assess(RISKY_TEXT, _spans_for_risky_text())["score"] == 0.0
    finally:
        config.ENABLE_RISK_WARNING = original


# ---------------------------------------------------------------------------
# worse_of：一個請求只留最高分
# ---------------------------------------------------------------------------


def test_worse_of_留下分數高的那筆():
    low = {"score": 0.2}
    high = {"score": 0.8}

    assert risk.worse_of(low, high) is high
    assert risk.worse_of(high, low) is high
    assert risk.worse_of(None, low) is low


def test_worse_of_同分時保留先來的():
    """否則同一包 payload 的警示內容會隨欄位順序跳動，log 不好對照。"""
    first = {"score": 0.7, "contributing_types": ["AGE", "POSITION"]}
    second = {"score": 0.7, "contributing_types": ["GENDER", "ADDRESS"]}

    assert risk.worse_of(first, second) is first


# ---------------------------------------------------------------------------
# format_warning：能講的與不能講的
# ---------------------------------------------------------------------------


def test_警示訊息含型別分數與泛化建議():
    result = risk.assess(RISKY_TEXT, _spans_for_risky_text())

    message = risk.format_warning(result)

    assert "組合風險" in message
    assert "AGE" in message and "GENDER" in message
    assert "35-39歲" in message  # 泛化建議照原樣印（使用者確認過，log 只在本機）


def test_警示訊息不含被遮蔽掉的真值():
    """年齡可以印（本來就不遮），但被遮掉的東西一個字都不能出現在 log 裡。"""
    text = "客戶 [TW_ID_1] 是 35 歲女性，住 [ADDRESS_1]，任職資深後端工程師"
    spans = [
        {"start": text.index("資深後端工程師"), "end": len(text),
         "type": "POSITION", "text": "資深後端工程師"},
    ]

    message = risk.format_warning(risk.assess(text, spans))

    assert "A123456789" not in message
    assert "新竹市東區" not in message


# ---------------------------------------------------------------------------
# mask_payload_with_risk：接線本身
# ---------------------------------------------------------------------------


def test_風險評分不影響遮蔽結果():
    """Layer 3 只提示不遮蔽 —— 有沒有它，payload 被改動的部分要一模一樣。"""
    def build():
        return {
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "user", "content": f"我的身分證是 A123456789。{RISKY_TEXT}"}
            ],
        }

    with_risk = build()
    counts_a, assessment = masker.mask_payload_with_risk(with_risk, MappingTable())

    original = config.ENABLE_RISK_WARNING
    config.ENABLE_RISK_WARNING = False
    try:
        without_risk = build()
        counts_b = masker.mask_payload(without_risk, MappingTable())
    finally:
        config.ENABLE_RISK_WARNING = original

    assert with_risk == without_risk
    assert counts_a == counts_b
    assert assessment is not None


def test_沒有偵測到任何_span_的欄位也會被評分():
    """AGE/GENDER 是 D 的模組自己用正則抓的，不經過 span 機制。

    只看「有 span 的欄位」會漏掉語意層關閉時的純年齡／性別描述，
    這個測試把那條路徑釘住。
    """
    payload = {
        "messages": [{"role": "user", "content": "這位 35 歲的女性想諮詢"}]
    }

    counts, assessment = masker.mask_payload_with_risk(payload, MappingTable())

    assert counts == {}  # 一筆都沒遮
    assert set(assessment["contributing_types"]) == {"AGE", "GENDER"}


def test_一包_payload_只回報分數最高的欄位(monkeypatch):
    """agent 每輪重送整段歷史，逐欄位印會被同一段內容洗版，只留最高分那筆。

    POSITION 只有語意層抓得到，測試環境預設不開 NER，因此比照
    `tests/test_masker.py` 的慣例直接假造偵測結果。
    """
    payload = {
        "messages": [
            {"role": "user", "content": "今天天氣很好"},
            {"role": "user", "content": "這位 35 歲的女性想諮詢"},
            {"role": "user", "content": RISKY_TEXT},
        ]
    }
    risky_path = ("messages", 2, "content")
    monkeypatch.setattr(
        masker.detector,
        "scan_payload",
        lambda _payload, _cache=None: [
            {"path": risky_path, "text": RISKY_TEXT, "spans": _spans_for_risky_text()}
        ],
    )

    _, assessment = masker.mask_payload_with_risk(payload, MappingTable())

    # 第三則的準識別子最多，分數必須高過第二則的 AGE+GENDER
    assert "POSITION" in assessment["contributing_types"]
    assert assessment["score"] > risk.assess("這位 35 歲的女性想諮詢", [])["score"]


def test_語意層關閉時_規則層型別不會構成組合風險():
    """釘住一個誠實的限制：規則層那 8 個型別沒有一個是準識別子。

    因此語意層關閉（預設）時，可貢獻分數的只剩 AGE + GENDER = 0.50，
    永遠碰不到 0.6 的警告門檻 —— 組合風險提示實質上需要 `PII_ENABLE_NER=1`。
    這件事寫在 docs/B_design.md，用測試釘住避免哪天被誤以為壞掉。
    """
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "這位 35 歲的女性，身分證 A123456789，"
                "電話 0912345678，信箱 test@example.com",
            }
        ]
    }

    counts, assessment = masker.mask_payload_with_risk(payload, MappingTable())

    assert counts == {"TW_ID": 1, "TW_PHONE_M": 1, "EMAIL": 1}  # 明碼個資照樣全遮
    assert set(assessment["contributing_types"]) == {"AGE", "GENDER"}
    assert not risk.is_warning_worthy(assessment)


def test_沒有風險時分數為零():
    payload = {"messages": [{"role": "user", "content": "幫我把這段程式碼重構"}]}

    counts, assessment = masker.mask_payload_with_risk(payload, MappingTable())

    assert counts == {}
    assert assessment["score"] == 0.0
    assert not risk.is_warning_worthy(assessment)


def test_停用時不回傳評分():
    original = config.ENABLE_RISK_WARNING
    config.ENABLE_RISK_WARNING = False
    try:
        payload = {"messages": [{"role": "user", "content": RISKY_TEXT}]}
        _, assessment = masker.mask_payload_with_risk(payload, MappingTable())
        assert assessment is None
    finally:
        config.ENABLE_RISK_WARNING = original


def test_mask_payload_舊介面仍然只回傳筆數():
    """既有呼叫端（與 C 參照的行為）不該因為多了 Layer 3 而改變。"""
    payload = {"messages": [{"role": "user", "content": "身分證 A123456789"}]}

    assert masker.mask_payload(payload, MappingTable()) == {"TW_ID": 1}


# ---------------------------------------------------------------------------
# 兩條遮蔽路徑都要印警示
# ---------------------------------------------------------------------------


def _force_risky_scan(monkeypatch):
    """讓遮蔽層看到一筆達門檻的內容（AGE 0.35 + GENDER 0.15 + POSITION 0.20）。"""
    monkeypatch.setattr(
        main.masker.detector,
        "scan_payload",
        lambda _payload, _cache=None: [
            {
                "path": ("messages", 0, "content"),
                "text": RISKY_TEXT,
                "spans": _spans_for_risky_text(),
            }
        ],
    )


def test_openai_路徑會印出組合風險警示(monkeypatch, caplog):
    _force_risky_scan(monkeypatch)
    body = json.dumps(
        {"messages": [{"role": "user", "content": RISKY_TEXT}]}, ensure_ascii=False
    ).encode("utf-8")

    with caplog.at_level(logging.WARNING, logger="proxy"):
        main._mask_request("/v1/chat/completions", body, MappingTable())

    assert any("組合風險" in record.message for record in caplog.records)


def test_claude_code_路徑會印出組合風險警示(monkeypatch, caplog):
    """兩條路徑共用同一段遮蔽層，Layer 3 不該只有其中一條有。"""
    _force_risky_scan(monkeypatch)
    payload = {"messages": [{"role": "user", "content": RISKY_TEXT}]}

    with caplog.at_level(logging.WARNING, logger="proxy"):
        main._mask_anthropic_payload(payload, MappingTable())

    assert any("組合風險" in record.message for record in caplog.records)


def test_未達門檻不印警示(caplog):
    """0.6 以下不吵人 —— 每個請求都印會讓真正該看的那行被淹沒。"""
    body = json.dumps(
        {"messages": [{"role": "user", "content": "這位 35 歲的女性想諮詢"}]},
        ensure_ascii=False,
    ).encode("utf-8")

    with caplog.at_level(logging.WARNING, logger="proxy"):
        main._mask_request("/v1/chat/completions", body, MappingTable())

    assert not any("組合風險" in record.message for record in caplog.records)
