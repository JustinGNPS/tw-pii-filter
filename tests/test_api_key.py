"""core.rules.api_key 的 unit test。

注意：以下所有 API key / token 皆為手動組合的合成假資料，
並非任何真實服務所核發的金鑰。
"""

import unittest

from core.rules.api_key import detect_api_key, is_valid_api_key

SK_KEY = "sk-abcd1234EFGH5678ijkl"                      # 合成假 sk- 金鑰，23 碼
GHP_KEY = "ghp_abcdefgh1234567890ABCDEFGH123456"        # 合成假 GitHub PAT，36 碼
AKIA_KEY = "AKIA1234567890ABCDEF"                        # 合成假 AWS Access Key ID


class IsValidApiKeyTests(unittest.TestCase):
    def test_valid_keys(self):
        valid_keys = [SK_KEY, GHP_KEY, AKIA_KEY]
        for key in valid_keys:
            with self.subTest(key=key):
                self.assertTrue(is_valid_api_key(key))

    def test_invalid_keys(self):
        invalid_keys = [
            "",
            "sk-short",           # sk- 但長度不足
            "ghp_tooshort",       # ghp_ 但長度不足
            "AKIAABC",            # AKIA 但長度不足
            "not-a-key-at-all",
            None,
            12345,
        ]
        for key in invalid_keys:
            with self.subTest(key=key):
                self.assertFalse(is_valid_api_key(key))


class DetectApiKeyTests(unittest.TestCase):
    def test_detect_single_sk_key(self):
        text = f"我不小心把金鑰貼上來了：{SK_KEY}，麻煩幫我撤銷。"
        result = detect_api_key(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], SK_KEY)
        self.assertEqual(span["type"], "API_KEY")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[API_KEY_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_key_styles_sequential_replacement(self):
        text = f"金鑰：{SK_KEY}，token：{GHP_KEY}，AWS key：{AKIA_KEY}。"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 3)
        self.assertEqual(result["spans"][0]["text"], SK_KEY)
        self.assertEqual(result["spans"][0]["replacement"], "[API_KEY_1]")
        self.assertEqual(result["spans"][1]["text"], GHP_KEY)
        self.assertEqual(result["spans"][1]["replacement"], "[API_KEY_2]")
        self.assertEqual(result["spans"][2]["text"], AKIA_KEY)
        self.assertEqual(result["spans"][2]["replacement"], "[API_KEY_3]")

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何 API key。"
        result = detect_api_key(text)

        self.assertEqual(result["spans"], [])

    def test_detect_ignores_key_embedded_in_longer_token(self):
        # 前後緊接其他英數字時，不應被截斷誤判
        text = f"字串 X{AKIA_KEY}Y 不是有效的 AWS key"
        result = detect_api_key(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
