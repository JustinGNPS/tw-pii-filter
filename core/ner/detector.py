"""
Layer 2 語意層 NER 偵測器
負責人：D
依賴模型：gyr66/bert-base-chinese-finetuned-ner

已對齊 docs/interface.md（A 更新後版本）：
  1. 欄位命名確認為 start/end/type/text/confidence/source（已符合）
  2. 索引為字元索引，text[start:end] 需等於偵測到的原文 —— HuggingFace pipeline
     本身即回傳字元 offset，理論上已符合，但務必在本機用中英文混排的句子
     實測驗證一次（wordpiece 合併中文字詞時偶有 edge case）
  3. 重疊仲裁與 replacement 編號一律交給 A 的 detect_all() 處理，本層只需回傳
     原始偵測結果，不用自己處理重疊或編號
  4. A 要求對外函式命名為 detect_ner(text) -> List[Dict]（模組層級函式，非類別方法），
     已在下方補上，內部仍沿用 NERDetector 類別實作

TODO(D): 仍待確認：
  - 實測發現模型的 entity_group 回傳的是 name、address 這類代碼（不是先前假設的
    PERSON/LOCATION/ORG），一樣不在 interface.md 的類別代碼清單裡，需請 A 確認
    是否要新增這些代碼、或需要做類別名稱轉換對照表
  - 已修正一個重大 bug：不可用模型 word 欄位當作 text，因中文 wordpiece 重組後
    word 會夾帶多餘空格（例如 "王 小 明"），導致 text[start:end] != text，
    違反 interface.md 對字元索引的要求。已改為一律用 text[start:end] 切片。
"""

from typing import List, Dict, Optional
from transformers import pipeline


class NERDetector:
    """
    語意層 NER 偵測器，將中文文字送入預訓練模型，
    輸出符合系統規格（暫定）的實體清單。
    """

    MODEL_NAME = "gyr66/bert-base-chinese-finetuned-ner"
    SOURCE = "model"  # 供 A 的 conflict_resolver 分辨偵測來源

    def __init__(self, device: int = -1):
        """
        Args:
            device: -1 表示使用 CPU，若要用 GPU 請傳入對應的 device index（例如 0）
        """
        # aggregation_strategy="simple" 會把同一實體的子詞（wordpiece）合併回完整詞
        # 中文情境下通常搭配 "simple" 或 "max" 效果較穩定，可依實測調整
        self._pipeline = pipeline(
            task="ner",
            model=self.MODEL_NAME,
            tokenizer=self.MODEL_NAME,
            aggregation_strategy="simple",
            device=device,
        )

    def detect(self, text: str) -> List[Dict]:
        """
        對輸入文字執行 NER 偵測，回傳符合系統規格的實體清單。

        Args:
            text: 待偵測的原始文字

        Returns:
            List[Dict]: 每個元素格式（暫定，待對齊 docs/interface.md）：
                {
                    "start": int,        # 實體起始字元位置
                    "end": int,          # 實體結束字元位置
                    "type": str,         # 實體類型，例如 name / address
                    "text": str,         # 實體原文字串
                    "confidence": float, # 模型信心分數（0~1），供 A 的仲裁邏輯使用
                    "source": "model",   # 偵測來源，區分規則層 / 語意層
                }
        """
        if not text:
            return []

        raw_results = self._pipeline(text)

        formatted: List[Dict] = []
        for ent in raw_results:
            start = ent.get("start")
            end = ent.get("end")
            formatted.append({
                "start": start,
                "end": end,
                "type": ent.get("entity_group"),
                # 注意：不要用 ent.get("word")！中文 wordpiece 重組後的 word
                # 會夾帶多餘空格（例如 "王 小 明"），導致 text[start:end] != text。
                # 一律用 start/end 對原文切片，才能保證符合 interface.md 的字元索引要求。
                "text": text[start:end],
                "confidence": float(ent.get("score", 0.0)),
                "source": self.SOURCE,
            })

        return formatted


# ---------------------------------------------------------------------------
# 模組層級介面：A 依 docs/interface.md 呼叫的是 detect_ner(text)，不是類別方法。
# 這裡用單例（lazy singleton）包住 NERDetector，避免每次呼叫都重新載入模型
# （模型載入本身有開銷，重複載入會讓延遲更難看）。
# ---------------------------------------------------------------------------

_detector_instance: Optional[NERDetector] = None


def _get_detector() -> NERDetector:
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = NERDetector()
    return _detector_instance


def detect_ner(text: str) -> List[Dict]:
    """
    供 A 的 detect_all(text, extra_spans=detect_ner(text)) 呼叫的對外介面。

    Args:
        text: 待偵測的原始文字

    Returns:
        List[Dict]: 原始偵測結果，每筆含 start/end/type/text/confidence/source。
        不處理重疊、不產生 replacement 編號 —— 這些交給 A 的 detect_all() 統一仲裁。
    """
    return _get_detector().detect(text)


if __name__ == "__main__":
    # 本地測試 - 僅使用完全虛構的假名與假地址，嚴禁真實個資
    test_sentences = [
        "王小明昨天去台北市信義區的星巴克買了一杯咖啡。",
        "假設客戶陳大同的聯絡地址是虛構市測試路123號，電話為0900-000-000（此為測試假資料）。",
        "李小華與張小美一起在新竹科學園區的某公司上班。",
    ]

    for sentence in test_sentences:
        print(f"\n輸入句子：{sentence}")
        results = detect_ner(sentence)
        for r in results:
            # 驗證 A 要求的字元索引特性：text[start:end] 必須等於偵測到的原文
            sliced = sentence[r["start"]:r["end"]]
            check = "OK" if sliced == r["text"] else f"❌ 對不上！slice='{sliced}' vs text='{r['text']}'"
            print(f"  -> {r}  [索引檢查: {check}]")