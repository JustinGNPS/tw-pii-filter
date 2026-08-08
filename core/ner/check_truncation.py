"""
check_truncation.py
======================
驗證 gyr66/bert-base-chinese-finetuned-ner 是否有 512 token 截斷問題。

B 在 7/31 提出的疑慮：agent 送出的單一欄位常遠超過 512 token 能撐住的字數
（B 實測 Aider 單一欄位 2761 字元），如果模型輸入長度真的受限，語意層可能
只掃到文字前面一截，而且不會報錯，是無聲的漏測，直接影響 B 手上 #11
（語意層接線）能不能放心採用這個結果。

用法（在 repo 根目錄執行）：
    python core/ner/check_truncation.py
"""

from pathlib import Path

from transformers import AutoTokenizer

from detector import detect_ner

MODEL_NAME = "gyr66/bert-base-chinese-finetuned-ner"


def check_token_length() -> None:
    """檢查一：tokenizer 對長文字的實際 token 數，跟模型上限比對。"""
    print("=" * 60)
    print("檢查一：tokenizer 對長文字的實際 token 數")
    print("=" * 60)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    # 用之前產生的合成語料當測試文字，跟 B 說的 2761 字元量級接近
    sample_path = Path("data/synthetic_pii/fake_code_samples/customer_export.py")
    if sample_path.exists():
        text = sample_path.read_text(encoding="utf-8")
    else:
        text = "測試文字。" * 600  # 找不到語料檔時的備援，約 3000 字元

    ids = tokenizer(text)["input_ids"]
    print(f"文字長度：{len(text)} 字元")
    print(f"Token 數：{len(ids)}")
    print(f"tokenizer.model_max_length = {tokenizer.model_max_length}")

    if len(ids) >= 512:
        print("⚠️ Token 數 >= 512，這篇文字很可能會被截斷")
    else:
        print("Token 數在 512 以內，這篇文字不會被截斷")


def check_practical_detection() -> None:
    """檢查二：把假姓名放在文字開頭 vs 第 2000 字元，比較實際偵測結果。"""
    print("\n" + "=" * 60)
    print("檢查二：假姓名放在開頭 vs 第 2000 字元，比較 detect_ner() 的結果")
    print("=" * 60)

    filler = "這是填充文字，用來把後面的內容往後推。" * 60
    padding = (filler * 2)[:2000]  # 精確截到 2000 字元長

    text_front = "王小明是負責人。" + padding
    text_back = padding + "王小明是負責人。"

    front_result = detect_ner(text_front)
    back_result = detect_ner(text_back)

    front_hit = any(r["text"] == "王小明" for r in front_result)
    back_hit = any(r["text"] == "王小明" for r in back_result)

    print(f"姓名放在開頭時偵測到：{front_hit}")
    print(f"姓名放在第 2000 字元後偵測到：{back_hit}")

    if front_hit and not back_hit:
        print("\n❌ 確認截斷：開頭抓得到、後面抓不到，超過某個長度後的內容沒被掃到")
    elif front_hit and back_hit:
        print("\n✅ 兩處都抓到，這個長度（2000+ 字元）沒有截斷問題")
    else:
        print("\n⚠️ 兩處都沒抓到，這個測試本身可能有問題（不是截斷相關），需要再檢查")


if __name__ == "__main__":
    check_token_length()
    check_practical_detection()