import unittest
from kaeris import detectors as d

class TestGlossary(unittest.TestCase):
    def test_dropped_term(self):
        self.assertEqual(
            d._lost_glossary("Open KAERIS now", "Открыть сейчас", ["KAERIS"]),
            ["KAERIS"])
    def test_term_survived(self):
        self.assertEqual(
            d._lost_glossary("Open KAERIS now", "Открыть KAERIS сейчас", ["KAERIS"]), [])
    def test_no_glossary(self):
        self.assertEqual(d._lost_glossary("Open KAERIS", "Открыть", None), [])

if __name__ == "__main__":
    unittest.main()
