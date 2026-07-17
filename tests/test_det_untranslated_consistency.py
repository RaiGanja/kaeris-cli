import unittest
from kaeris import detectors as d

class TestUntranslated(unittest.TestCase):
    def test_leftover_source_ru(self):
        # target is Russian but value carries no Cyrillic + a real lowercase word
        self.assertEqual(
            d._untranslated_string("Save changes", "save changes", "ru"),
            ["may be untranslated — no target-language script, still reads as source text"])
    def test_real_translation_ok(self):
        self.assertEqual(d._untranslated_string("Save changes", "Сохранить изменения", "ru"), [])

class TestConsistency(unittest.TestCase):
    def test_term_drift(self):
        src = {"btn.a": "Cancel", "btn.b": "Cancel"}
        tgt = {"btn.a": "Отмена", "btn.b": "Отменить"}
        out = d._compute_consistency(src, tgt)
        self.assertEqual(len(out), 1)
    def test_consistent_ok(self):
        src = {"btn.a": "Cancel", "btn.b": "Cancel"}
        tgt = {"btn.a": "Отмена", "btn.b": "Отмена"}
        self.assertEqual(d._compute_consistency(src, tgt), [])

if __name__ == "__main__":
    unittest.main()
