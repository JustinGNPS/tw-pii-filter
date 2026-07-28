"""core.rules.email 的 unit test。

注意：以下所有 email 地址皆為合成假資料（example.com 為 RFC 2606 保留測試網域）。
"""

import unittest

from core.rules.email import detect_email, is_valid_email


class IsValidEmailTests(unittest.TestCase):
    def test_valid_formats(self):
        valid_emails = [
            "test@example.com",
            "a.b+tag@sub.example.co",
            "user_name-123@example-domain.io",
        ]
        for email in valid_emails:
            with self.subTest(email=email):
                self.assertTrue(is_valid_email(email))

    def test_invalid_formats(self):
        invalid_emails = [
            "",
            "not-an-email",
            "missing-domain@",
            "@missing-local.com",
            "double@@example.com",
            "no-tld@example",
            None,
            12345,
        ]
        for email in invalid_emails:
            with self.subTest(email=email):
                self.assertFalse(is_valid_email(email))


class DetectEmailTests(unittest.TestCase):
    def test_detect_single_email(self):
        text = "王小明的信箱是 test@example.com，歡迎聯絡。"
        result = detect_email(text)

        self.assertEqual(result["text"], text)
        self.assertEqual(len(result["spans"]), 1)

        span = result["spans"][0]
        start, end = span["start"], span["end"]
        self.assertEqual(text[start:end], "test@example.com")
        self.assertEqual(span["type"], "EMAIL")
        self.assertEqual(span["source"], "rule")
        self.assertEqual(span["replacement"], "[EMAIL_1]")
        self.assertGreater(span["confidence"], 0)

    def test_detect_multiple_emails_sequential_replacement(self):
        text = "聯絡人：alice@example.com 與 bob@example.org。"
        result = detect_email(text)

        self.assertEqual(len(result["spans"]), 2)
        self.assertEqual(result["spans"][0]["text"], "alice@example.com")
        self.assertEqual(result["spans"][0]["replacement"], "[EMAIL_1]")
        self.assertEqual(result["spans"][1]["text"], "bob@example.org")
        self.assertEqual(result["spans"][1]["replacement"], "[EMAIL_2]")

    def test_detect_no_match_in_plain_text(self):
        text = "這段文字沒有任何 email 地址。"
        result = detect_email(text)

        self.assertEqual(result["spans"], [])


if __name__ == "__main__":
    unittest.main()
