"""對照表測試。

最重要的一條：**同一個真值永遠拿到同一個佔位符**。
違反這條會導致還原時把兩個人的個資對調 —— 比洩漏更嚴重。
"""

import pytest

from proxy import mapping
from proxy.mapping import (
    DEFAULT_IDLE_TIMEOUT,
    FALLBACK_TYPE,
    TOKEN_PATTERN,
    MappingTable,
    normalize_type,
)


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


class Test型別代碼正規化:
    """語意層送進來的是小寫的 name / address / position。

    若原樣拿去發號碼會產生 `[name_1]`，而 `TOKEN_PATTERN` 只認大寫 ——
    遮蔽成功、還原失效，佔位符會被寫進使用者的檔案。
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("name", "NAME"),  # D 的 NER 實際回傳值
            ("address", "ADDRESS"),
            ("position", "POSITION"),
            ("TW_ID", "TW_ID"),  # 已經合法的不該被動到
            ("tw_phone_m", "TW_PHONE_M"),
            ("credit card", "CREDIT_CARD"),  # 空白也當分隔
            ("ADDRESS2", "ADDRESS"),  # 數字會被清掉（佔位符無法反解）
            ("__weird__", "WEIRD"),  # 頭尾底線清掉，否則不符 TOKEN_PATTERN
            ("", FALLBACK_TYPE),  # 空字串有退路，不會產生 `[_1]`
            ("123", FALLBACK_TYPE),
            (None, FALLBACK_TYPE),
        ],
    )
    def test_正規化結果(self, raw, expected):
        assert normalize_type(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["name", "address", "position", "", "123", "怪 型別", None]
    )
    def test_正規化後一定是還原得回去的佔位符(self, raw):
        """真正的驗收條件：發出去的佔位符必須被 TOKEN_PATTERN 認得。"""
        table = MappingTable()

        token = table.token_for(raw, "王小明")

        assert TOKEN_PATTERN.fullmatch(token), f"{raw!r} 產生了還原不了的 {token}"
        assert table.value_for(token) == "王小明"

    def test_小寫型別遮蔽後還原得回原文(self):
        table = MappingTable()
        token = table.token_for("name", "王小明")

        text, restored, unknown = table.restore_text(f"客戶 {token} 來電")

        assert token == "[NAME_1]"
        assert text == "客戶 王小明 來電"
        assert (restored, unknown) == (1, 0)

    def test_大小寫混用不會撞號_把兩個人的個資對調(self):
        """最危險的一條。

        若 `name` 與 `NAME` 被當成兩個型別，兩邊都會從 1 號開始發，
        產生兩個 `[NAME_1]` 指向不同的人 —— 還原時個資會對調。
        """
        table = MappingTable()

        first = table.token_for("name", "王小明")
        second = table.token_for("NAME", "陳大同")

        assert first == "[NAME_1]"
        assert second == "[NAME_2]"
        assert table.value_for(first) == "王小明"
        assert table.value_for(second) == "陳大同"

    def test_正規化後同型別的同一真值仍是同一個佔位符(self):
        table = MappingTable()

        assert table.token_for("name", "王小明") == table.token_for("NAME", "王小明")
        assert len(table) == 1


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


class Test閒置逾時清除:
    """對照表是明文個資，不該無限期駐留 —— 見 docs/B_design.md 決定 11。

    只在 `token_for()` 檢查、`value_for()` 刻意不檢查，用 monkeypatch 控制
    `time.monotonic()` 讓測試不必真的等待。
    """

    def _fake_clock(self, monkeypatch, start: float = 0.0):
        """回傳一個可以手動撥動的假時鐘（list 包一個數字，方便在測試裡改）。"""
        clock = [start]
        monkeypatch.setattr(mapping.time, "monotonic", lambda: clock[0])
        return clock

    def test_閒置超過門檻時下一次呼叫前會清空(self, monkeypatch):
        clock = self._fake_clock(monkeypatch)
        table = MappingTable(idle_timeout=10.0)

        table.token_for("TW_ID", "A123456789")
        clock[0] = 5.0
        table.token_for("EMAIL", "a@example.com")
        assert len(table) == 2

        clock[0] = 20.0  # 距上次動作（5.0）已經 15 秒，超過門檻 10 秒
        token = table.token_for("TW_ID", "A123456789")

        assert token == "[TW_ID_1]"  # 重新配號（舊表已被清空，不是延續舊號碼）
        assert len(table) == 1  # EMAIL 那筆被清掉了，只剩剛剛新配的這筆

    def test_閒置未超過門檻不會清空(self, monkeypatch):
        clock = self._fake_clock(monkeypatch)
        table = MappingTable(idle_timeout=10.0)

        table.token_for("TW_ID", "A123456789")
        clock[0] = 9.0  # 未超過門檻
        table.token_for("EMAIL", "a@example.com")

        assert len(table) == 2  # 兩筆都還在

    def test_value_for_不會觸發閒置清空(self, monkeypatch):
        """還原路徑刻意不檢查逾時——避免長時間串流還原被自己觸發的清空打斷。"""
        clock = self._fake_clock(monkeypatch)
        table = MappingTable(idle_timeout=10.0)
        token = table.token_for("TW_ID", "A123456789")

        clock[0] = 999.0  # 遠遠超過門檻
        assert table.value_for(token) == "A123456789"  # 還查得到，沒被清空
        assert len(table) == 1

    def test_idle_timeout為None時永不自動清空(self, monkeypatch):
        clock = self._fake_clock(monkeypatch)
        table = MappingTable(idle_timeout=None)

        table.token_for("TW_ID", "A123456789")
        clock[0] = 10_000_000.0
        table.token_for("EMAIL", "a@example.com")

        assert len(table) == 2  # 沒被清空

    def test_預設逾時是_1800_秒(self, monkeypatch):
        clock = self._fake_clock(monkeypatch)
        assert DEFAULT_IDLE_TIMEOUT == 1800.0

        table = MappingTable()  # 不傳 idle_timeout，用預設值
        table.token_for("TW_ID", "A123456789")

        clock[0] = 1799.0  # 未超過
        table.token_for("EMAIL", "a@example.com")
        assert len(table) == 2

        clock[0] = 1799.0 + 1800.1  # 距上次動作（1799.0）超過 1800 秒
        table.token_for("TW_TAX", "12345675")
        assert len(table) == 1  # 前兩筆被清空了

    def test_手動_clear_也會重置閒置計時(self, monkeypatch):
        """clear() 之後閒置計時器要重新起算，不能讓下一次呼叫誤判成早就過期。"""
        clock = self._fake_clock(monkeypatch)
        table = MappingTable(idle_timeout=10.0)
        table.token_for("TW_ID", "A123456789")

        clock[0] = 5.0
        table.clear()

        clock[0] = 12.0  # 距 clear() 的時間（5.0）只過了 7 秒，未超過門檻
        table.token_for("EMAIL", "a@example.com")
        assert len(table) == 1  # 沒有被誤判成過期又清一次（只是本來就空的）
