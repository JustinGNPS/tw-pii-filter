"""遮蔽測試。

核心驗收條件：遮蔽後的 payload 裡**找不到任何一個原始個資字串**。
"""

import json

from proxy import config, masker
from core.redact.mapping import MappingTable


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


def test_同型別多筆時_號碼依出現順序():
    """替換要從後往前，但發號碼不該跟著倒過來。

    否則文章裡第一個出現的人會拿到 [NAME_2]、第二個拿到 [NAME_1]，
    功能無害但 demo 時看的人會困惑。
    """
    table = MappingTable()
    text = "王小明 找 陳大同"
    spans = [
        {"start": 0, "end": 3, "type": "name", "text": "王小明"},
        {"start": 6, "end": 9, "type": "name", "text": "陳大同"},
    ]

    assert masker.mask_text(text, spans, table) == "[NAME_1] 找 [NAME_2]"


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


class Test語意層的型別:
    """語意層（D 的 NER）的 span 會經由 `detect_all(extra_spans=...)` 一起進來，
    型別代碼是小寫的 name / address / position。
    """

    def test_小寫型別遮蔽後仍還原得回原文(self):
        table = MappingTable()
        text = "客戶 王小明 住在 台北市信義區虛構路 1 號"
        spans = [
            {"start": 3, "end": 6, "type": "name", "text": "王小明"},
            {"start": 10, "end": 23, "type": "address", "text": "台北市信義區虛構路 1 號"},
        ]

        masked = masker.mask_text(text, spans, table)
        restored, count, unknown = table.restore_text(masked)

        assert masked == "客戶 [NAME_1] 住在 [ADDRESS_1]"
        assert restored == text
        assert (count, unknown) == (2, 0)

    def test_預設跳過_position_不遮也不進對照表(self):
        table = MappingTable()
        text = "客服 王小明 您好"
        spans = [
            {"start": 0, "end": 2, "type": "position", "text": "客服"},
            {"start": 3, "end": 6, "type": "name", "text": "王小明"},
        ]

        masked = masker.mask_text(text, spans, table)

        assert masked == "客服 [NAME_1] 您好"
        assert len(table) == 1  # 只有姓名進表，職稱沒發過號碼

    def test_跳過清單可以指定_大小寫都吃(self):
        table = MappingTable()
        text = "王小明 住 台北市"
        spans = [
            {"start": 0, "end": 3, "type": "name", "text": "王小明"},
            {"start": 6, "end": 9, "type": "address", "text": "台北市"},
        ]

        masked = masker.mask_text(text, spans, table, skip_types=["address"])

        assert masked == "[NAME_1] 住 台北市"

    def test_跳過清單設成空的就什麼都遮(self):
        table = MappingTable()
        spans = [{"start": 0, "end": 2, "type": "position", "text": "客服"}]

        masked = masker.mask_text("客服 您好", spans, table, skip_types=[])

        assert masked == "[POSITION_1] 您好"

    def test_摘要只計實際遮掉的筆數(self, monkeypatch):
        """log 說「已遮蔽 N 筆」時，那 N 筆必須真的被遮掉了。"""
        table = MappingTable()
        payload = {"messages": [{"role": "user", "content": "客服 A123456789"}]}
        # scan_payload 走的是 A 的規則層，這裡直接假造含語意層型別的結果
        monkeypatch.setattr(
            masker.detector,
            "scan_payload",
            lambda _payload, _cache=None: [
                {
                    "path": ("messages", 0, "content"),
                    "text": "客服 A123456789",
                    "spans": [
                        {"start": 0, "end": 2, "type": "position", "text": "客服"},
                        {"start": 3, "end": 13, "type": "TW_ID", "text": "A123456789"},
                    ],
                }
            ],
        )

        counts = masker.mask_payload(payload, table)

        assert counts == {"TW_ID": 1}  # position 沒被算進去
        assert payload["messages"][0]["content"] == "客服 [TW_ID_1]"

    def test_整個欄位都被跳過時_原樣不動(self, monkeypatch):
        table = MappingTable()
        payload = {"messages": [{"role": "user", "content": "客服 您好"}]}
        monkeypatch.setattr(
            masker.detector,
            "scan_payload",
            lambda _payload, _cache=None: [
                {
                    "path": ("messages", 0, "content"),
                    "text": "客服 您好",
                    "spans": [
                        {"start": 0, "end": 2, "type": "position", "text": "客服"}
                    ],
                }
            ],
        )

        counts = masker.mask_payload(payload, table)

        assert counts == {}
        assert payload["messages"][0]["content"] == "客服 您好"
        assert len(table) == 0


def test_預設跳過清單來自設定():
    """預設值必須真的是 POSITION + COMPANY，不是靠測試自己傳參數才成立。

    `COMPANY` 是 08-14 的決定 13 加進來的（公司／店名不是個人識別資料，
    遮掉會讓 agent 讀不懂程式碼）。詳見 `proxy/config.py` 的 `DEFAULT_SKIP_TYPES`。
    """
    assert config.SKIP_TYPES == frozenset({"POSITION", "COMPANY"})
    assert masker.resolve_skip_types(None) == config.SKIP_TYPES


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
