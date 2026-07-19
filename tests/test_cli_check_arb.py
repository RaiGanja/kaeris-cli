# cli/tests/test_cli_check_arb.py
import json, os, tempfile, unittest
from kaeris import cli

class TestCliCheckArb(unittest.TestCase):
    def _write(self, d, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def _args(self, t, src, extra=None):
        argv = ["check", "--source", src, "--langs", "es",
                "--pattern", "{lang}.arb", "--out", t, "--json"]
        if extra:
            argv += extra
        return cli.build_parser().parse_args(argv)

    def test_arb_metadata_ignored_and_clean_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.arb")
            tgt = os.path.join(t, "es.arb")
            self._write({"@@locale": "en", "greet": "Hi", "@greet": {"description": "x"}}, src)
            self._write({"@@locale": "es", "greet": "Hola"}, tgt)
            code = cli.cmd_check(self._args(t, src))
            self.assertEqual(code, 0)  # @@locale/@greet must not count as missing/extra

    def test_arb_red_fault_exits_one(self):
        # .arb is accepted at the gate AND detectors fire: a lost {name} placeholder is RED.
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.arb")
            tgt = os.path.join(t, "es.arb")
            self._write({"@@locale": "en", "greet": "Hi {name}"}, src)
            self._write({"@@locale": "es", "greet": "Hola"}, tgt)
            code = cli.cmd_check(self._args(t, src))
            self.assertEqual(code, 1)

if __name__ == "__main__":
    unittest.main()
