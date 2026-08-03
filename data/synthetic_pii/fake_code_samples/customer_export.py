"""
customer_export.py
（合成測試資料，非真實個資 —— tw-pii-filter 專案 demo 用）

模擬情境：工程師把客戶資料匯出腳本貼給 AI coding agent 除錯，
測試 proxy 能否在轉發前偵測到裡面的個資。
"""

CUSTOMERS = [
    {"id": 1, "name": "劉翰", "id_number": "H121819605", "address": "台北市信義區光復路二段259巷309號", "phone": "0986-231148"},
    {"id": 2, "name": "羅志怡", "id_number": "W254235114", "address": "台南市東區建國路三段136巷414號1樓", "phone": "0981-967096"},
    {"id": 3, "name": "呂佳", "id_number": "U293103416", "address": "新北市板橋區文心路三段233巷326號", "phone": "0955-219684"},
    {"id": 4, "name": "廖怡", "id_number": "R127648358", "address": "台北市大安區中正路三段206巷138號", "phone": "0995-222955"},
    {"id": 5, "name": "簡廷豪", "id_number": "I138849690", "address": "台南市安平區光復路二段261巷253號", "phone": "0912-657924"},
    {"id": 6, "name": "許俊柏", "id_number": "M284801840", "address": "新竹市北區自由路三段223巷81號", "phone": "0948-798975"},
    {"id": 7, "name": "鄭穎", "id_number": "U125288099", "address": "台中市北區中正路一段186巷450號", "phone": "0903-920659"},
    {"id": 8, "name": "王晴", "id_number": "C127824892", "address": "台南市北區光復路三段205巷344號11樓", "phone": "0978-473417"},
]


def export_to_csv(path: str) -> None:
    """把 CUSTOMERS 清單寫成 CSV，供客服系統匯入。"""
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "id_number", "address", "phone"])
        writer.writeheader()
        writer.writerows(CUSTOMERS)


if __name__ == "__main__":
    # 假設客服 陳志明（身分證 H121819605）反應資料有誤，先匯出確認
    export_to_csv("customers_debug.csv")
