"""`kaeris check --sarif` — findings that land in the pull request, on the line at fault.

A JSON report is something a developer has to go and read. SARIF is something GitHub shows
them: upload it in a workflow and every fault appears as an annotation on the exact line of
the locale file, in the diff, next to the human reviewer's comments. That is the difference
between a tool you run and a tool that is part of the review.

The hard part is not the format — it is the line number. check_locales works in keys
("greet"), and an annotation without a line is useless, so every finding is resolved back to
the line where that key sits in the file it belongs to.
"""
import json
import os
import tempfile
import unittest

from kaeris import cli
from kaeris import sarif


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


class TestLineLookup(unittest.TestCase):
    def test_finds_the_line_of_a_flat_key(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "es.json")
            _write(p, {"a": "1", "greet": "Hola", "z": "9"})
            # indent=2 → {, "a", "greet", "z", }
            self.assertEqual(sarif.line_of_key(p, "greet"), 3)

    def test_finds_a_nested_key_by_its_last_segment(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "es.json")
            _write(p, {"menu": {"file": {"save": "Guardar"}}})
            self.assertEqual(sarif.line_of_key(p, "menu.file.save"), 4)

    def test_missing_key_falls_back_to_line_one(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "es.json")
            _write(p, {"a": "1"})
            self.assertEqual(sarif.line_of_key(p, "nope"), 1)

    def test_a_key_that_also_appears_as_a_value_is_not_confused(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "es.json")
            _write(p, {"first": "save", "save": "Guardar"})
            self.assertEqual(sarif.line_of_key(p, "save"), 3)


class TestSarifDocument(unittest.TestCase):
    def _doc(self):
        result = {
            "ok": False,
            "missing": {"es": ["bye"]},
            "extra": {},
            "placeholder_issues": [],
            "missing_files": [],
            "faults": [{"lang": "es", "key": "greet", "msg": "lost placeholder {name}",
                        "severity": "error"}],
            "warnings": [{"lang": "es", "key": "save", "msg": "may be untranslated"}],
        }
        return result

    def test_shape_is_valid_sarif(self):
        with tempfile.TemporaryDirectory() as t:
            _write(os.path.join(t, "es.json"), {"greet": "Hola", "save": "Save changes"})
            doc = sarif.build(self._doc(), source="en.json",
                              target_for=lambda lang: os.path.join(t, f"{lang}.json"), root=t)
        self.assertEqual(doc["version"], "2.1.0")
        self.assertIn("$schema", doc)
        run = doc["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "KAERIS")
        self.assertTrue(run["tool"]["driver"]["rules"], "rules describe what each finding means")

    def test_a_fault_becomes_an_error_on_its_own_line(self):
        with tempfile.TemporaryDirectory() as t:
            _write(os.path.join(t, "es.json"), {"greet": "Hola", "save": "Save changes"})
            doc = sarif.build(self._doc(), source="en.json",
                              target_for=lambda lang: os.path.join(t, f"{lang}.json"), root=t)
        res = [r for r in doc["runs"][0]["results"] if "placeholder" in r["message"]["text"]]
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual(r["level"], "error")
        loc = r["locations"][0]["physicalLocation"]
        self.assertEqual(loc["artifactLocation"]["uri"], "es.json")
        self.assertEqual(loc["region"]["startLine"], 2)      # "greet" on line 2

    def test_a_warning_stays_a_warning(self):
        with tempfile.TemporaryDirectory() as t:
            _write(os.path.join(t, "es.json"), {"greet": "Hola", "save": "Save changes"})
            doc = sarif.build(self._doc(), source="en.json",
                              target_for=lambda lang: os.path.join(t, f"{lang}.json"), root=t)
        res = [r for r in doc["runs"][0]["results"] if "untranslated" in r["message"]["text"]]
        self.assertEqual(res[0]["level"], "warning")

    def test_a_missing_key_is_reported_against_the_target_file(self):
        with tempfile.TemporaryDirectory() as t:
            _write(os.path.join(t, "es.json"), {"greet": "Hola"})
            doc = sarif.build(self._doc(), source="en.json",
                              target_for=lambda lang: os.path.join(t, f"{lang}.json"), root=t)
        res = [r for r in doc["runs"][0]["results"] if "bye" in r["message"]["text"]]
        self.assertEqual(len(res), 1, "a missing key must be reported, it is the commonest fault")
        self.assertEqual(res[0]["level"], "error")

    def test_paths_are_relative_to_the_repo_root(self):
        """GitHub matches annotations to the diff by repo-relative path — absolute paths
        silently annotate nothing."""
        with tempfile.TemporaryDirectory() as t:
            _write(os.path.join(t, "locales", "es.json"), {"greet": "Hola"})
            doc = sarif.build(self._doc(), source="locales/en.json",
                              target_for=lambda lang: os.path.join(t, "locales", f"{lang}.json"),
                              root=t)
        for r in doc["runs"][0]["results"]:
            uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            self.assertFalse(uri.startswith("/"), uri)
            self.assertFalse(uri.startswith(t), uri)


class TestCliFlag(unittest.TestCase):
    def test_check_writes_the_file_and_still_reports_its_verdict(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            _write(src, {"greet": "Hi {name}"})
            _write(os.path.join(t, "es.json"), {"greet": "Hola"})
            out = os.path.join(t, "kaeris.sarif")
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "es", "--out", t, "--sarif", out, "--json"])
            code = cli.cmd_check(args)
            self.assertEqual(code, 1, "a lost placeholder must still fail the build")
            with open(out, encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["version"], "2.1.0")
            self.assertTrue(doc["runs"][0]["results"])

    def test_a_clean_run_writes_an_empty_report_not_nothing(self):
        """An absent file makes upload-sarif fail the workflow; a clean run must still
        produce a valid document with zero results."""
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            _write(src, {"greet": "Hi"})
            _write(os.path.join(t, "es.json"), {"greet": "Hola"})
            out = os.path.join(t, "kaeris.sarif")
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "es", "--out", t, "--sarif", out, "--json"])
            self.assertEqual(cli.cmd_check(args), 0)
            with open(out, encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["runs"][0]["results"], [])


if __name__ == "__main__":
    unittest.main()
