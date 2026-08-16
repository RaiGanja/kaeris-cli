"""Скачанный архив не может писать за пределы папки вывода.

Наш сервер строит имена членов из кода языка, а код сверяет с белым списком — но это ЕГО
половина защиты. Половины клиента не было вовсе: что архив сказал, то и писалось. Доказано
16.08.2026 подставным сервером (ровно туда уводит переменная KAERIS_API_URL): архив с членами
`../../.ssh/authorized_keys` и `../../../.bashrc` положил оба файла на диск, за пределы
проекта, и вернул их в списке «written» как обычный результат.

Здесь проверяются обе стороны сторожа: что он ловит побег И что он пропускает всё настоящее,
что мы правда кладём в архив (иначе «защита» просто ломает продукт).
"""
import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kaeris.client import KaerisClient, KaerisError, safe_member_name  # noqa: E402


ESCAPES = [
    "../../.ssh/authorized_keys",
    "../de.json",
    "a/../../de.json",
    "/etc/cron.d/de.json",
    "/de.json",
    "..\\..\\de.json",
    "C:/Windows/de.json",
    "C:\\Windows\\de.json",
    "values-de/nested/strings.xml",     # глубже одного уровня мы не кладём никогда
    "",
    ".",
    "..",
]

# Ровно то, что кладёт write_translation_zip в backend/translator.py.
LEGITIMATE = [
    ("de.json", "de.json"),
    ("de.yml", "de.yml"),
    ("de.arb", "de.arb"),
    ("de.strings", "de.strings"),
    ("de.po", "de.po"),
    ("de.xliff", "de.xliff"),
    ("de.properties", "de.properties"),
    ("de.resx", "de.resx"),
    ("de.ftl", "de.ftl"),
    ("de.md", "de.md"),
    ("zh-Hans.json", "zh-Hans.json"),
    ("pt_BR.json", "pt_BR.json"),
    ("translations.csv", "translations.csv"),
    ("Localizable.xcstrings", "Localizable.xcstrings"),
    ("values-de/strings.xml", "values-de/strings.xml"),
    ("./de.json", "de.json"),
]


class TestSafeMemberName(unittest.TestCase):
    def test_escaping_names_are_refused(self):
        for name in ESCAPES:
            with self.subTest(name=name):
                with self.assertRaises(KaerisError):
                    safe_member_name(name)

    def test_real_member_names_pass_unchanged(self):
        for name, expected in LEGITIMATE:
            with self.subTest(name=name):
                self.assertEqual(safe_member_name(name), expected)


class TestDownloadRefusesTheWholeArchive(unittest.TestCase):
    """Отказ не бывает частичным: архив с таким членом — не наш архив, из него не берётся
    ничего, включая внешне честный de.json."""

    def _client_with(self, members):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, body in members.items():
                zf.writestr(name, body)
        client = KaerisClient(api_url="https://example.invalid")
        client._get = lambda path, headers=None: buf.getvalue()
        return client

    def test_evil_member_refuses_everything(self):
        client = self._client_with({"de.json": json.dumps({"a": "Hallo"}),
                                    "../../.ssh/authorized_keys": "ssh-rsa AAAA"})
        with self.assertRaises(KaerisError) as ctx:
            client.download("job")
        self.assertIn("outside the output directory", str(ctx.exception))

    def test_honest_archive_still_downloads(self):
        client = self._client_with({"de.json": json.dumps({"a": "Hallo"}),
                                    "values-de/strings.xml": "<resources/>"})
        members = client.download("job")
        self.assertEqual(sorted(members), ["de.json", "values-de/strings.xml"])

    def test_directory_entries_are_skipped_not_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("values-de/", b"")
            zf.writestr("values-de/strings.xml", "<resources/>")
        client = KaerisClient(api_url="https://example.invalid")
        client._get = lambda path, headers=None: buf.getvalue()
        self.assertEqual(sorted(client.download("job")), ["values-de/strings.xml"])


if __name__ == "__main__":
    unittest.main()
