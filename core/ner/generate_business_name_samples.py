"""
generate_business_name_samples.py
====================================
產生一批「句子裡完全沒有真實個資、只有店名/公司行號」的測試句，
專門用來回答 C 的問題：address（或其他型別）誤把店名/公司名標成個資的
誤報率是多少。

因為句子裡刻意不放任何真人姓名/地址，只要模型偵測到任何 span，
一律視為誤報（False Positive）——這樣可以獨立算出「非個資的商業實體名稱」
被誤判成個資的比率，跟原本 synthetic_pii 語料（真的有姓名地址）的
precision/recall 分開看，兩者互補、不互相干擾。
"""

import json
from pathlib import Path

# 虛構公司/店名 + 幾個常見連鎖品牌（用來測試模型對「常見真實商業實體」
# 的誤報情形，只是拿名稱當測試輸入，不涉及任何創作內容重製）
BUSINESS_NAMES = [
    "星巴克", "全家便利商店", "7-ELEVEN", "麥當勞", "肯德基",
    "王品牛排", "鼎泰豐", "誠品書店", "家樂福", "大潤發",
    "虛構科技股份有限公司", "測試資訊有限公司", "假想生技公司",
    "示範顧問股份有限公司", "範例電子有限公司", "模擬金融控股公司",
    "練習物流有限公司", "樣本設計工作室", "假設餐飲集團", "測資雲端服務公司",
]

# 句型模板：故意不含任何姓名/地址，只放店名/公司名 + 中性內容
SENTENCE_TEMPLATES = [
    "今天中午去{biz}買了午餐。",
    "會議約在{biz}樓下的咖啡廳。",
    "上週去{biz}應徵工讀生。",
    "這份採購單的供應商是{biz}。",
    "他最近開始在{biz}上班。",
    "帳單上的商店名稱顯示{biz}。",
    "合約的甲方是{biz}。",
    "附近新開了一家{biz}。",
    "這次活動的贊助商包含{biz}。",
    "他推薦大家去{biz}吃晚餐。",
]


def generate_sentences() -> list[dict]:
    sentences = []
    for i, biz in enumerate(BUSINESS_NAMES):
        template = SENTENCE_TEMPLATES[i % len(SENTENCE_TEMPLATES)]
        text = template.format(biz=biz)
        start = text.index(biz)
        end = start + len(biz)
        sentences.append({
            "id": i + 1,
            "text": text,
            "business_name": biz,
            "biz_start": start,
            "biz_end": end,
        })
    return sentences


def main():
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)

    sentences = generate_sentences()
    out_path = out_dir / "business_name_samples.json"
    out_path.write_text(
        json.dumps(
            {
                "_notice": "測試用句子，故意不含任何真實個資，只用來測店名/公司名的誤報率。",
                "sentences": sentences,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已產生 {len(sentences)} 句店名/公司名測試句：{out_path}")


if __name__ == "__main__":
    main()