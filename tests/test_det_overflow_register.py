import unittest
from kaeris import detectors as d

class TestOverflow(unittest.TestCase):
    def test_flags_long_growth(self):
        out = d._compute_overflow({"k": "OK"}, {"k": "D'accord et bien plus long"}, "fr")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["key"], "k")
    def test_expected_german_stretch_ok(self):
        # short + modest expansion under the German norm → not flagged
        out = d._compute_overflow({"k": "Save the file"}, {"k": "Datei speichern"}, "de")
        self.assertEqual(out, [])

class TestRegister(unittest.TestCase):
    def test_mixed_de(self):
        tgt = {"a": "Sie müssen sich anmelden", "b": "Bitte bestätigen Sie",
               "c": "du kannst hier klicken", "d": "wenn du willst"}
        self.assertEqual(len(d._register_faults(tgt, "de")), 1)
    def test_consistent_ok(self):
        tgt = {"a": "Sie müssen sich anmelden", "b": "Bitte bestätigen Sie"}
        self.assertEqual(d._register_faults(tgt, "de"), [])

if __name__ == "__main__":
    unittest.main()
