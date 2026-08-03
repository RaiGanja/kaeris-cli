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
    # ICU structure vs text. These are the cases where the two copies silently DIVERGED:
    # the backend learned that an ICU block's argument is the placeholder and its arms are
    # not, while the CLI still read arms flatly — so `kaeris check` invented faults the API
    # no longer reported. Parity has to cover the placeholder detectors to catch that.
    ("{gender, select, male {He} female {She} other {They}} updated it",
     "{gender, select, male {Er} female {Sie} other {Sie}} hat es aktualisiert", "de"),
    ("{count} files translated",
     "{count, plural, one {# файл} few {# файла} many {# файлов} other {# файла}}", "ru"),
    ("{count} errors found",
     "{count, plural, zero{لا أخطاء} one{خطأ واحد} two{خطأان} other{# خطأ}}", "ar"),
    ("{count, plural, one {Hi {name}, # file} other {Hi {name}, # files}}",
     "{count, plural, one {Hola, # archivo} other {Hola, # archivos}}", "es"),
    ("Hi {name}", "Hola", "es"),
    ("Order {0:C} for {{user}}", "Bestellung für", "de"),
    # Glossary collapse (found in production, German, glossary=KAERIS): the model answered
    # with the term instead of translating. Both copies must agree on it and on the cases
    # that merely look like it.
    ("How much does it cost?", "KAERIS", "de"),
    ("What makes KAERIS different?", "Was unterscheidet KAERIS?", "de"),
    ("KAERIS Pro", "KAERIS", "de"),
    # Found live 01.08: a short question survived into Russian byte-for-byte (the model
    # reported 98% confidence), and French answered the question in English instead of
    # translating it. Both copies must agree on these and on the brand phrase that must NOT
    # trip them.
    ("How do I get my API key?", "How do I get my API key?", "ru"),
    ("How do I get my API key?", "Как получить мой API-ключ?", "ru"),
    ("GitHub Actions", "GitHub Actions", "ru"),
    ("Which formats do you support?",
     "You can get your API key by signing up for an account on the provider's website. "
     "After registration you will find it in the developer dashboard.", "fr"),
]

GLOSSARY = ["KAERIS"]

@unittest.skipIf(BK is None, "backend/translator.py not importable — parity skipped")
class TestDetectorParity(unittest.TestCase):
    def test_string_detectors_match_backend(self):
        from kaeris import detectors as d
        for src, tr, lang in CORPUS:
            self.assertEqual(d._find_placeholders(src), BK._find_placeholders(src), src)
            self.assertEqual(d._lost_placeholders(src, tr),
                             BK._lost_placeholders(src, tr), (src, tr))
            self.assertEqual(d._placeholder_type_faults(src, tr),
                             BK._placeholder_type_faults(src, tr), (src, tr))
            self.assertEqual(d._numeric_faults(src, tr), BK._numeric_faults(src, tr), (src, tr))
            self.assertEqual(d._entity_faults(src, tr), BK._entity_faults(src, tr), (src, tr))
            self.assertEqual(d._icu_faults(src, tr, lang), BK._icu_faults(src, tr, lang), (src, tr, lang))
            self.assertEqual(d._lost_tags(src, tr), BK._lost_tags(src, tr), (src, tr))
            self.assertEqual(d._untranslated_string(src, tr, lang),
                             BK._untranslated_string(src, tr, lang), (src, tr, lang))
            self.assertEqual(d._lost_glossary(src, tr, GLOSSARY),
                             BK._lost_glossary(src, tr, GLOSSARY), (src, tr))
            self.assertEqual(d._glossary_collapse(src, tr, GLOSSARY),
                             BK._glossary_collapse(src, tr, GLOSSARY), (src, tr))
            self.assertEqual(d._glossary_case_drift(src, tr, GLOSSARY),
                             BK._glossary_case_drift(src, tr, GLOSSARY), (src, tr))
            self.assertEqual(d._answered_instead_of_translating(src, tr),
                             BK._answered_instead_of_translating(src, tr), (src, tr))

if __name__ == "__main__":
    unittest.main()
