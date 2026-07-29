"""對照表測試。

最重要的一條：**同一個真值永遠拿到同一個佔位符**。
違反這條會導致還原時把兩個人的個資對調 —— 比洩漏更嚴重。
"""

from proxy.mapping import TOKEN_PATTERN, MappingTable


def test_同一個真值永遠拿到同一個佔位符():
    table = MappingTable()

    first = table.token_for("TW_ID", "A123456789")
    other = table.token_for("TW_ID", "F131104093")
    again = table.token_for("TW_ID", "A123456789")

    assert first == again
    assert first != other


def test_號碼依型別各自遞增():
    table = MappingTable()

    assert table.token_for("TW_ID", "A123456789") == "[TW_ID_1]"
    assert table.token_for("TW_ID", "F131104093") == "[TW_ID_2]"
    assert table.token_for("EMAIL", "a@example.com") == "[EMAIL_1]"


def test_跨請求的號碼不會被別的真值佔用():
    """模擬 agent 重送對話歷史時，A 的編號會重算、但我們的不會。"""
    table = MappingTable()

    # 第 1 次請求：只有 A123456789
    assert table.token_for("TW_ID", "A123456789") == "[TW_ID_1]"

    # 第 2 次請求：A 的 detect_all() 這次把 F131104093 編成 1 號，
    # 但我們自己發號碼，1 號仍然屬於 A123456789
    assert table.token_for("TW_ID", "F131104093") == "[TW_ID_2]"
    assert table.value_for("[TW_ID_1]") == "A123456789"


def test_查不到的佔位符回傳_None_不猜測():
    table = MappingTable()
    table.token_for("TW_ID", "A123456789")

    assert table.value_for("[TW_ID_99]") is None
    assert table.value_for("[NOT_A_TYPE_1]") is None
    assert table.value_for("完全不是佔位符") is None


def test_restore_text_換回真值並回報筆數():
    table = MappingTable()
    token = table.token_for("TW_ID", "A123456789")

    text, restored, unknown = table.restore_text(f"客戶 {token} 的資料")

    assert text == "客戶 A123456789 的資料"
    assert (restored, unknown) == (1, 0)


def test_restore_text_遇到不認識的佔位符原樣保留():
    table = MappingTable()

    text, restored, unknown = table.restore_text("AI 亂編的 [TW_ID_5]")

    assert text == "AI 亂編的 [TW_ID_5]"
    assert (restored, unknown) == (0, 1)


def test_clear_會清空並重新從_1_開始():
    table = MappingTable()
    table.token_for("TW_ID", "A123456789")
    assert len(table) == 1

    table.clear()

    assert len(table) == 0
    assert table.token_for("TW_ID", "F131104093") == "[TW_ID_1]"


def test_不同型別的相同字串各自有佔位符():
    table = MappingTable()

    as_id = table.token_for("TW_ID", "A123456789")
    as_key = table.token_for("API_KEY", "A123456789")

    assert as_id != as_key
    assert table.value_for(as_id) == table.value_for(as_key) == "A123456789"


class Test佔位符樣式:
    def test_認得所有型別代碼(self):
        types = [
            "TW_ID",
            "TW_TAX",
            "TW_NHI",
            "TW_PHONE_M",
            "TW_PHONE_L",
            "EMAIL",
            "CREDIT_CARD",
            "API_KEY",
        ]
        for pii_type in types:
            assert TOKEN_PATTERN.fullmatch(f"[{pii_type}_1]"), pii_type

    def test_不會誤抓一般的中括號(self):
        """程式碼裡的陣列語法不該被當成佔位符。"""
        for text in ("items[0]", "list[i]", "[TODO]", "[abc_1]", "[]"):
            assert not TOKEN_PATTERN.search(text), text
