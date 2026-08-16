"""Адрес API и ключ — два места, где секрет уходит наружу молча.

Пункт 6, замерено 16.08.2026 подставным сервером в локальной сети: при `KAERIS_API_URL`
на `http://` ключ ПЛАТЯЩЕГО приезжал на чужой хост в заголовке `X-API-Key` открытым текстом
вместе с содержимым переводимого файла. Защита на переадресацию (`_SameHostRedirect`) в
клиенте уже была — она стерегла ВТОРОЙ прыжок, а первый шёл куда угодно и как угодно.
Правило доктрины: защита обязана покрывать все измерения — и https, и хост, и переадресацию.

Пункт 5: ключ с невидимым переносом строки или похожей на латиницу кириллицей раньше давал
UnicodeEncodeError/ValueError из недр http.client — агент получал трейсбек про заголовки, а
человек не догадывался, что с ключом всё в порядке, кроме одного символа. И главное: сам ключ
не должен появляться ни в одном сообщении об ошибке.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kaeris.client import KaerisClient, KaerisError, checked_api_url, checked_key  # noqa: E402


class TestApiUrl(unittest.TestCase):
    def test_plain_http_to_any_real_host_is_refused(self):
        for url in ("http://192.168.0.151:8000", "http://example.com",
                    "http://kaeris.dev", "http://10.0.0.5"):
            with self.subTest(url=url):
                with self.assertRaises(KaerisError) as ctx:
                    KaerisClient(api_url=url)
                self.assertIn("https", str(ctx.exception))

    def test_localhost_over_http_still_works(self):
        """У разработчика с локальным backend'ом провода нет — ломать его незачем."""
        for url in ("http://127.0.0.1:8000", "http://localhost:8000", "http://127.5.5.5"):
            with self.subTest(url=url):
                self.assertEqual(KaerisClient(api_url=url).api_url, url)

    def test_https_and_bare_host_pass(self):
        self.assertEqual(checked_api_url("https://kaeris.dev/"), "https://kaeris.dev")
        self.assertEqual(checked_api_url("kaeris.dev"), "https://kaeris.dev")

    def test_other_schemes_are_refused(self):
        for url in ("ftp://kaeris.dev", "file:///etc/passwd", "ws://kaeris.dev", ""):
            with self.subTest(url=url):
                with self.assertRaises(KaerisError):
                    checked_api_url(url)


class TestKeys(unittest.TestCase):
    def test_invisible_whitespace_is_forgiven(self):
        c = KaerisClient(api_key="  kaerisp_abc123\n", openrouter_key="sk-or-v1-xyz\r\n")
        self.assertEqual(c.api_key, "kaerisp_abc123")
        self.assertEqual(c.openrouter_key, "sk-or-v1-xyz")
        self.assertEqual(c._headers()["X-API-Key"], "kaerisp_abc123")

    def test_unsendable_characters_are_named_not_crashed(self):
        for bad in ("kaerisp_ключ", "kaerisp_a“b", "kaerisp_a\x00b"):
            with self.subTest(key=bad):
                with self.assertRaises(KaerisError) as ctx:
                    KaerisClient(api_key=bad)
                self.assertIn("cannot be sent", str(ctx.exception))

    def test_the_key_is_never_in_the_error_text(self):
        """Отказ про ключ не имеет права цитировать ключ: сообщение уедет в стенограмму
        агента и в лог его клиента."""
        secret = "kaerisp_ОЧЕНЬ_секретный_1234567890"
        with self.assertRaises(KaerisError) as ctx:
            KaerisClient(api_key=secret)
        message = str(ctx.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn("1234567890", message)

    def test_empty_and_absent_keys_stay_anonymous(self):
        self.assertIsNone(checked_key(None, "x"))
        self.assertIsNone(checked_key("   ", "x"))


if __name__ == "__main__":
    unittest.main()
