"""core.ner.detector（語意層，D）與 core.rules.detect_all（規則層，A）的整合測試。

驗證的是「整合鏈通不通」：D 的 detect_ner(text) 產生的 spans，格式要能直接
當作 core.rules.detect_all(text, extra_spans=...) 的輸入，經 Layer 4 仲裁後
兩層的偵測結果都要在最終輸出裡、彼此不重疊、replacement 序號連續。

刻意不載入真正的 BERT 模型：core/ner/requirements.txt 的 torch/transformers
屬於重量級、且需要從網路下載模型權重的相依套件，不適合放進每次執行的 pytest
套件——這正是本專案把模型準確度評估另外獨立成 core/ner/eval_precision_recall.py
（手動執行的腳本）、而不是寫成 pytest 測試的原因。

做法：在 import core.ner.detector 之前，用假的 transformers 模組頂替
sys.modules["transformers"]，讓 NERDetector.__init__() 呼叫到的 pipeline(...)
回傳一個假的 callable（呼叫它就回傳我們準備好的假 NER 原始輸出），不需要真的
安裝 torch/transformers，也不會有任何網路存取。detect_ner() 本身的邏輯
（信心分數過濾、type 轉大寫、用 start/end 切原文而非 word 欄位）仍是真正在
執行的程式碼——這才是本測試真正要驗證的整合契約，不是模型準不準。
"""

import sys
import unittest
from unittest.mock import MagicMock, patch


def _import_ner_detector_with_fake_transformers(fake_pipeline_callable):
    """在假的 transformers 模組底下 import core.ner.detector，避免需要真的安裝
    torch/transformers。回傳 import 進來的 module。
    """
    fake_transformers = MagicMock()
    fake_transformers.pipeline = MagicMock(return_value=fake_pipeline_callable)

    # 若先前已經被 import 過（例如同一個 process 裡跑過別的測試），先清掉，
    # 確保這次一定是用我們準備的假 pipeline 重新初始化 singleton。
    sys.modules.pop("core.ner.detector", None)

    with patch.dict(sys.modules, {"transformers": fake_transformers}):
        import core.ner.detector as ner_detector  # noqa: PLC0415

    return ner_detector


class NerRulesIntegrationTests(unittest.TestCase):
    """規則層 + 語意層整合鏈：detect_ner() 的輸出能否被 detect_all(extra_spans=...) 正確吸收。"""

    def test_person_name_from_ner_and_tw_id_from_rules_both_survive_layer4(self):
        text = "王小明的身分證字號是 A123456789，請妥善保管。"

        # 比照 HuggingFace ner pipeline（aggregation_strategy="simple"）的原始輸出格式。
        # word 欄位刻意帶入 wordpiece 合併常見的多餘空格，驗證 detect_ner 真的是用
        # start/end 切原文，而不是直接沿用 word（detector.py 內有特別註明這個陷阱）。
        fake_raw_ner_output = [
            {
                "entity_group": "name",
                "score": 0.97,
                "word": "王 小明",
                "start": 0,
                "end": 3,
            }
        ]
        fake_pipeline_callable = MagicMock(return_value=fake_raw_ner_output)
        ner_detector = _import_ner_detector_with_fake_transformers(fake_pipeline_callable)
        self.addCleanup(lambda: setattr(ner_detector, "_detector_instance", None))

        ner_spans = ner_detector.detect_ner(text)

        # --- detect_ner() 自己的輸出要先符合 docs/interface.md 格式 ---
        self.assertEqual(len(ner_spans), 1)
        name_span = ner_spans[0]
        self.assertEqual(name_span["type"], "NAME")  # 原生 "name" 已轉大寫
        self.assertEqual(name_span["text"], "王小明")  # 用 start/end 切原文，不是帶空格的 word
        self.assertEqual(text[name_span["start"]:name_span["end"]], "王小明")
        self.assertEqual(name_span["source"], "model")

        # --- 整合鏈的關鍵一步：NER 結果當 extra_spans 餵給規則層的 detect_all ---
        from core.rules import detect_all  # noqa: PLC0415  (延後 import，避免與假 transformers 無關)

        result = detect_all(text, extra_spans=ner_spans)

        # 兩邊的偵測結果都要在最終輸出裡
        types_found = {span["type"] for span in result["spans"]}
        self.assertEqual(types_found, {"NAME", "TW_ID"})

        name_result = next(s for s in result["spans"] if s["type"] == "NAME")
        tw_id_result = next(s for s in result["spans"] if s["type"] == "TW_ID")
        self.assertEqual(text[name_result["start"]:name_result["end"]], "王小明")
        self.assertEqual(text[tw_id_result["start"]:tw_id_result["end"]], "A123456789")
        self.assertEqual(name_result["source"], "model")
        self.assertEqual(tw_id_result["source"], "rule")

        # 沒有重疊（依 start 排序後，前一筆的 end 不能超過下一筆的 start）
        spans_sorted = sorted(result["spans"], key=lambda s: s["start"])
        for i in range(len(spans_sorted) - 1):
            self.assertLessEqual(spans_sorted[i]["end"], spans_sorted[i + 1]["start"])

        # replacement 編號連續（各自 type 從 1 開始）
        self.assertEqual(name_result["replacement"], "[NAME_1]")
        self.assertEqual(tw_id_result["replacement"], "[TW_ID_1]")

        # detect_all 回傳格式本身也要符合 interface.md
        self.assertEqual(set(result.keys()), {"text", "spans"})
        for span in result["spans"]:
            self.assertEqual(
                set(span.keys()),
                {"start", "end", "type", "text", "confidence", "source", "replacement"},
            )

    def test_low_confidence_ner_result_is_filtered_before_reaching_detect_all(self):
        # detect_ner 預設 min_confidence=0.5，低於門檻的雜訊在進 detect_all 之前
        # 就該被濾掉，不該污染規則層的仲裁結果。
        text = "王小明的身分證字號是 A123456789。"
        fake_raw_ner_output = [
            {"entity_group": "name", "score": 0.2, "word": "王 小明", "start": 0, "end": 3},
        ]
        fake_pipeline_callable = MagicMock(return_value=fake_raw_ner_output)
        ner_detector = _import_ner_detector_with_fake_transformers(fake_pipeline_callable)
        self.addCleanup(lambda: setattr(ner_detector, "_detector_instance", None))

        ner_spans = ner_detector.detect_ner(text)
        self.assertEqual(ner_spans, [])

        from core.rules import detect_all  # noqa: PLC0415

        result = detect_all(text, extra_spans=ner_spans)
        types_found = {span["type"] for span in result["spans"]}
        self.assertEqual(types_found, {"TW_ID"})


if __name__ == "__main__":
    unittest.main()
