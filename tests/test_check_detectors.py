# cli/tests/test_check_detectors.py
import unittest
from kaeris import check as chk

class TestCheckDetectors(unittest.TestCase):
    def _run(self, source, targets, glossary=None):
        return chk.check_locales(source, list(targets), lambda l: targets[l], glossary)

    def test_red_fails_ok(self):
        res = self._run({"greet": "Hi {name}"}, {"es": {"greet": "Hola"}})
        self.assertFalse(res["ok"])
        self.assertTrue(any(f["severity"] == "error" for f in res["faults"]))

    def test_yellow_does_not_fail_ok(self):
        # same-key untranslated leftover → warning, but keys all present & placeholders fine
        res = self._run({"a": "Save changes"}, {"ru": {"a": "save changes"}})
        self.assertTrue(res["warnings"])
        self.assertTrue(res["ok"])  # warnings never flip ok

    def test_glossary_opt_in(self):
        res = self._run({"a": "Open KAERIS"}, {"ru": {"a": "Открыть"}}, glossary=["KAERIS"])
        self.assertFalse(res["ok"])

if __name__ == "__main__":
    unittest.main()
