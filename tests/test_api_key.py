"""core.rules.api_key 的 unit test。

注意：以下所有 API key / token / 密碼皆為手動組合的合成假資料，
並非任何真實服務所核發的金鑰或真實密碼。
"""

import unittest

from core.rules.api_key import detect_api_key, is_valid_api_key

SK_KEY = "sk-abcd1234EFGH5678ijkl"                      # 合成假 sk- 金鑰，23 碼
GHP_KEY = "ghp_abcdefgh1234567890ABCDEFGH123456"        # 合成假 GitHub PAT，36 碼
AKIA_KEY = "AKIA1234567890ABCDEF"                        # 合成假 AWS Access Key ID
ANTHROPIC_KEY = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"  # 合成假 Anthropic 金鑰
OPENAI_PROJ_KEY = "sk-proj-ABCDEFGHIJ1234567890abcdefghij"           # 合成假 OpenAI project key
GOOGLE_KEY = "AIzaAbCdEfGhIj1234567890abcdefghijAAAAA"               # 合成假 Google API key
SLACK_BOT_KEY = "xoxb-111111111111-222222222222-abcdefghijklmnopqrstuvwx"  # 合成假 Slack bot token
SLACK_USER_KEY = "xoxp-333333333333-444444444444-zyxwvutsrqponmlkjihgfedc"  # 合成假 Slack user token
# 合成假 JWT：header 為 {"alg":"HS256"}，payload/signature 為隨意佔位字串的 base64url 編碼
JWT_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9"
    ".c3ViamVjdC1wbGFjZWhvbGRlci1kYXRh"
    ".c2lnbmF0dXJlLXBsYWNlaG9sZGVyLXZhbHVl"
)


class IsValidApiKeyTests(unittest.TestCase):
    def test_valid_keys(self):
        valid_keys = [
            SK_KEY,
            GHP_KEY,
            AKIA_KEY,
            ANTHROPIC_KEY,
            OPENAI_PROJ_KEY,
            GOOGLE_KEY,
            SLACK_BOT_KEY,
            SLACK_USER_KEY,
            JWT_TOKEN,
        ]
        for key in valid_keys:
            with self.subTest(key=key):
                self.assertTrue(is_valid_api_key(key))

    def test_invalid_keys(self):
        invalid_keys = [
            "",
            "sk-short",              # sk- 但長度不足
            "ghp_tooshort",          # ghp_ 但長度不足
            "AKIAABC",               # AKIA 但長度不足
            "sk-ant-short",          # sk-ant- 但長度不足
            "sk-proj-short",         # sk-proj- 但長度不足
            "AIzashort",             # AIza 但長度不足
            "xoxb-short",            # xox[a-z]- 但長度不足
            "eyJhbGciOiJIUzI1NiJ9.onlyonesegment",  # JWT 只有兩段
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

    def test_detect_anthropic_key(self):
        text = f"ANTHROPIC_API_KEY={ANTHROPIC_KEY}"
        result = detect_api_key(text)

        texts_found = [span["text"] for span in result["spans"]]
        self.assertIn(ANTHROPIC_KEY, texts_found)

    def test_detect_openai_project_key(self):
        text = f"這是我的 OpenAI project key：{OPENAI_PROJ_KEY}"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["text"], OPENAI_PROJ_KEY)

    def test_detect_google_key(self):
        text = f"Google API key: {GOOGLE_KEY}"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["text"], GOOGLE_KEY)

    def test_detect_slack_tokens(self):
        text = f"bot token {SLACK_BOT_KEY}，user token {SLACK_USER_KEY}"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], SLACK_BOT_KEY)
        self.assertEqual(result["spans"][1]["text"], SLACK_USER_KEY)

    def test_detect_jwt(self):
        text = f"Authorization: Bearer {JWT_TOKEN}"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["text"], JWT_TOKEN)

    def test_detect_generic_assignment_styles(self):
        text = (
            "設定檔內容：\n"
            "API_KEY=mysecretvalue123\n"
            "token=anothersecret456\n"
            "password=p4ssw0rd123\n"
        )
        result = detect_api_key(text)

        texts_found = [span["text"] for span in result["spans"]]
        self.assertEqual(
            texts_found,
            ["mysecretvalue123", "anothersecret456", "p4ssw0rd123"],
        )
        for span in result["spans"]:
            self.assertEqual(span["type"], "API_KEY")

    def test_detect_generic_assignment_is_case_insensitive_and_quoted(self):
        text = 'Api-Key = "mysecretvalue123"'
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["text"], "mysecretvalue123")

    def test_detect_does_not_double_count_known_prefix_inside_assignment(self):
        # API_KEY=sk-xxx 這種賦值，值本身剛好也符合已知前綴樣式，
        # 應該只回報一筆，不能同時被「前綴樣式」與「賦值樣式」各記一次。
        text = f"API_KEY={SK_KEY}"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(result["spans"][0]["text"], SK_KEY)
        self.assertEqual(result["spans"][0]["replacement"], "[API_KEY_1]")

    def test_detect_mixed_prefix_and_assignment_styles_sequential_replacement(self):
        text = f"{ANTHROPIC_KEY} 是 sk-ant- 金鑰，另外 password=p4ssw0rd123 也要遮蔽。"
        result = detect_api_key(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], ANTHROPIC_KEY)
        self.assertEqual(result["spans"][0]["replacement"], "[API_KEY_1]")
        self.assertEqual(result["spans"][1]["text"], "p4ssw0rd123")
        self.assertEqual(result["spans"][1]["replacement"], "[API_KEY_2]")


if __name__ == "__main__":
    unittest.main()
