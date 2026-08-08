"""
eval_business_name_fp.py
==========================
用 business_name_samples.json（句子裡故意不含任何真實個資，只有店名/公司名）
跑 detect_ner()，統計模型把這些非個資的商業實體名稱誤判成 PII 的比率。

回答 C 在 PR 裡問的問題：address 把「星巴克」這種店名也標進去，誤報率多高？

用法（在 repo 根目錄執行）：
    python core/ner/eval_business_name_fp.py
"""

import json
from pathlib import Path

from detector import detect_ner


def main():
    data_path = Path("data/synthetic_pii/business_name_samples.json")
    if not data_path.exists():
        print(f"找不到 {data_path}，請先用 generate_business_name_samples.py 產生語料。")
        return

    data = json.loads(data_path.read_text(encoding="utf-8"))
    sentences = data["sentences"]

    total = len(sentences)
    tagged_as_company = 0    # 店名被正確辨識成 company（是否算 PII 屬政策問題，不是模型判斷錯）
    type_confused = 0        # 店名被標成 company 以外的型別（真正的型別搞混，例如當成 address）
    not_detected = 0         # 店名完全沒被偵測到
    other_noise = 0          # 句子裡其他地方（非店名）被誤標的筆數
    type_counter: dict[str, int] = {}

    print(f"共 {total} 句測試句（皆不含真實個資）\n")

    for s in sentences:
        text = s["text"]
        biz_start, biz_end = s["biz_start"], s["biz_end"]

        results = detect_ner(text)

        biz_hit_type = None  # 命中店名範圍的型別（若有多筆重疊，取第一筆）
        for r in results:
            type_counter[r["type"]] = type_counter.get(r["type"], 0) + 1
            overlap = max(0, min(r["end"], biz_end) - max(r["start"], biz_start))
            if overlap > 0:
                if biz_hit_type is None:
                    biz_hit_type = r["type"]
            else:
                other_noise += 1

        if biz_hit_type is None:
            not_detected += 1
            status = "OK 沒被標"
        elif biz_hit_type == "COMPANY":
            tagged_as_company += 1
            status = "✅ 正確標成 COMPANY（不一定算 PII，屬政策問題）"
        else:
            type_confused += 1
            status = f"❌ 型別搞混，標成 {biz_hit_type}"

        print(f"  [{status}] '{text}'  -> {results}")

    print(f"\n{'=' * 60}")
    print("統計結果")
    print("=" * 60)
    print(f"總句數：{total}")
    print(f"正確標成 company：{tagged_as_company} / {total} "
          f"（{tagged_as_company / total:.1%}，是否需要遮蔽是政策決定，不算模型判斷錯）")
    print(f"真正型別搞混（標成 company 以外的型別）：{type_confused} / {total} "
          f"（{type_confused / total:.1%}）")
    print(f"完全沒偵測到：{not_detected} / {total}")
    print(f"句子裡其他地方（非店名）被誤標的筆數：{other_noise}")
    print(f"依型別統計被觸發的次數：{type_counter}")


if __name__ == "__main__":
    main()