import unittest
from kaeris import detectors as d

class TestEntity(unittest.TestCase):
    def test_double_encoded(self):
        self.assertEqual(
            d._entity_faults("Save & exit", "Enregistrer &amp;amp; quitter"),
            ["double-encoded entity &amp;amp; — the & was re-escaped (renders literally)"])
    def test_broken_unicode(self):
        self.assertEqual(
            d._entity_faults("Hello", "\\uZZZZ Bonjour"),
            ["broken \\u escape — a unicode escape was mangled to non-hex"])
    def test_clean(self):
        self.assertEqual(d._entity_faults("Save & exit", "Enregistrer & quitter"), [])

class TestTags(unittest.TestCase):
    def test_lost_bold(self):
        self.assertEqual(d._lost_tags("Click <b>here</b>", "Cliquez ici"),
                         ["</b>", "<b>"])
    def test_clean(self):
        self.assertEqual(d._lost_tags("Click <b>here</b>", "Cliquez <b>ici</b>"), [])

if __name__ == "__main__":
    unittest.main()
