import ast, os, unittest

ALLOWED = {"re", "collections"}

class TestDetectorsOffline(unittest.TestCase):
    def test_only_stdlib_pure_imports(self):
        path = os.path.join(os.path.dirname(__file__), "..", "kaeris", "detectors.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
        offenders = mods - ALLOWED
        self.assertEqual(offenders, set(), f"detectors.py must stay offline; found: {offenders}")

if __name__ == "__main__":
    unittest.main()
