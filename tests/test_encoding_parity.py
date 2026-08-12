"""`kaeris/encoding.py` — построчная копия правил из `backend/translator.py`.

Две копии живут в разных пакетах (клиент ставит `kaeris` отдельно, бэкенд он не видит), и
разъезжаются они молча: сервер начнёт принимать файл, который `check` в CI объявит нечитаемым,
или наоборот. Поэтому один корпус БАЙТОВ гоняется через обе стороны и сравнивается результат
в результат — включая текст отказа.

Dev-only: на чужой машине и в опубликованном пакете бэкенда нет, тест пропускается.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kaeris import encoding as enc  # noqa: E402


def _backend():
    here = os.path.dirname(__file__)
    backend = os.path.abspath(os.path.join(here, "..", "..", "backend"))
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        import translator  # noqa
        return translator
    except Exception:
        return None


BK = _backend()

ЗН = "Café — Grüße 👋 Привет"

КОРПУС = [
    ("en.json", ЗН.encode("utf-8")),
    ("en.json", b"\xef\xbb\xbf" + ЗН.encode("utf-8")),
    ("en.json", ЗН.encode("utf-16")),
    ("en.json", ЗН.encode("utf-16-le")),
    ("en.json", ЗН.encode("utf-16-be")),
    ("en.json", ЗН.encode("utf-32")),
    ("en.json", "Hello".encode("utf-16-le")),          # ASCII в UTF-16: нули — валидный UTF-8
    ("en.json", "Café".encode("latin-1")),             # отказ
    ("en.json", "Привет".encode("cp1251")),            # отказ
    ("en.json", b'{"k": "\x00\x01"}'),                 # NUL: отказ
    ("a.properties", "k=Café\n".encode("latin-1")),    # спецификация Java: не отказ
    ("a.properties", "k=Café\n".encode("utf-8")),
    ("a.md", b""),                                     # пусто — не ошибка кодировки
    ("a.md", b"\xef\xbb\xbf"),                         # только BOM
    ("a.md", "日本語".encode("utf-8")),
    ("a.md", b"\xff\xfe"),                             # только BOM UTF-16
]


@unittest.skipIf(BK is None, "бэкенд не импортируется (опубликованный пакет / чужая машина)")
class Parity(unittest.TestCase):
    def _исход(self, fn, имя, байты):
        try:
            return ("ok", fn(байты, имя))
        except ValueError as e:
            return ("отказ", str(e))

    def test_both_copies_answer_identically(self):
        for имя, байты in КОРПУС:
            with self.subTest(файл=имя, байты=байты[:16]):
                наш = self._исход(enc.decode_bytes, имя, байты)
                их = self._исход(BK.decode_upload, имя, байты)
                self.assertEqual(наш, их, f"копии разъехались на {имя} / {байты[:16]!r}")

    def test_the_corpus_covers_both_outcomes(self):
        """Канарейка: корпус, в котором нет ни одного отказа, доказывал бы только то,
        что обе копии умеют говорить «ок»."""
        исходы = {self._исход(enc.decode_bytes, имя, б)[0] for имя, б in КОРПУС}
        self.assertEqual(исходы, {"ok", "отказ"})


if __name__ == "__main__":
    unittest.main()
