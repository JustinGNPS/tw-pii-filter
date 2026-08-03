"""
產生「假程式碼檔案」樣本 —— 把合成個資嵌入模擬真實開發情境的檔案，
供 B 的 Proxy demo（餵給 AI coding agent）或 D 的 NER 測試直接使用。

所有內容皆基於 synthetic_pii.json 產生的虛構資料，非真實個資。
"""

import json
from pathlib import Path


def load_records(json_path: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return data["records"]


def build_customer_export_py(records):
    rows = "\n".join(
        f'    {{"id": {r["id"]}, "name": "{r["name"]}", "id_number": "{r["taiwan_id"]}", '
        f'"address": "{r["address"]}", "phone": "{r["phone"]}"}},'
        for r in records[:8]
    )
    return f'''"""
customer_export.py
（合成測試資料，非真實個資 —— tw-pii-filter 專案 demo 用）

模擬情境：工程師把客戶資料匯出腳本貼給 AI coding agent 除錯，
測試 proxy 能否在轉發前偵測到裡面的個資。
"""

CUSTOMERS = [
{rows}
]


def export_to_csv(path: str) -> None:
    """把 CUSTOMERS 清單寫成 CSV，供客服系統匯入。"""
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "id_number", "address", "phone"])
        writer.writeheader()
        writer.writerows(CUSTOMERS)


if __name__ == "__main__":
    # 假設客服 陳志明（身分證 {records[0]["taiwan_id"]}）反應資料有誤，先匯出確認
    export_to_csv("customers_debug.csv")
'''


def build_chat_log_txt(records):
    lines = []
    for r in records[8:13]:
        lines.append(f"[客服] 您好，請問訂單是綁定哪個聯絡電話？")
        lines.append(f"[客戶] 我叫{r['name']}，電話是 {r['phone']}")
        lines.append(f"[客服] 好的，方便再提供收件地址嗎？")
        lines.append(f"[客戶] {r['address']}，身分證字號後面對帳需要的話是 {r['taiwan_id']}")
        lines.append("---")
    header = (
        "# chat_log.txt（合成測試資料，非真實個資 —— tw-pii-filter 專案 demo 用）\n"
        "# 模擬情境：客服對話紀錄外洩到程式碼倉庫，測試偵測層能否抓出裡面的個資\n\n"
    )
    return header + "\n".join(lines) + "\n"


def build_api_request_log_json(records):
    entries = []
    for r in records[13:18]:
        entries.append(
            {
                "timestamp": "2026-07-29T10:00:00+08:00",
                "endpoint": "/api/v1/user/verify",
                "request_body": {
                    "name": r["name"],
                    "id_number": r["taiwan_id"],
                    "phone": r["phone"],
                },
                "status": 200,
            }
        )
    payload = {
        "_notice": "合成測試資料，非真實個資 —— tw-pii-filter 專案 demo 用。模擬情境：API request log 不小心把明文個資寫進 log 檔。",
        "logs": entries,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_crm_notes_md(records):
    lines = [
        "# CRM 客戶備註（合成測試資料，非真實個資 —— tw-pii-filter 專案 demo 用）",
        "",
        "> 模擬情境：業務把客戶備註貼到筆記軟體，AI 協助整理摘要時經過 proxy。",
        "",
    ]
    for r in records[18:23]:
        lines.append(f"## 客戶：{r['name']}")
        lines.append(f"- 聯絡電話：{r['phone']}")
        lines.append(f"- 收件地址：{r['address']}")
        lines.append(f"- 身分證字號（對帳用）：{r['taiwan_id']}")
        lines.append("- 備註：對產品 A 有興趣，下週回電")
        lines.append("")
    return "\n".join(lines)


def main():
    base = Path(__file__).parent
    records = load_records(base / "output" / "synthetic_pii.json")

    out_dir = base / "output" / "fake_code_samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "customer_export.py").write_text(
        build_customer_export_py(records), encoding="utf-8"
    )
    (out_dir / "chat_log.txt").write_text(
        build_chat_log_txt(records), encoding="utf-8"
    )
    (out_dir / "api_request_log.json").write_text(
        build_api_request_log_json(records), encoding="utf-8"
    )
    (out_dir / "crm_notes.md").write_text(
        build_crm_notes_md(records), encoding="utf-8"
    )

    print(f"已產生 4 個假程式碼樣本檔於：{out_dir}")


if __name__ == "__main__":
    main()
