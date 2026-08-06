"""
get_model_labels.py
======================
直接查詢 gyr66/bert-base-chinese-finetuned-ner 模型本身定義的完整標籤清單
（id2label），而不是靠測試句子「有沒有撞見過」去歸納。

回答 C 在 PR 裡問的問題：NAME/ADDRESS/POSITION/COMPANY 是完整清單嗎？
還有沒有其他型別？

用法（在 repo 根目錄執行）：
    python core/ner/get_model_labels.py
"""

from transformers import AutoConfig

MODEL_NAME = "gyr66/bert-base-chinese-finetuned-ner"


def main():
    config = AutoConfig.from_pretrained(MODEL_NAME)
    id2label = config.id2label

    print(f"模型：{MODEL_NAME}")
    print(f"訓練時定義的標籤總數：{len(id2label)}\n")

    # NER 模型的標籤通常是 BIO 格式（B-XXX / I-XXX / O），
    # 把 B-/I- 前綴去掉、取聯集，才是 aggregation_strategy="simple" 之後
    # entity_group 實際會出現的型別清單
    entity_types = set()
    for label in id2label.values():
        if label == "O":
            continue
        # 去掉 "B-" / "I-" 前綴
        entity_type = label.split("-", 1)[-1] if "-" in label else label
        entity_types.add(entity_type)

    print("原始標籤（BIO 格式）：")
    for idx in sorted(id2label.keys()):
        print(f"  {idx}: {id2label[idx]}")

    print(f"\n去掉 BIO 前綴後的實際型別清單（entity_group 會出現的值）：")
    for t in sorted(entity_types):
        print(f"  - {t}")

    print(f"\n共 {len(entity_types)} 種型別（不含 'O' 這個「非實體」標籤）")


if __name__ == "__main__":
    main()