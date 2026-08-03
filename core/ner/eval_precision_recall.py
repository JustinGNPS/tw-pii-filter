"""
eval_precision_recall.py
==========================
拿 data/synthetic_pii/ 產生語料時的「正確答案」（哪些記錄的 name / address
被塞進哪個假檔案），比對 detect_ner() 實際偵測到的結果，算出 precision / recall。

正確答案的建構方式：
    generate_fake_code_samples.py 產生假檔案時，用的是固定的 record 切片
    （customer_export.py 用 records[:8]、chat_log.txt 用 records[8:13]...），
    所以我們知道每個檔案裡「應該」出現哪些 name / address，用字串搜尋找出它們
    在檔案文字中的所有出現位置，當作 ground truth spans。

只評估 name / address 兩種類型（NER 該抓的東西）；phone / taiwan_id 本來就該由
A 的規則層處理，不計入這份評估；position（職稱詞）也不計入，因為 B 已建議
預設不遮蔽，不算目標 PII。

用法（在 repo 根目錄執行）：
    python core/ner/eval_precision_recall.py
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from detector import detect_ner

# 每個假檔案對應到 synthetic_pii.json records 的切片區間（需與
# generate_fake_code_samples.py 的切片保持一致，若之後語料重新產生要同步更新）
FILE_RECORD_SLICES = {
    "customer_export.py": (0, 8),
    "chat_log.txt": (8, 13),
    "api_request_log.json": (13, 18),
    "crm_notes.md": (18, 23),
}


@dataclass
class Span:
    start: int
    end: int
    type: str
    text: str


def find_all_occurrences(haystack: str, needle: str, entity_type: str) -> List[Span]:
    """找出 needle 在 haystack 中所有出現位置，組成 ground truth spans。"""
    spans = []
    for m in re.finditer(re.escape(needle), haystack):
        spans.append(Span(start=m.start(), end=m.end(), type=entity_type, text=needle))
    return spans


def build_ground_truth(text: str, records: list) -> List[Span]:
    gt: List[Span] = []
    for r in records:
        gt.extend(find_all_occurrences(text, r["name"], "NAME"))
        gt.extend(find_all_occurrences(text, r["address"], "ADDRESS"))
    return gt


def overlap_ratio(a: Span, b: Span) -> float:
    """計算兩個 span 的重疊比例（IoU：交集長度 / 聯集長度）。"""
    inter_start = max(a.start, b.start)
    inter_end = min(a.end, b.end)
    inter = max(0, inter_end - inter_start)
    union = max(a.end, b.end) - min(a.start, b.start)
    return inter / union if union > 0 else 0.0


def match_spans(predicted: List[Span], ground_truth: List[Span], iou_threshold: float = 0.5):
    """
    貪婪比對 predicted 與 ground_truth：同類型、IoU 超過門檻視為配對成功。
    回傳 (TP 配對數, FP 清單, FN 清單)。
    """
    matched_gt = set()
    matched_pred = set()

    for i, p in enumerate(predicted):
        best_j, best_iou = None, 0.0
        for j, g in enumerate(ground_truth):
            if j in matched_gt or g.type != p.type:
                continue
            iou = overlap_ratio(p, g)
            if iou > best_iou:
                best_iou, best_j = iou, j
        if best_j is not None and best_iou >= iou_threshold:
            matched_gt.add(best_j)
            matched_pred.add(i)

    tp = len(matched_pred)
    fp = [p for i, p in enumerate(predicted) if i not in matched_pred]
    fn = [g for j, g in enumerate(ground_truth) if j not in matched_gt]
    return tp, fp, fn


def evaluate_file(path: Path, records: list):
    text = path.read_text(encoding="utf-8")
    ground_truth = build_ground_truth(text, records)

    raw_predicted = detect_ner(text)
    # 只評估 name / address，position 不算目標 PII（B 建議預設不遮蔽）
    # 注意：detect_ner() 已將 type 統一轉大寫（對齊 interface.md 慣例），
    # 這裡的比對條件要跟著用大寫
    predicted = [
        Span(start=r["start"], end=r["end"], type=r["type"], text=r["text"])
        for r in raw_predicted
        if r["type"] in ("NAME", "ADDRESS")
    ]

    tp, fp, fn = match_spans(predicted, ground_truth)
    precision = tp / (tp + len(fp)) if (tp + len(fp)) > 0 else float("nan")
    recall = tp / (tp + len(fn)) if (tp + len(fn)) > 0 else float("nan")
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0 and not (precision != precision)
        else float("nan")
    )

    print(f"\n{'=' * 60}")
    print(f"檔案：{path.name}")
    print(f"Ground truth 筆數：{len(ground_truth)}　預測筆數（name/address）：{len(predicted)}")
    print(f"TP={tp}  FP={len(fp)}  FN={len(fn)}")
    print(f"Precision={precision:.3f}  Recall={recall:.3f}  F1={f1:.3f}")

    if fp:
        print("  漏抓/誤抓 - False Positive（預測有、答案沒有）：")
        for p in fp:
            print(f"    - [{p.type}] '{p.text}' ({p.start}:{p.end})")
    if fn:
        print("  漏抓 - False Negative（答案有、沒預測到）：")
        for g in fn:
            print(f"    - [{g.type}] '{g.text}' ({g.start}:{g.end})")

    return tp, len(fp), len(fn)


def main():
    base = Path("data/synthetic_pii")
    records = json.loads((base / "synthetic_pii.json").read_text(encoding="utf-8"))["records"]

    total_tp = total_fp = total_fn = 0
    for filename, (lo, hi) in FILE_RECORD_SLICES.items():
        path = base / "fake_code_samples" / filename
        if not path.exists():
            print(f"找不到 {path}，略過")
            continue
        tp, fp, fn = evaluate_file(path, records[lo:hi])
        total_tp += tp
        total_fp += fp
        total_fn += fn

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else float("nan")
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else float("nan")

    print(f"\n{'=' * 60}")
    print("整體（全部 4 個檔案合計，僅 name/address）")
    print("=" * 60)
    print(f"TP={total_tp}  FP={total_fp}  FN={total_fn}")
    print(f"Precision={precision:.3f}  Recall={recall:.3f}  F1={f1:.3f}")


if __name__ == "__main__":
    main()