"""
benchmark_ner_latency.py
=========================
測量 NERDetector.detect() 的單次推論延遲，供 B 評估 proxy 是否要加快取 /
讓 Layer 2 有條件觸發。

用法：
    python benchmark_ner_latency.py

輸出：CPU（若有 GPU 也會自動測一次）在約 2500~3000 字元文字上，
     detect() 執行 N 次的 min / mean / median / max（毫秒）。

測試文字使用 data/synthetic_pii/ 底下的合成假資料組成，非真實個資。
"""

import statistics
import time
from pathlib import Path

# 依實際專案路徑調整這行 import
from detector import NERDetector


def load_benchmark_text(target_chars: int = 2800) -> str:
    """
    組出一段接近 agent 實際送出量（約 2500~3000 字元）的測試文字。
    優先讀取 data/synthetic_pii/fake_code_samples/ 下的假檔案重複拼接，
    若找不到就用內建的假文字重複拼接補足長度。
    """
    candidates = [
        Path("data/synthetic_pii/fake_code_samples/customer_export.py"),
        Path("data/synthetic_pii/fake_code_samples/chat_log.txt"),
        Path("data/synthetic_pii/fake_code_samples/crm_notes.md"),
    ]

    chunks = []
    for p in candidates:
        if p.exists():
            chunks.append(p.read_text(encoding="utf-8"))

    if not chunks:
        # 找不到語料檔時的備援假文字（純虛構）
        chunks = [
            "客戶王小明的聯絡地址是台北市信義區測試路100號，電話0900-000-000，"
            "身分證字號A123456789，此為測試假資料。" * 5
        ]

    text = "\n".join(chunks)
    while len(text) < target_chars:
        text += "\n" + text
    return text[:target_chars]


def benchmark(detector: NERDetector, text: str, runs: int = 20) -> list:
    # 先跑一次暖機（排除模型載入後第一次推論的額外開銷）
    detector.detect(text)

    timings_ms = []
    for _ in range(runs):
        start = time.perf_counter()
        detector.detect(text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        timings_ms.append(elapsed_ms)
    return timings_ms


def report(label: str, timings_ms: list) -> None:
    print(f"\n[{label}]")
    print(f"  次數     : {len(timings_ms)}")
    print(f"  最小值   : {min(timings_ms):.1f} ms")
    print(f"  平均值   : {statistics.mean(timings_ms):.1f} ms")
    print(f"  中位數   : {statistics.median(timings_ms):.1f} ms")
    print(f"  最大值   : {max(timings_ms):.1f} ms")


def main():
    text = load_benchmark_text()
    print(f"測試文字長度：{len(text)} 字元")

    # --- CPU ---
    detector_cpu = NERDetector(device=-1)
    timings_cpu = benchmark(detector_cpu, text, runs=20)
    report("CPU", timings_cpu)

    # --- GPU（若可用）---
    try:
        import torch

        if torch.cuda.is_available():
            detector_gpu = NERDetector(device=0)
            timings_gpu = benchmark(detector_gpu, text, runs=20)
            report("GPU", timings_gpu)
        else:
            print("\n[GPU] 未偵測到可用 CUDA GPU，略過")
    except ImportError:
        print("\n[GPU] 無法匯入 torch，略過")


if __name__ == "__main__":
    main()