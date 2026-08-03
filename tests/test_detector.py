"""測試 `proxy/detector.py` 接語意層（D 的 NER）的部分。

不在這裡真的載入 `core.ner.detector`（會拉 torch/transformers，很重、
CI 與組員機器不一定裝了）。`PII_ENABLE_NER` 關閉時的路徑本來就不會碰到
那個 import，直接測；啟用時的路徑改成 mock `detector._extra_spans` 本身，
只驗證「語意層回傳的東西真的會被送進 `detect_all(extra_spans=...)`」這件事。
"""

from proxy import config, detector
from proxy.cache import DetectionCache


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
