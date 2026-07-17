# cli/tests/test_det_icu.py
import unittest
from kaeris import detectors as d

class TestICU(unittest.TestCase):
    def test_missing_other_and_ru_forms(self):
        src = "{n, plural, one {# item} other {# items}}"
        tr = "{n, plural, one {# штука}}"
        self.assertEqual(
            d._icu_faults(src, tr, "ru"),
            ["ICU plural is missing the required 'other' branch",
             "ICU plural is missing the few/many form(s) ru requires"])
    def test_dropped_construct(self):
        src = "{n, plural, one {# item} other {# items}}"
        tr = "перевод без плюрала"
        self.assertEqual(
            d._icu_faults(src, tr, "ru"),
            ["ICU plural/select construct dropped or broken (1 in source, 0 in translation)"])
    def test_clean_two_form(self):
        src = "{n, plural, one {# item} other {# items}}"
        tr = "{n, plural, one {# Element} other {# Elemente}}"
        self.assertEqual(d._icu_faults(src, tr, "de"), [])

if __name__ == "__main__":
    unittest.main()
