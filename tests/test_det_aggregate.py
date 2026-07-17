import unittest
from kaeris import detectors as d

class TestStringFaults(unittest.TestCase):
    def test_red_placeholder(self):
        out = d.string_faults("Hi {name}", "Hola", "es")
        self.assertTrue(any(f["severity"] == d.ERROR for f in out))
    def test_yellow_only_untranslated(self):
        out = d.string_faults("Save changes", "save changes", "ru")
        self.assertTrue(out and all(f["severity"] == d.WARN for f in out))
    def test_glossary_opt_in(self):
        self.assertEqual(d.string_faults("Open KAERIS", "Открыть", "ru"), [])  # no glossary → skip
        out = d.string_faults("Open KAERIS", "Открыть", "ru", glossary=["KAERIS"])
        self.assertTrue(any("KAERIS" in f["msg"] and f["severity"] == d.ERROR for f in out))

class TestFileFaults(unittest.TestCase):
    def test_register_is_warn(self):
        tgt = {"a": "Sie müssen", "b": "bestätigen Sie", "c": "du kannst", "d": "wenn du"}
        out = d.file_faults(tgt, tgt, "de")
        self.assertTrue(any(f["severity"] == d.WARN for f in out))

if __name__ == "__main__":
    unittest.main()
