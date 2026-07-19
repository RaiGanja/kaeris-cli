# cli/tests/test_cli_check_layout.py
"""Regression (2026-07-19 audit #11): `kaeris check` must look for target files where
`kaeris translate` actually writes them. The shipped i18next preset uses a namespace
layout (locales/<lang>/translation.json); check's old default pattern '{lang}.json'
looked for locales/de.json, which never exists, so a correctly-translated project failed
CI with a false 'locale missing'. Without an explicit --pattern, check now resolves
targets through the same _target_path() translate uses."""
import json, os, tempfile, unittest
from kaeris import cli


class TestCliCheckLayout(unittest.TestCase):
    def _write(self, d, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def test_namespace_layout_found_without_explicit_pattern(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "locales", "en", "translation.json")
            tgt = os.path.join(t, "locales", "de", "translation.json")
            self._write({"greet": "Hi"}, src)
            self._write({"greet": "Hallo"}, tgt)
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "de",
                 "--out", os.path.join(t, "locales"), "--json"])
            code = cli.cmd_check(args)
            self.assertEqual(code, 0)  # must FIND locales/de/translation.json

    def test_flat_layout_still_works_without_pattern(self):
        # The common flat case (en.json -> de.json) must be unaffected by the change.
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            tgt = os.path.join(t, "de.json")
            self._write({"greet": "Hi"}, src)
            self._write({"greet": "Hallo"}, tgt)
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "de", "--out", t, "--json"])
            code = cli.cmd_check(args)
            self.assertEqual(code, 0)

    def test_flutter_arb_prefix_layout_found(self):
        # Flutter gen-l10n: lib/l10n/app_en.arb -> lib/l10n/app_de.arb. Before the fix this
        # fell through to lib/l10n/de/app_en.arb and check falsely reported the locale missing.
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "lib", "l10n")
            src = os.path.join(d, "app_en.arb")
            tgt = os.path.join(d, "app_de.arb")
            self._write({"@@locale": "en", "greet": "Hi"}, src)
            self._write({"@@locale": "de", "greet": "Hallo"}, tgt)
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "de", "--out", d, "--json"])
            self.assertEqual(cli.cmd_check(args), 0)   # must find app_de.arb

    def test_explicit_pattern_still_honored(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            tgt = os.path.join(t, "strings.de.json")
            self._write({"greet": "Hi"}, src)
            self._write({"greet": "Hallo"}, tgt)
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "de", "--out", t,
                 "--pattern", "strings.{lang}.json", "--json"])
            code = cli.cmd_check(args)
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
