# cli/tests/test_check_nonstring_values.py
"""A locale file is not all strings. Real projects keep numbers, booleans, lists and
nulls next to their copy (limits, feature flags, ordered options) — and `kaeris
translate` deliberately carries them through untouched, so the very file WE write
contains them.

`check` guarded the per-string detectors against non-strings and then handed the same
unfiltered dict to the file-level ones: `_register_faults` joins every value, so a
single number crashed the run with a Python traceback. It only bit languages that have
T–V register markers (de/fr/es/it/ru) — the most common targets — so it looked like it
worked for whoever happened to try Japanese first.

The firewall must return a verdict, never a traceback.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from kaeris import check as chk  # noqa: E402

NONSTRING = {
    "greeting": "Hello, {name}!",
    "maxUsers": 100,          # number
    "enabled": True,          # boolean
    "tags": ["a", "b"],       # list
    "nothing": None,          # null
    "nested": {"deep": "Deep text"},
}
TRANSLATED = {
    "greeting": "Hallo, {name}!",
    "maxUsers": 100,
    "enabled": True,
    "tags": ["a", "b"],
    "nothing": None,
    "nested": {"deep": "Tiefer Text"},
}

# Every language with T–V register markers, plus a few without: the crash was
# language-dependent, so the test has to sweep both sides of that line.
LANGS = ["de", "fr", "es", "it", "ru", "ja", "zh", "ar", "pl", "nl", "pt", "tr", "ko"]


class NonStringValuesDoNotCrashCheck(unittest.TestCase):
    def test_check_locales_survives_every_language(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                result = chk.check_locales(
                    NONSTRING, [lang], lambda _l: TRANSLATED, []
                )
                self.assertTrue(
                    result["ok"],
                    f"{lang}: a locale that differs only in its strings must pass",
                )
                self.assertEqual(result["missing"], {}, f"{lang}: nothing is missing")

    def test_non_string_values_are_not_reported_as_untranslated(self):
        """A number identical on both sides is not an untranslated string."""
        result = chk.check_locales(NONSTRING, ["de"], lambda _l: TRANSLATED, [])
        blob = json.dumps(result, ensure_ascii=False)
        for key in ("maxUsers", "enabled", "tags", "nothing"):
            self.assertNotIn(key, blob, f"{key} is not copy — it must not be flagged")

    def test_file_level_detectors_still_fire_on_strings(self):
        """Filtering non-strings must not silence the file-level detectors themselves:
        a German file mixing du and Sie is still reported."""
        src = {
            "a": "You have mail", "b": "You are late", "c": "Your seat",
            "d": "You can leave", "n": 42,
        }
        # Two informal and two formal markers: the detector fires only when BOTH sides
        # appear at least twice ("Ihr" is not one of them — checked against the detector).
        tgt = {
            "a": "Du hast Post", "b": "Du bist spät", "c": "Sie haben Post",
            "d": "Sie können gehen", "n": 42,
        }
        result = chk.check_locales(src, ["de"], lambda _l: tgt, [])
        self.assertTrue(
            any("register" in w["msg"] for w in result["warnings"]),
            "mixed du/Sie must still be flagged when a number sits in the file",
        )


class CheckCommandExitsCleanly(unittest.TestCase):
    """End to end through the real command: a traceback would reach CI as exit 1 and
    read as 'your locale is broken'."""

    def _run(self, args, cwd):
        return subprocess.run(
            [sys.executable, "-m", "kaeris.cli"] + args,
            cwd=cwd, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.path.abspath(
                os.path.join(os.path.dirname(__file__), ".."))},
        )

    def test_check_and_json_exit_zero_on_a_clean_locale_with_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = os.path.join(tmp, "locales")
            os.makedirs(loc)
            with open(os.path.join(loc, "en.json"), "w", encoding="utf-8") as f:
                json.dump(NONSTRING, f, ensure_ascii=False)
            with open(os.path.join(loc, "de.json"), "w", encoding="utf-8") as f:
                json.dump(TRANSLATED, f, ensure_ascii=False)

            plain = self._run(
                ["check", "--source", "locales/en.json", "--langs", "de", "--out", "locales"],
                cwd=tmp,
            )
            self.assertNotIn("Traceback", plain.stderr, plain.stderr[-800:])
            self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)

            machine = self._run(
                ["check", "--source", "locales/en.json", "--langs", "de",
                 "--out", "locales", "--json"],
                cwd=tmp,
            )
            self.assertNotIn("Traceback", machine.stderr, machine.stderr[-800:])
            self.assertEqual(machine.returncode, 0, machine.stdout + machine.stderr)
            json.loads(machine.stdout)  # must stay machine-readable for CI/agents


if __name__ == "__main__":
    unittest.main()
