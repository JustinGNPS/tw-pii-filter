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
  - 已修正一個關鍵 bug（C 在 PR review 中發現）：type 欄位已統一轉大寫
    （name -> NAME、address -> ADDRESS、position -> POSITION、company -> COMPANY），
    避免 proxy/extension 的佔位符正則（只認大寫）無法還原小寫型別，造成靜默失敗。
    最終要不要沿用模型原生分類名稱、還是轉成 PERSON/LOCATION 這類命名，
    仍待 A 定案寫進 interface.md，但至少「大小寫」這件事已經解決。
  - 用店名/公司名合成語料測試後，發現模型還有第四種型別 company，且大部分
    （16/20）能正確辨識出「這是商業實體」而非誤標成 address；仍有 2/20 的情況
    把店名標成 address（真正的型別搞混，比例約 10%）。company 是否算目標 PII
    屬政策問題（類似 position，可考慮預設不遮蔽、讓使用者決定）
  - 已加上 ADDRESS 型別的合理性過濾（ADDRESS_INDICATOR_CHARS）：不含任何
    行政區劃/地址關鍵字（市/縣/區/路/巷/號等）的 ADDRESS 結果一律過濾，
    用來降低斷詞邊界錯誤產生的雜訊（例如「咖啡廳」被切成「啡廳」誤標成地址）。
    這是權宜措施，不是根治模型本身的判斷力問題，過濾條件仍可能誤殺極少數
    合法的短地址（例如只寫「內湖」沒有「區」），需持續觀察、視情況調整字元集
  - 已修正一個關鍵 bug（B 7/31 提出、實測確認）：模型 tokenizer.model_max_length
    = 512，超過此長度的文字後段會被無聲截斷、完全不會被掃到（實測 1501 字元的
    文字產生 788 token，明顯超標；把假姓名放在第 2000 字元後完全偵測不到）。
    已改為超過 CHUNK_CHAR_LIMIT（400 字元）自動切成有重疊的分段分別處理，
    座標位移換算回原文位置後合併、去重。已知限制：去重靠 (start,end,type)
    精確比對，極端情況下同一實體在不同分段被判斷出些微不同邊界時不會合併；
    分段處理也會讓長文字的推論時間變長（多次呼叫模型），需要重新量測延遲。
  - 已加上 _get_detector() 的 double-checked locking（C 在 review B 的 PR #11
    asyncio.to_thread 整合時發現）：避免併發請求下模型被重複載入
"""

from typing import List, Dict, Optional
import threading
from transformers import pipeline


class NERDetector:
    """
    語意層 NER 偵測器，將中文文字送入預訓練模型，
    輸出符合系統規格（暫定）的實體清單。
    """

    MODEL_NAME = "gyr66/bert-base-chinese-finetuned-ner"
    SOURCE = "model"  # 供 A 的 conflict_resolver 分辨偵測來源
    DEFAULT_MIN_CONFIDENCE = 0.5  # 低於此信心分數的偵測結果視為雜訊，直接過濾

    # ADDRESS 型別的合理性檢查：真正的地址幾乎都含有行政區劃/地址關鍵字。
    # 用來過濾中文斷詞邊界錯誤產生的雜訊，例如「咖啡廳」被切成「啡廳」誤標成地址
    # （模型本身的判斷力問題，無法從程式碼層面根治，這只是降低雜訊的權宜措施，
    # 不是完整解法——過濾條件仍可能誤殺極少數合法的短地址，需持續觀察調整）。
    ADDRESS_INDICATOR_CHARS = set("市縣區鄉鎮村里路街巷弄號樓段道")

    # 分段處理相關常數（回應 B 7/31 提出、實測確認存在的 512 token 截斷問題）：
    # 模型 tokenizer.model_max_length = 512，實測 1501 字元的文字會產生 788 個
    # token（換算約 1.9 字元/token），超過上限的部分會被模型忽略、完全不會報錯，
    # 是無聲漏測。CHUNK_CHAR_LIMIT 保守抓在遠低於安全比例換算值之下，留足夠餘裕
    # 因應英數字元（電話號碼、身分證字號等）token 密度較高的情況。
    # CHUNK_OVERLAP 讓相鄰兩段有重疊，避免實體剛好被切在段落交界處而漏抓。
    CHUNK_CHAR_LIMIT = 400
    CHUNK_OVERLAP = 50

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

    def _format_entities(
        self, raw_entities: List[Dict], text: str, min_confidence: float
    ) -> List[Dict]:
        """把 pipeline 的原始輸出轉成符合系統規格的格式（欄位轉換、過濾雜訊）。"""
        formatted: List[Dict] = []
        for ent in raw_entities:
            score = float(ent.get("score", 0.0))
            if score < min_confidence:
                continue  # 過濾低信心雜訊

            start = ent.get("start")
            end = ent.get("end")
            entity_text = text[start:end]
            entity_type = ent.get("entity_group", "").upper()

            # 地址合理性檢查：不含任何地址關鍵字的字串（例如斷詞邊界切出來的
            # 「啡廳」）視為雜訊，直接過濾，不送給下游
            if entity_type == "ADDRESS" and not any(
                c in self.ADDRESS_INDICATOR_CHARS for c in entity_text
            ):
                continue

            formatted.append({
                "start": start,
                "end": end,
                # 統一轉大寫：proxy（B）與 extension（C）的佔位符正則
                # `[A-Z][A-Z_]*` 只認大寫，若送出小寫 type（name/address/position/company），
                # 佔位符 [name_1] 會產生得出來，但還原時比對不到，導致靜默失敗
                # （不拋錯、只回報「還原 0 筆」），使用者檔案裡會留下一堆沒還原的佔位符。
                # C 在 PR review 中發現這個問題並建議在來源端轉大寫，而非放寬正則
                # （放寬正則會讓 [Name_1]/[NAME_1]/[name_1] 變成三種不同佔位符，更危險）。
                "type": entity_type,
                # 注意：不要用 ent.get("word")！中文 wordpiece 重組後的 word
                # 會夾帶多餘空格（例如 "王 小 明"），導致 text[start:end] != text。
                # 一律用 start/end 對原文切片，才能保證符合 interface.md 的字元索引要求。
                "text": entity_text,
                "confidence": score,
                "source": self.SOURCE,
            })

        return formatted

    @staticmethod
    def _dedupe(entities: List[Dict]) -> List[Dict]:
        """
        相鄰分段有重疊，重疊區域裡的實體可能被偵測兩次（兩段都掃到同一段文字）。
        用 (start, end, type) 當 key 去重——完全落在重疊區域內的實體，兩次偵測到
        的絕對座標理應相同，可以精準去重。

        已知限制：如果同一實體在兩個分段各自被判斷出些微不同的邊界（跨過重疊區邊緣
        的極端情況），這裡不會合併，可能留下一筆不完全重複的殘影。這是分段處理的
        已知取捨，不是完整解法，之後如有需要可以再加邊界合併邏輯。
        """
        seen = set()
        deduped = []
        for ent in entities:
            key = (ent["start"], ent["end"], ent["type"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ent)
        return sorted(deduped, key=lambda e: e["start"])

    def detect(self, text: str, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> List[Dict]:
        """
        對輸入文字執行 NER 偵測，回傳符合系統規格的實體清單。

        文字超過 CHUNK_CHAR_LIMIT 時會自動切成有重疊的多個分段分別處理，
        避免超出模型 512 token 上限而被無聲截斷（後段完全沒被掃到）。

        Args:
            text: 待偵測的原始文字
            min_confidence: 信心分數門檻，低於此值的結果會被過濾掉。
                實測發現電話號碼這類數字序列常被模型切碎、誤判成 name/address，
                且信心分數明顯偏低（例如 0.27），這類雜訊不該送進 A 的仲裁邏輯，
                過濾掉即可（電話號碼本來就該由 A 的規則層處理）。

        Returns:
            List[Dict]: 每個元素格式：
                {
                    "start": int,        # 實體起始字元位置
                    "end": int,          # 實體結束字元位置
                    "type": str,         # 實體類型，例如 NAME / ADDRESS
                    "text": str,         # 實體原文字串
                    "confidence": float, # 模型信心分數（0~1），供 A 的仲裁邏輯使用
                    "source": "model",   # 偵測來源，區分規則層 / 語意層
                }
        """
        if not text:
            return []

        if len(text) <= self.CHUNK_CHAR_LIMIT:
            raw_results = self._pipeline(text)
            return self._format_entities(raw_results, text, min_confidence)

        # 分段處理：切成有重疊的多段，各自跑 NER，座標位移換算回原文位置
        step = self.CHUNK_CHAR_LIMIT - self.CHUNK_OVERLAP
        all_raw_with_offset: List[Dict] = []

        chunk_start = 0
        while chunk_start < len(text):
            chunk_end = min(chunk_start + self.CHUNK_CHAR_LIMIT, len(text))
            chunk_text = text[chunk_start:chunk_end]

            for ent in self._pipeline(chunk_text):
                shifted = dict(ent)
                shifted["start"] = ent["start"] + chunk_start
                shifted["end"] = ent["end"] + chunk_start
                all_raw_with_offset.append(shifted)

            if chunk_end == len(text):
                break
            chunk_start += step

        formatted = self._format_entities(all_raw_with_offset, text, min_confidence)
        return self._dedupe(formatted)


# ---------------------------------------------------------------------------
# 模組層級介面：A 依 docs/interface.md 呼叫的是 detect_ner(text)，不是類別方法。
# 這裡用單例（lazy singleton）包住 NERDetector，避免每次呼叫都重新載入模型
# （模型載入本身有開銷，重複載入會讓延遲更難看）。
# ---------------------------------------------------------------------------

_detector_instance: Optional[NERDetector] = None
_detector_lock = threading.Lock()


def _get_detector() -> NERDetector:
    """
    Lazy singleton，附 double-checked locking。

    C 在 review B 的 asyncio.to_thread 整合時發現：原本沒加鎖，proxy 用
    asyncio.to_thread 讓多個請求真的併行跑進 thread pool 後，冷啟動時可能有
    兩個 thread 同時看到 _detector_instance is None，各自建一個 NERDetector()
    —— BERT 模型被載入兩次（各數百 MB，記憶體尖峰翻倍），其中一個 instance
    建完就被覆蓋丟棄，白花數百 ms 的載入時間，而且 HuggingFace pipeline()
    的建構本身是否 thread-safe 也沒保證。

    Double-checked locking：先在鎖外檢查一次（多數情況下 instance 已經存在，
    不用每次呼叫都搶鎖，維持效能），只有真的是 None 時才進鎖，進鎖後再檢查
    一次（可能在等鎖的期間，別的 thread 已經建好了），避免重複建立。
    """
    global _detector_instance
    if _detector_instance is None:  # 第一次檢查（鎖外，快速路徑）
        with _detector_lock:
            if _detector_instance is None:  # 拿到鎖後再檢查一次
                _detector_instance = NERDetector()
    return _detector_instance


def detect_ner(text: str, min_confidence: float = NERDetector.DEFAULT_MIN_CONFIDENCE) -> List[Dict]:
    """
    供 A 的 detect_all(text, extra_spans=detect_ner(text)) 呼叫的對外介面。

    Args:
        text: 待偵測的原始文字
        min_confidence: 信心分數門檻，低於此值的結果會被過濾（預設 0.5）

    Returns:
        List[Dict]: 原始偵測結果，每筆含 start/end/type/text/confidence/source。
        不處理重疊、不產生 replacement 編號 —— 這些交給 A 的 detect_all() 統一仲裁。
    """
    return _get_detector().detect(text, min_confidence=min_confidence)


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