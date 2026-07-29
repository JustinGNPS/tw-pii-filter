"""
合成測試資料產生器（Synthetic PII Generator）
=================================================
產生「格式正確、但完全虛構」的台灣個資測試資料，供 NER / 規則層 / Proxy demo 使用。

嚴禁事項（CONTRIBUTING.md 鐵則 3）：
- 這裡產生的所有姓名、地址、身分證字號皆為隨機組合，不對應任何真實人物
- 身分證字號雖然通過官方 checksum 演算法驗證（格式合法），但號碼本身是隨機生成，
  並未查證是否曾核發給真人 —— 若要更保守，可再加一層「已知測試號碼黑名單排除」

用法：
    python generate_fake_pii.py --count 80 --seed 42
"""

import argparse
import csv
import json
import random
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. 台灣身分證字號 checksum 演算法
# ---------------------------------------------------------------------------

LETTER_MAP = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17,
    "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23,
    "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28, "V": 29, "W": 32, "X": 30,
    "Y": 31, "Z": 33,
}

WEIGHTS = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1]


def generate_taiwan_id(letter: str, gender_digit: int, serial7: list) -> str:
    """
    產生格式合法（checksum 正確）的台灣身分證字號。

    Args:
        letter: 縣市碼字母 A-Z
        gender_digit: 1（男）或 2（女）
        serial7: 長度 7 的數字列表（流水號部分）

    Returns:
        11 碼身分證字號字串，例如 "A123456789"
    """
    code = LETTER_MAP[letter]
    n1, n2 = divmod(code, 10)
    digits = [n1, n2, gender_digit, *serial7]
    weighted_sum = sum(d * w for d, w in zip(digits, WEIGHTS[:-1]))
    check_digit = (10 - (weighted_sum % 10)) % 10
    serial_str = "".join(str(d) for d in serial7)
    return f"{letter}{gender_digit}{serial_str}{check_digit}"


def random_taiwan_id(rng: random.Random) -> str:
    letter = rng.choice(list(LETTER_MAP.keys()))
    gender = rng.choice([1, 2])
    serial7 = [rng.randint(0, 9) for _ in range(7)]
    return generate_taiwan_id(letter, gender, serial7)


# ---------------------------------------------------------------------------
# 2. 假姓名產生（常見姓氏 + 常見名字隨機配對，非真人）
# ---------------------------------------------------------------------------

SURNAMES = list("陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡")
GIVEN_CHARS = list("志明淑芬俊傑雅婷家豪怡君建宏思穎宗翰佳穎冠廷詩涵柏翰宜蓁彥廷子晴")


def random_name(rng: random.Random) -> str:
    surname = rng.choice(SURNAMES)
    given_len = rng.choice([1, 2])
    given = "".join(rng.choice(GIVEN_CHARS) for _ in range(given_len))
    return surname + given


# ---------------------------------------------------------------------------
# 3. 假地址產生（真實行政區名稱 + 虛構路名門牌）
# ---------------------------------------------------------------------------

CITIES_DISTRICTS = {
    "台北市": ["信義區", "大安區", "中山區", "士林區", "內湖區"],
    "新北市": ["板橋區", "三重區", "中和區", "新莊區", "淡水區"],
    "台中市": ["西屯區", "北屯區", "南屯區", "北區", "西區"],
    "台南市": ["東區", "北區", "安平區", "永康區"],
    "高雄市": ["苓雅區", "三民區", "左營區", "鳳山區"],
    "新竹市": ["東區", "北區", "香山區"],
}

ROAD_NAMES = ["中正路", "自由路", "民生路", "光復路", "和平路", "建國路", "文心路", "復興路"]


def random_address(rng: random.Random) -> str:
    city = rng.choice(list(CITIES_DISTRICTS.keys()))
    district = rng.choice(CITIES_DISTRICTS[city])
    road = rng.choice(ROAD_NAMES)
    section = rng.choice(["一段", "二段", "三段", ""])
    lane = rng.randint(1, 300)
    number = rng.randint(1, 500)
    floor = rng.choice(["", f"{rng.randint(1, 12)}樓"])
    parts = [city, district, road]
    if section:
        parts.append(section)
    parts.append(f"{lane}巷{number}號")
    if floor:
        parts.append(floor)
    return "".join(parts)


# ---------------------------------------------------------------------------
# 4. 假電話號碼
# ---------------------------------------------------------------------------

def random_phone(rng: random.Random) -> str:
    return f"09{rng.randint(0, 9)}{rng.randint(0, 9)}-{rng.randint(0, 999999):06d}"


# ---------------------------------------------------------------------------
# 5. 組成一筆完整虛構個資紀錄
# ---------------------------------------------------------------------------

def generate_record(rng: random.Random, idx: int) -> dict:
    return {
        "id": idx,
        "name": random_name(rng),
        "taiwan_id": random_taiwan_id(rng),
        "address": random_address(rng),
        "phone": random_phone(rng),
    }


def main():
    parser = argparse.ArgumentParser(description="產生合成測試 PII 語料")
    parser.add_argument("--count", type=int, default=80, help="產生筆數")
    parser.add_argument("--seed", type=int, default=42, help="亂數種子，固定可重現")
    parser.add_argument(
        "--out-dir", type=str, default="output", help="輸出資料夾"
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = [generate_record(rng, i + 1) for i in range(args.count)]

    # --- CSV ---
    csv_path = out_dir / "synthetic_pii.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "taiwan_id", "address", "phone"])
        writer.writeheader()
        writer.writerows(records)

    # --- JSON ---
    json_path = out_dir / "synthetic_pii.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "_notice": "合成測試資料，非真實個資。所有姓名/地址/電話/身分證字號皆為程式隨機產生，僅供 tw-pii-filter 專案測試與 demo 使用。",
                "records": records,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"已產生 {len(records)} 筆合成資料：")
    print(f"  - {csv_path}")
    print(f"  - {json_path}")
    return records


if __name__ == "__main__":
    main()
