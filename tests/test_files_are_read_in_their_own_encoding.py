"""`kaeris check` и кодировки — пункт 7 плана безопасности, канал CLI.

ЧТО ОХРАНЯЕМ. `check` стоит у клиентов в CI и читал файлы так: `open(path,
encoding="utf-8")`. Обычный JSON с BOM — его ставит Visual Studio, «Блокнот» и половина
инструментов Windows — валил проверку:

    ✗ Could not read source file en.json: Unexpected UTF-8 BOM (decode using utf-8-sig)
    код выхода 2

Питоновский жаргон в чужой сборке, красный CI, и проверка не выполнена ВООБЩЕ — то есть
сторож, который клиент поставил ради ошибок локализации, молчал о них по причине, не
имеющей к ним отношения. Файл в UTF-16 (так пишет Xcode) — та же история.

ВТОРАЯ ПОЛОВИНА. Файл ЦЕЛЕВОГО языка в нераспознанной кодировке `check` считал
отсутствующим и печатал «все ключи пропущены» — человек шёл искать пропавший перевод,
которого никто не терял.

Правила чтения — копия бэкенда (`kaeris/encoding.py`), расхождение стережёт
`test_encoding_parity.py`.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kaeris import encoding as enc  # noqa: E402

ЗНАЧЕНИЕ = "Café — Grüße 👋"

КОДИРОВКИ = {
    "utf-8": lambda s: s.encode("utf-8"),
    "utf-8 с BOM": lambda s: b"\xef\xbb\xbf" + s.encode("utf-8"),
    "utf-16 с BOM": lambda s: s.encode("utf-16"),
    "utf-16-le без BOM": lambda s: s.encode("utf-16-le"),
    "utf-16-be без BOM": lambda s: s.encode("utf-16-be"),
}


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "kaeris.cli", *args],
                          cwd=cwd or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          capture_output=True, text=True)


class CheckReadsRealFiles(unittest.TestCase):
    """Настоящий путь: подпроцесс `kaeris check`, а не вызов функции (3-е правило)."""

    def _проект(self, tmp, кодировка, значение=ЗНАЧЕНИЕ, только_цель=False):
        док = json.dumps({"k": значение}, ensure_ascii=False)
        байты = КОДИРОВКИ[кодировка](док)
        with open(os.path.join(tmp, "en.json"), "wb") as f:
            f.write(док.encode("utf-8") if только_цель else байты)
        with open(os.path.join(tmp, "de.json"), "wb") as f:
            f.write(байты)
        return os.path.join(tmp, "en.json")

    def test_every_encoding_a_real_tool_writes_is_read(self):
        for кодировка in КОДИРОВКИ:
            with self.subTest(кодировка=кодировка), tempfile.TemporaryDirectory() as tmp:
                src = self._проект(tmp, кодировка)
                r = _run("check", "--source", src, "--langs", "de")
                self.assertEqual(r.returncode, 0,
                                 f"{кодировка}: check не смог прочитать свой же файл\n"
                                 f"{r.stdout}{r.stderr}")
                self.assertNotIn("Could not read", r.stdout + r.stderr)

    def test_a_target_file_is_not_called_missing_because_of_its_encoding(self):
        """Файл цели существует и полон — про «пропущенные ключи» речи быть не должно."""
        for кодировка in ("utf-8 с BOM", "utf-16 с BOM"):
            with self.subTest(кодировка=кодировка), tempfile.TemporaryDirectory() as tmp:
                src = self._проект(tmp, кодировка, только_цель=True)
                r = _run("check", "--source", src, "--langs", "de")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("missing", (r.stdout + r.stderr).lower())

    def test_an_unreadable_encoding_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as tmp:
            путь = os.path.join(tmp, "en.json")
            with open(путь, "wb") as f:
                f.write(json.dumps({"k": "Café"}, ensure_ascii=False).encode("latin-1"))
            r = _run("check", "--source", путь, "--langs", "de")
            вывод = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0)
            # Именно СОВЕТ, а не слово «UTF-8»: оно есть и в самом сообщении об ошибке,
            # и проверка на него зеленела бы с выключенной подсказкой (поймал срыв).
            self.assertIn("save it as UTF-8", вывод,
                          "сказали, что файл нечитаем, но не сказали, что делать")
            self.assertNotIn("utf-8-sig", вывод, "жаргон питона вместо совета человеку")

    def test_the_canary_proves_this_test_can_fail(self):
        """Сломанный файл обязан дать ненулевой код — иначе проверки выше ничего не значат."""
        with tempfile.TemporaryDirectory() as tmp:
            путь = os.path.join(tmp, "en.json")
            with open(путь, "wb") as f:
                f.write(b'{"k": "no closing brace"')
            self.assertNotEqual(_run("check", "--source", путь, "--langs", "de").returncode, 0)


class DecoderItself(unittest.TestCase):
    def test_ascii_in_utf16_without_a_bom_is_not_mistaken_for_utf8(self):
        raw = '{"k": "Hello"}'.encode("utf-16-le")
        self.assertNotEqual(raw.decode("utf-8"), '{"k": "Hello"}')   # предпосылка
        self.assertEqual(enc.decode_bytes(raw, "en.json"), '{"k": "Hello"}')

    def test_a_bom_never_becomes_part_of_the_text(self):
        self.assertEqual(enc.decode_bytes(b"\xef\xbb\xbfhello", "a.md"), "hello")

    def test_java_properties_fall_back_to_latin_1_like_java(self):
        self.assertEqual(enc.decode_bytes("k=Café\n".encode("latin-1"), "a.properties"),
                         "k=Café\n")

    def test_the_latin_1_fallback_is_only_for_properties(self):
        with self.assertRaises(enc.UnknownEncoding):
            enc.decode_bytes("k=Café\n".encode("latin-1"), "a.json")

    def test_binary_is_refused(self):
        with self.assertRaises(enc.UnknownEncoding):
            enc.decode_bytes(b'{"k": "\x00\x01\x02"}', "a.json")


if __name__ == "__main__":
    unittest.main()
