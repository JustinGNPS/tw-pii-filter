"""測試 `proxy/detector.py` 接語意層（D 的 NER）的部分。

不在這裡真的載入 `core.ner.detector`（會拉 torch/transformers，很重、
CI 與組員機器不一定裝了）。`PII_ENABLE_NER` 關閉時的路徑本來就不會碰到
那個 import，直接測；啟用時的路徑改成 mock `detector._extra_spans` 本身，
只驗證「語意層回傳的東西真的會被送進 `detect_all(extra_spans=...)`」這件事。
"""

import sys
import types

from proxy import config, detector
from proxy.cache import DetectionCache


def _span(type_: str, text: str = "xx", start: int = 0, end: int = 2) -> dict:
    """組一個語意層形狀的 span，只有 type 需要在各測試間變化。"""
    return {
        "start": start,
        "end": end,
        "type": type_,
        "text": text,
        "confidence": 0.9,
        "source": "model",
    }


class Test語意層開關:
    def test_預設關閉時_extra_spans回傳None(self, monkeypatch):
        """關閉時不該進到會 import core.ner.detector 的那一行。"""
        monkeypatch.setattr(config, "ENABLE_NER", False)
        assert detector._extra_spans("客服 王小明的信箱是 test@example.com") is None

    def test_關閉時detect只回傳規則層結果(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_NER", False)
        cache = DetectionCache()
        result = detector.detect("王小明的信箱是 test@example.com", cache=cache)
        types = {span["type"] for span in result["spans"]}
        assert types == {"EMAIL"}  # 姓名不是規則層的型別，語意層又關著

    def test_啟用時語意層結果會併入偵測結果(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_NER", True)
        fake_ner_spans = [
            {
                "start": 0,
                "end": 3,
                "type": "NAME",
                "text": "王小明",
                "confidence": 0.9,
                "source": "model",
            }
        ]
        monkeypatch.setattr(detector, "_extra_spans", lambda text: fake_ner_spans)

        cache = DetectionCache()
        result = detector.detect("王小明的信箱是 test@example.com", cache=cache)
        types = {span["type"] for span in result["spans"]}

        assert "NAME" in types  # 語意層的結果有進來
        assert "EMAIL" in types  # 規則層照常運作，兩者不互相取代


class Test語意層型別白名單:
    """語意層是通用領域模型（14 種標籤），只採信白名單裡的型別。

    背景與實測數據見 `proxy/config.py` 的 `NER_ALLOW_TYPES` 一節：
    Claude Code 的 system prompt 會被判出 `GAME`（連一個反引號都算）與
    `ORGANIZATION`，若不過濾就會被遮成佔位符送給上游。
    """

    # 模型 config 的 id2label 實際會出現、但與個人身分無關的型別
    # （`python core/ner/get_model_labels.py` 查得，共 14 種標籤）
    雜訊型別 = ["GAME", "BOOK", "MOVIE", "SCENE", "ORGANIZATION",
                "GOVERNMENT", "QQ", "VX", "EMAIL", "MOBILE"]

    def test_雜訊型別一律丟棄(self):
        spans = [_span(t) for t in self.雜訊型別]
        assert detector._keep_allowed_types(spans) == []

    def test_白名單型別全部保留(self):
        spans = [_span(t) for t in ("NAME", "ADDRESS", "POSITION", "COMPANY")]
        kept = detector._keep_allowed_types(spans)
        assert [s["type"] for s in kept] == ["NAME", "ADDRESS", "POSITION", "COMPANY"]

    def test_混雜時只留白名單那些(self):
        spans = [_span("NAME"), _span("GAME"), _span("ADDRESS"), _span("VX")]
        kept = detector._keep_allowed_types(spans)
        assert [s["type"] for s in kept] == ["NAME", "ADDRESS"]

    def test_小寫型別代碼也能正確比對(self):
        """語意層模型原生輸出過小寫代碼；白名單比對前要正規化，
        否則 `name` 會被誤判成不在白名單而整個被丟掉（靜默失效）。"""
        kept = detector._keep_allowed_types([_span("name"), _span("game")])
        assert [s["type"] for s in kept] == ["name"]

    def test_空白名單代表全部採信(self, monkeypatch):
        """退路：`PII_NER_ALLOW_TYPES=` 設成空字串時回到改動前的行為，
        供比對與除錯用。"""
        monkeypatch.setattr(config, "NER_ALLOW_TYPES", frozenset())
        spans = [_span("GAME"), _span("NAME")]
        assert detector._keep_allowed_types(spans) == spans

    def test_語意層關閉時的None原樣傳遞(self):
        assert detector._keep_allowed_types(None) is None

    def test__extra_spans真的有套用白名單(self, monkeypatch):
        """接線測試：確認過濾是掛在 `_extra_spans()` 上（也就是送進
        `detect_all(extra_spans=...)` 之前），不是只有函式本身寫對。

        用假的 `core.ner.detector` 模組頂替，不需要 torch/transformers。
        """
        monkeypatch.setattr(config, "ENABLE_NER", True)
        fake_module = types.ModuleType("core.ner.detector")
        fake_module.detect_ner = lambda text: [_span("NAME"), _span("GAME")]
        monkeypatch.setitem(sys.modules, "core.ner.detector", fake_module)

        result = detector._extra_spans("隨便一段文字")
        assert [s["type"] for s in result] == ["NAME"]

    def test_規則層型別不受白名單影響(self, monkeypatch):
        """白名單只作用於語意層。規則層的 EMAIL 有正則驗證，即使 `EMAIL`
        不在白名單裡（它確實不在 —— 語意層抓到的是 '@' 這種破碎片段），
        規則層抓到的那筆也必須照常留下並遮蔽。
        """
        monkeypatch.setattr(config, "ENABLE_NER", True)
        fake_module = types.ModuleType("core.ner.detector")
        # 語意層對同一段文字吐出破碎的 EMAIL 片段 + 一個雜訊型別
        fake_module.detect_ner = lambda text: [
            _span("EMAIL", text="@", start=8, end=9),
            _span("GAME", text="test", start=4, end=8),
        ]
        monkeypatch.setitem(sys.modules, "core.ner.detector", fake_module)

        result = detector.detect("信箱是 test@example.com", cache=DetectionCache())
        emails = [s for s in result["spans"] if s["type"] == "EMAIL"]

        assert len(emails) == 1
        assert emails[0]["text"] == "test@example.com"  # 規則層抓到的完整信箱
        assert emails[0]["source"] == "rule"
        assert "GAME" not in {s["type"] for s in result["spans"]}


class Test型別策略的預設值:
    """把決定 13 的預設值釘住 —— 這兩個設定的差別很容易被誤解，
    改動時應該是有意識的決定，不是不小心。"""

    def test_語意層白名單預設四種(self):
        assert config.NER_ALLOW_TYPES == frozenset(
            {"NAME", "ADDRESS", "POSITION", "COMPANY"}
        )

    def test_POSITION與COMPANY偵測得到但不遮蔽(self):
        """決定 13：`COMPANY` 與 `POSITION` 一樣，偵測是對的、政策上不遮，
        但**仍在白名單內** —— 因為 AI 真的看得到它們，必須計入 Layer 3
        組合風險分數。若改成排除在白名單外，風險分數會漏報。
        """
        assert {"POSITION", "COMPANY"} <= config.NER_ALLOW_TYPES
        assert {"POSITION", "COMPANY"} <= config.SKIP_TYPES


class Test快取與語意層開關的交互:
    def test_快取以文字為key_不受ENABLE_NER執行期切換影響單次呼叫(self, monkeypatch):
        """同一個 DetectionCache 內，只要沒被清空，第二次呼叫應該直接命中，
        不會重新呼叫 _extra_spans（驗證快取真的省下了語意層那筆重複成本）。
        """
        monkeypatch.setattr(config, "ENABLE_NER", True)
        calls = []

        def fake_extra_spans(text: str) -> list[dict]:
            calls.append(text)
            return []

        monkeypatch.setattr(detector, "_extra_spans", fake_extra_spans)

        cache = DetectionCache()
        text = "客服 王小明的信箱是 test@example.com"
        detector.detect(text, cache=cache)
        detector.detect(text, cache=cache)

        assert len(calls) == 1  # 第二次是快取命中，沒有再跑一次語意層

    def test_同一張快取切換ENABLE_NER不會拿到切換前的舊結果(self, monkeypatch):
        """C 在 PR #11 review 提過的風險：快取 key 若只看文字，`PII_ENABLE_NER`
        執行期切換後、沒換一張新快取的話，可能誤用切換前算出的結果。
        `detector.detect()` 已把 `config.ENABLE_NER` 併入 key_extra，這裡驗證
        切換後兩次呼叫互不干擾。
        """
        cache = DetectionCache()
        text = "王小明的信箱是 test@example.com"

        monkeypatch.setattr(config, "ENABLE_NER", False)
        off_result = detector.detect(text, cache=cache)
        assert {span["type"] for span in off_result["spans"]} == {"EMAIL"}

        monkeypatch.setattr(config, "ENABLE_NER", True)
        monkeypatch.setattr(
            detector,
            "_extra_spans",
            lambda t: [
                {
                    "start": 0,
                    "end": 3,
                    "type": "NAME",
                    "text": "王小明",
                    "confidence": 0.9,
                    "source": "model",
                }
            ],
        )
        on_result = detector.detect(text, cache=cache)
        types = {span["type"] for span in on_result["spans"]}
        assert types == {"EMAIL", "NAME"}  # 不是關閉時算出的舊快取結果
