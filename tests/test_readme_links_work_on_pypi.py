"""The README is the PyPI page: a relative link there points at pypi.org, not at the repo."""
import os
import re
import unittest

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")


class TestReadmeLinks(unittest.TestCase):
    def setUp(self):
        with open(README, encoding="utf-8") as f:
            self.text = f.read()

    def test_no_relative_links(self):
        relative = re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", self.text)
        self.assertEqual(relative, [])

    def test_ci_examples_use_the_action_repo(self):
        self.assertNotIn("kaeris-cli/.github/actions", self.text)
        self.assertIn("RaiGanja/kaeris-action@v1", self.text)


if __name__ == "__main__":
    unittest.main()
