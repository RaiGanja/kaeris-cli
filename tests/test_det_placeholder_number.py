import unittest
from kaeris import detectors as d

class TestPlaceholderType(unittest.TestCase):
    def test_arity_drop(self):
        self.assertEqual(
            d._placeholder_type_faults("Hi %s and %s", "Hola %s"),
            ["placeholder %s appears 2× in source but 1× in translation"])
    def test_invented(self):
        self.assertEqual(
            d._placeholder_type_faults("Hi {name}", "Hola {name} {extra}"),
            ["invented placeholder {extra} (not in source)"])
    def test_clean(self):
        self.assertEqual(d._placeholder_type_faults("Hi {name}", "Hola {name}"), [])

class TestNumericDrift(unittest.TestCase):
    def test_changed_number(self):
        self.assertEqual(
            d._numeric_faults("Delete 5 files", "Delete 50 files"),
            ["number 50 appears in the translation but not the source",
             "number 5 from the source is missing or changed in the translation"])
    def test_grouping_not_flagged(self):
        self.assertEqual(d._numeric_faults("Total 1,000", "Итого 1.000"), [])
    def test_spelled_out_not_flagged(self):
        self.assertEqual(d._numeric_faults("5 items", "fünf Elemente"), [])

if __name__ == "__main__":
    unittest.main()
