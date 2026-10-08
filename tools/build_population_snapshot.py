"""把內政部單一年齡人口 CSV 轉成 Layer 3 使用的離線統計快照。

原始資料來源：政府資料開放平臺「人口數單一年齡組─按性別、區域別分」。
本工具只保留縣市／性別／年齡的彙整人數，不處理或產生個人層級資料。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


EXPECTED_REGIONS = {
    "新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "宜蘭縣", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣",
    "嘉義縣", "屏東縣", "臺東縣", "花蓮縣", "澎湖縣", "基隆市",
    "新竹市", "嘉義市", "金門縣", "連江縣",
}
SEX_KEYS = {"性別總計": "all", "男": "male", "女": "female"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path, help="從內政部統計查詢下載的 UTF-8 CSV")
    parser.add_argument("output_json", type=Path, help="要產生的 JSON 快照路徑")
    parser.add_argument("--period", required=True, help="資料月份，格式 YYYY-MM")
    return parser.parse_args()


def _parse_row_name(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 3:
        raise ValueError(f"無法解析列名：{value!r}")
    return parts[0], parts[1], parts[2]


def _parse_count(value: str) -> int:
    """官方 CSV 以「-」表示零人，其他值應為整數。"""
    return 0 if value.strip() == "-" else int(value)


def build_snapshot(input_csv: Path, period: str) -> dict:
    with input_csv.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.reader(csv_file))

    if not rows:
        raise ValueError("CSV 是空的")
    header = rows[0]
    expected_ages = [f"{age}歲" for age in range(100)]
    if header[0:2] != ["人口數", "總計"] or header[2:102] != expected_ages:
        raise ValueError("CSV 欄位與預期的內政部單一年齡格式不符")

    roc_year = int(period[:4]) - 1911
    month = int(period[5:7])
    exact_period_label = f"{roc_year}年 {month}月"
    regions: dict[str, dict[str, dict]] = {}

    for row in rows[1:]:
        if not row:
            continue
        row_period, raw_region, raw_sex = _parse_row_name(row[0])
        # 查詢單一月份時，官方 CSV 會同時輸出「(m~m月)」彙整列與月份列；
        # 只取後者，避免把同一批人口重複寫入快照。
        if row_period != exact_period_label:
            continue
        if raw_region == "區域別總計":
            region = "全國"
        elif raw_region in EXPECTED_REGIONS:
            region = raw_region
        else:
            continue
        sex = SEX_KEYS.get(raw_sex)
        if sex is None:
            continue
        if len(row) < 104:
            raise ValueError(f"{row[0]!r} 的欄位數不足")
        regions.setdefault(region, {})[sex] = {
            "total": _parse_count(row[1]),
            "ages": [_parse_count(value) for value in row[2:102]],
            "age_100_plus": _parse_count(row[102]),
            "age_unknown": _parse_count(row[103]),
        }

    expected_region_keys = EXPECTED_REGIONS | {"全國"}
    if set(regions) != expected_region_keys:
        missing = sorted(expected_region_keys - set(regions))
        extra = sorted(set(regions) - expected_region_keys)
        raise ValueError(f"縣市集合不完整，missing={missing}, extra={extra}")
    for region, by_sex in regions.items():
        if set(by_sex) != set(SEX_KEYS.values()):
            raise ValueError(f"{region} 缺少性別列")
        if by_sex["male"]["total"] + by_sex["female"]["total"] != by_sex["all"]["total"]:
            raise ValueError(f"{region} 的男女總數無法對上性別總計")
        for sex, group in by_sex.items():
            age_sum = sum(group["ages"]) + group["age_100_plus"] + group["age_unknown"]
            if age_sum != group["total"]:
                raise ValueError(f"{region}/{sex} 的各年齡人數無法對上總人口")

    return {
        "schema_version": 1,
        "period": period,
        "source": {
            "dataset_id": "14226",
            "dataset_title": "人口數單一年齡組─按性別、區域別分",
            "publisher": "內政部統計處",
            "dataset_url": "https://data.gov.tw/dataset/14226",
        },
        "scope": "registered_population_by_single_age_sex_county_city",
        "regions": dict(sorted(regions.items())),
    }


def main() -> None:
    args = _parse_args()
    snapshot = build_snapshot(args.input_csv, args.period)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
