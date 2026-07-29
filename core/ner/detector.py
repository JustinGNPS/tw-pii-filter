"""
Layer 2 語意層 NER 偵測器
負責人：D
依賴模型：gyr66/bert-base-chinese-finetuned-ner

TODO(D): docs/interface.md 尚未確認比對，請在合併前手動確認：
  1. 欄位命名是否為 start/end/type/text（也可能是 start_pos/end_pos/label/value 等）
  2. type 的枚舉值是否要對齊 A 定義的風險類別（例如 PERSON -> "PERSON" 還是 "NAME"）
  3. start/end 是否為字元索引（character offset）還是 token 索引
  4. 已詢問 A：detect_all() 是否已內建 conflict_resolver、與規則層的重疊如何處理、
     replacement 序號跳號的仲裁邏輯是否也套用到本層輸出（待回覆）
"""

from typing import List, Dict
from transformers import pipeline


class NERDetector:
    """
    語意層 NER 偵測器，將中文文字送入預訓練模型，
    輸出符合系統規格（暫定）的實體清單。
    """

    MODEL_NAME = "gyr66/bert-base-chinese-finetuned-ner"

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
                    "start": int,   # 實體起始字元位置
                    "end": int,     # 實體結束字元位置
                    "type": str,    # 實體類型，例如 PERSON / LOCATION / ORG
                    "text": str,    # 實體原文字串
                }
        """
        if not text:
            return []

        raw_results = self._pipeline(text)

        formatted: List[Dict] = []
        for ent in raw_results:
            # HuggingFace pipeline 在 aggregation_strategy="simple" 下
            # 通常回傳 entity_group / word / start / end / score
            formatted.append({
                "start": ent.get("start"),
                "end": ent.get("end"),
                "type": ent.get("entity_group"),
                "text": ent.get("word"),
                # TODO(D): 是否需要保留 score 給 Layer 2 的「組合風險評估」使用？
                # "score": ent.get("score"),
            })

        return formatted


if __name__ == "__main__":
    # 本地測試 - 僅使用完全虛構的假名與假地址，嚴禁真實個資
    detector = NERDetector()

    test_sentences = [
        "王小明昨天去台北市信義區的星巴克買了一杯咖啡。",
        "假設客戶陳大同的聯絡地址是虛構市測試路123號，電話為0900-000-000（此為測試假資料）。",
        "李小華與張小美一起在新竹科學園區的某公司上班。",
    ]

    for sentence in test_sentences:
        print(f"\n輸入句子：{sentence}")
        results = detector.detect(sentence)
        for r in results:
            print(f"  -> {r}")