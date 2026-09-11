"""以官方彙整人口資料估算準識別子組合的母體人數上限。

這裡回傳的是「上限」而不是真正的 k-anonymity 保證：快照只涵蓋年齡、性別、
縣市，文字中的職稱、公司、較細行政區等條件都可能讓實際符合人數更少。
執行時只讀取 repo 內的彙整快照，不會連線到外部服務。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional


SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "public_stats"
    / "tw_population_age_sex_region_202607.json"
)

_REGION_ALIASES = {
    "台北市": "臺北市",
    "台中市": "臺中市",
    "台南市": "臺南市",
    "台東縣": "臺東縣",
}
_REGIONS = (
    "新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "宜蘭縣", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣",
    "嘉義縣", "屏東縣", "臺東縣", "花蓮縣", "澎湖縣", "基隆市",
    "新竹市", "嘉義市", "金門縣", "連江縣",
)
_GENDER_TO_SNAPSHOT_KEY = {"male": "male", "female": "female"}
_SUPPORTED_TYPE_BY_DIMENSION = {
    "age": "AGE",
    "gender": "GENDER",
    "region": "ADDRESS",
}


class PopulationSnapshotError(ValueError):
    """離線人口快照缺漏或格式錯誤。"""


@lru_cache(maxsize=1)
def load_population_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    with path.open("r", encoding="utf-8") as snapshot_file:
        snapshot = json.load(snapshot_file)
    if snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("regions"), dict):
        raise PopulationSnapshotError("不支援的人口快照格式")
    return snapshot


def extract_county_city(spans: Optional[list[dict]]) -> Optional[str]:
    """從 ADDRESS spans 解析唯一的縣市；沒有或同時出現多縣市時回傳 None。"""
    found = set()
    for span in spans or []:
        if span.get("type") != "ADDRESS":
            continue
        value = str(span.get("text", ""))
        normalized = value.replace("台", "臺")
        for region in _REGIONS:
            if region in normalized:
                found.add(region)
        for alias, region in _REGION_ALIASES.items():
            if alias in value:
                found.add(region)
    if len(found) == 1:
        return found.pop()
    return None


def _count_for_group(snapshot: dict, *, age: Optional[int], gender: Optional[str],
                     region: Optional[str], age_range: Optional[tuple[int, int]] = None) -> int:
    region_key = region or "全國"
    sex_key = _GENDER_TO_SNAPSHOT_KEY.get(gender, "all")
    try:
        group = snapshot["regions"][region_key][sex_key]
    except KeyError as exc:
        raise PopulationSnapshotError(f"快照缺少群組：{region_key}/{sex_key}") from exc

    if age_range is not None:
        start, end = age_range
        return sum(group["ages"][start:end + 1])
    if age is not None:
        return group["ages"][age]
    return group["total"]


def estimate_population_upper_bound(
    *,
    age: Optional[int],
    gender: Optional[str],
    region: Optional[str],
    contributing_types: Iterable[str],
    snapshot: Optional[dict] = None,
) -> Optional[dict]:
    """回傳官方交叉統計中的群組人數；可用維度少於兩個時不估算。"""
    dimensions = {"age": age, "gender": gender, "region": region}
    present_dimensions = [name for name, value in dimensions.items() if value is not None]
    if len(present_dimensions) < 2:
        return None
    if age is not None and not 0 <= age <= 99:
        return None
    if gender is not None and gender not in _GENDER_TO_SNAPSHOT_KEY:
        return None

    snapshot = snapshot or load_population_snapshot()
    count = _count_for_group(snapshot, age=age, gender=gender, region=region)
    covered_types = sorted(_SUPPORTED_TYPE_BY_DIMENSION[name] for name in present_dimensions)
    uncovered_types = sorted(set(contributing_types) - set(covered_types))
    generalizations = []

    if age is not None:
        bucket_start = (age // 5) * 5
        bucket_end = min(bucket_start + 4, 99)
        generalizations.append({
            "dimension": "age",
            "from": age,
            "to": {"min": bucket_start, "max": bucket_end},
            "population_upper_bound": _count_for_group(
                snapshot,
                age=None,
                gender=gender,
                region=region,
                age_range=(bucket_start, bucket_end),
            ),
        })
    if region is not None:
        generalizations.append({
            "dimension": "region",
            "from": region,
            "to": "全國",
            "population_upper_bound": _count_for_group(
                snapshot, age=age, gender=gender, region=None
            ),
        })
    if gender is not None:
        generalizations.append({
            "dimension": "gender",
            "from": gender,
            "to": "all",
            "population_upper_bound": _count_for_group(
                snapshot, age=age, gender=None, region=region
            ),
        })

    return {
        "population_upper_bound": count,
        "dimensions": dimensions,
        "covered_types": covered_types,
        "uncovered_types": uncovered_types,
        "method": "official_joint_population_upper_bound",
        "data_period": snapshot["period"],
        "source_dataset_id": snapshot["source"]["dataset_id"],
        "generalizations": generalizations,
    }
