# cli/tests/test_detector_parity.py
"""Proves cli/kaeris/detectors.py is a byte-faithful copy of backend/translator.py:
every ported detector must return IDENTICAL output on a shared corpus. Dev-only —
skips when the backend isn't importable (published package / foreign machine)."""
import os, sys, unittest

def _load_backend():
    here = os.path.dirname(__file__)
    backend = os.path.abspath(os.path.join(here, "..", "..", "backend"))
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        import translator  # noqa
        return translator
    except Exception:
        return None

BK = _load_backend()

CORPUS = [  # (original, translated, lang)
    ("Hi %s and %s", "Hola %s", "es"),
    ("Delete 5 files", "Delete 50 files", "ru"),
    ("Total 1,000", "Итого 1.000", "ru"),
    ("Save & exit", "Enregistrer &amp;amp; quitter", "fr"),
    ("{n, plural, one {# item} other {# items}}", "{n, plural, one {# штука}}", "ru"),
    ("{n, plural, one {# item} other {# items}}", "{n, plural, one {# 項目} other {# 項目}}", "ja"),
    ("count 2 things", "عدد ٢ أشياء", "ar"),
    ("Click <b>here</b>", "Cliquez ici", "fr"),
    ("Save changes", "save changes", "ru"),
]

@unittest.skipIf(BK is None, "backend/translator.py not importable — parity skipped")
class TestDetectorParity(unittest.TestCase):
    def test_string_detectors_match_backend(self):
        from kaeris import detectors as d
        for src, tr, lang in CORPUS:
            self.assertEqual(d._placeholder_type_faults(src, tr),
                             BK._placeholder_type_faults(src, tr), (src, tr))
            self.assertEqual(d._numeric_faults(src, tr), BK._numeric_faults(src, tr), (src, tr))
            self.assertEqual(d._entity_faults(src, tr), BK._entity_faults(src, tr), (src, tr))
            self.assertEqual(d._icu_faults(src, tr, lang), BK._icu_faults(src, tr, lang), (src, tr, lang))
            self.assertEqual(d._lost_tags(src, tr), BK._lost_tags(src, tr), (src, tr))
            self.assertEqual(d._untranslated_string(src, tr, lang),
                             BK._untranslated_string(src, tr, lang), (src, tr, lang))

if __name__ == "__main__":
    unittest.main()
