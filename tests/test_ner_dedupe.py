"""NER 分段結果去重的回歸測試。

這些測試只呼叫純函式式的去重邏輯，不載入真正的 Hugging Face 模型。
"""

import sys
import types


# 匯入 detector.py 時避免測試環境為了這組純邏輯測試載入 torch/transformers。
if "transformers" not in sys.modules:
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.pipeline = lambda *args, **kwargs: None
    sys.modules["transformers"] = fake_transformers

from core.ner.detector import NERDetector


def span(start: int, end: int, entity_type: str = "NAME") -> dict:
    return {
        "start": start,
        "end": end,
        "type": entity_type,
        "text": "x" * (end - start),
        "confidence": 0.9,
        "source": "model",
    }


def test_partial_overlap_keeps_both_entities():
    entities = [span(0, 3), span(2, 6)]

    result = NERDetector._dedupe(entities)

    assert [(item["start"], item["end"]) for item in result] == [(0, 3), (2, 6)]


def test_fully_contained_same_type_is_removed():
    entities = [span(0, 3), span(0, 2)]

    result = NERDetector._dedupe(entities)

    assert [(item["start"], item["end"]) for item in result] == [(0, 3)]


def test_partial_overlap_with_different_types_keeps_both_entities():
    entities = [span(0, 3, "NAME"), span(2, 6, "ADDRESS")]

    result = NERDetector._dedupe(entities)

    assert [(item["start"], item["end"], item["type"]) for item in result] == [
        (0, 3, "NAME"),
        (2, 6, "ADDRESS"),
    ]
