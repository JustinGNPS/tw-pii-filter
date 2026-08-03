"""
整合測試：驗證 core.ner.detector.detect_ner() 的輸出能否直接餵給
core.rules.detect_all(text, extra_spans=...) 使用。

用假的 transformers 模組頂替 sys.modules，這樣不需要真的安裝 torch/transformers
或下載模型就能測試 —— 我們只關心「兩層的資料格式接不接得起來」，不需要真的跑 BERT。

驗證重點（對應 A 提出的三項）：
    1. detect_ner() 的輸出格式能直接當 extra_spans 餵給 detect_all()，不會缺欄位、
       不會因型別不合而出錯
    2. 兩層的 span 都在最終結果裡（規則層 + 語意層都有出現），而且互不重疊
       ——包含跨層重疊時 Layer 4 仲裁要生效，不能只在規則層內部仲裁
    3. replacement 序號連續（同一 type 底下從 1 開始不跳號）
"""

import sys
import types

import pytest

# 假 pipeline 的「劇本」：text -> 假的 NER 原始輸出（模擬 HuggingFace pipeline
# aggregation_strategy="simple" 的回傳格式）。用完整字串當 key，不同測試用不同
# 字串，彼此不會撞到，不需要每次清空重設。
_CANNED_NER_RESULTS: dict[str, list[dict]] = {}


def _fake_ner_call(text: str) -> list[dict]:
    """模擬 HuggingFace pipeline 的呼叫行為：吃文字、吐原始 NER 結果。"""
    return _CANNED_NER_RESULTS.get(text, [])


def _fake_pipeline(*args, **kwargs):
    """模擬 transformers.pipeline()：忽略所有參數（model/tokenizer/device...），
    直接回傳假的呼叫函式，不會真的去下載或載入任何模型。"""
    return _fake_ner_call


@pytest.fixture(scope="module")
def detect_ner_and_detect_all():
    """
    在假的 transformers 模組底下，匯入 detect_ner 與 detect_all。

    用 sys.modules 頂替，讓 detector.py 裡的 `from transformers import pipeline`
    拿到假的實作，不需要真的安裝 torch/transformers 或連網下載模型。
    """
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.pipeline = _fake_pipeline

    original_transformers = sys.modules.get("transformers")
    original_detector_module = sys.modules.get("core.ner.detector")

    sys.modules["transformers"] = fake_transformers
    sys.modules.pop("core.ner.detector", None)  # 強制重新 import，吃到假的 transformers

    from core.ner.detector import detect_ner
    from core.rules import detect_all

    yield detect_ner, detect_all

    # 還原，避免影響其他測試檔案（如果之後有測試需要真的 transformers）
    if original_transformers is not None:
        sys.modules["transformers"] = original_transformers
    else:
        sys.modules.pop("transformers", None)
    if original_detector_module is not None:
        sys.modules["core.ner.detector"] = original_detector_module
    else:
        sys.modules.pop("core.ner.detector", None)


def _assert_no_overlap(spans: list[dict]) -> None:
    """檢查任兩個 span 之間都不重疊（互不相交）。"""
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            overlap = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
            assert overlap == 0, f"發現重疊：{a} 與 {b}"


def _assert_replacement_sequential(spans: list[dict]) -> None:
    """檢查每個 type 底下的 replacement 序號從 1 開始連續遞增，不跳號。"""
    by_type: dict[str, list[dict]] = {}
    for s in spans:
        by_type.setdefault(s["type"], []).append(s)

    for pii_type, group in by_type.items():
        group_sorted = sorted(group, key=lambda s: s["start"])
        expected_numbers = list(range(1, len(group_sorted) + 1))
        actual_numbers = []
        for s in group_sorted:
            # replacement 格式 "[TYPE_N]"，取出 N
            suffix = s["replacement"].rstrip("]").rsplit("_", 1)[-1]
            actual_numbers.append(int(suffix))
        assert actual_numbers == expected_numbers, (
            f"{pii_type} 的 replacement 序號不連續：{actual_numbers}，"
            f"預期 {expected_numbers}"
        )


class TestDetectNerCompatibleWithDetectAll:
    """驗證點 1：detect_ner() 的輸出格式能直接餵給 detect_all(extra_spans=...)。"""

    def test_extra_spans_has_required_fields(self, detect_ner_and_detect_all):
        detect_ner, detect_all = detect_ner_and_detect_all

        text = "王小明的身分證字號是 A123456789，信箱是 test@example.com"
        _CANNED_NER_RESULTS[text] = [
            {"start": 0, "end": 3, "entity_group": "name", "score": 0.99},
        ]

        extra_spans = detect_ner(text)

        assert len(extra_spans) == 1
        span = extra_spans[0]
        for key in ("start", "end", "type", "text", "confidence", "source"):
            assert key in span, f"缺少欄位 {key}"
        assert span["type"] == "NAME"
        assert span["source"] == "model"
        assert span["text"] == "王小明"

        # 直接餵給 detect_all，不能因為缺欄位或型別不合而炸掉
        result = detect_all(text, extra_spans=extra_spans)
        assert "text" in result and "spans" in result


class TestBothLayersPresentInFinalResult:
    """驗證點 2：規則層與語意層的 span 都要出現在最終結果裡（互不重疊的情況下）。"""

    def test_rule_and_model_spans_both_survive(self, detect_ner_and_detect_all):
        detect_ner, detect_all = detect_ner_and_detect_all

        # 沿用 docs/interface.md 範例本身的文字，規則層應偵測到 TW_ID 與 EMAIL
        text = "王小明的身分證字號是 A123456789，信箱是 test@example.com"
        _CANNED_NER_RESULTS[text] = [
            {"start": 0, "end": 3, "entity_group": "name", "score": 0.99},
        ]

        extra_spans = detect_ner(text)
        result = detect_all(text, extra_spans=extra_spans)
        spans = result["spans"]

        sources = {s["source"] for s in spans}
        assert "rule" in sources, "規則層的 span 不見了"
        assert "model" in sources, "語意層的 span 不見了"

        types_found = {s["type"] for s in spans}
        assert "TW_ID" in types_found
        assert "EMAIL" in types_found
        assert "NAME" in types_found

        _assert_no_overlap(spans)
        _assert_replacement_sequential(spans)

        # text[start:end] 必須等於偵測到的原文，兩層都要符合
        for s in spans:
            assert text[s["start"]:s["end"]] == s["text"]


class TestOverlapArbitrationAcrossLayers:
    """
    驗證點 3：規則層與語意層的 span 重疊時，Layer 4 仲裁要跨層生效，
    不能只在規則層內部仲裁、放過語意層的重疊。
    """

    def test_overlapping_model_span_is_discarded(self, detect_ner_and_detect_all):
        detect_ner, detect_all = detect_ner_and_detect_all

        # "12345675" 是本專案測試慣用的合法統編（checksum 通過，第 7 碼為 7 的特例）
        text = "統一編號12345675由王小明登記。"

        _CANNED_NER_RESULTS[text] = [
            # 正常、不重疊的語意層偵測
            {"start": 13, "end": 16, "entity_group": "name", "score": 0.99},
            # 刻意跟規則層的 TW_TAX（start=4, end=12，長度 8）重疊，
            # 但範圍較小（長度 4），依 Layer 4 規則 1（範圍大者優先）應該被仲裁掉
            {"start": 4, "end": 8, "entity_group": "position", "score": 0.99},
        ]

        extra_spans = detect_ner(text)
        assert len(extra_spans) == 2  # detect_ner 本身不做仲裁，兩筆都要原樣回傳

        result = detect_all(text, extra_spans=extra_spans)
        spans = result["spans"]

        # 重疊、範圍較小的 POSITION 應該被仲裁掉，不該出現在最終結果
        assert not any(s["type"] == "POSITION" for s in spans), (
            "範圍較小、與規則層重疊的語意層 span 應該被 Layer 4 仲裁掉，但還在結果裡"
        )

        # TW_TAX（規則層）與 NAME（語意層）都應該保留
        types_found = {s["type"] for s in spans}
        assert "TW_TAX" in types_found
        assert "NAME" in types_found
        assert len(spans) == 2

        _assert_no_overlap(spans)
        _assert_replacement_sequential(spans)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))