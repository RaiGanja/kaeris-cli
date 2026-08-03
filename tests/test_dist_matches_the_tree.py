# cli/tests/test_dist_matches_the_tree.py
"""A built artifact sitting in dist/ must match the tree it claims to come from.

03.08.2026: the wheel was built, then the README was fixed, then the wheel was uploaded.
PyPI got a README that still advertised "DeepSeek output with GPT-4o-mini" — a model
difference between tiers that stopped existing on 01.08 — while the repository was clean.
A version can never be re-uploaded to PyPI, so that mistake costs a whole release.

`python3 -m build` is the only step that snapshots the tree; anything edited afterwards is
invisible until someone reads the published page. This test makes the gap visible before
the upload instead of after it. It skips when dist/ is empty (a fresh checkout, CI, or a
user who never builds).
"""
import glob
import os
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST = os.path.join(ROOT, "dist")


def _current_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
    return None


class BuiltArtifactsAreCurrent(unittest.TestCase):
    def setUp(self):
        self.wheels = sorted(glob.glob(os.path.join(DIST, "*.whl")))
        if not self.wheels:
            self.skipTest("nothing built — dist/ is empty")

    def test_no_stale_version_is_left_lying_around(self):
        """`twine upload dist/*` uploads everything it finds. An old wheel left in the
        directory is not a leftover, it is a second release."""
        version = _current_version()
        stale = [os.path.basename(w) for w in self.wheels if f"-{version}-" not in w]
        self.assertEqual(
            stale, [],
            f"dist/ holds artifacts from other versions than {version} — `twine upload "
            f"dist/*` would publish them too: {stale}",
        )

    def test_the_readme_inside_the_wheel_is_the_one_in_the_tree(self):
        """The README inside the wheel becomes the PyPI project page. If it lags, every
        visitor reads the old promise until the next release."""
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            on_disk = f.read()
        for wheel in self.wheels:
            with zipfile.ZipFile(wheel) as z:
                meta = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
                self.assertTrue(meta, f"{os.path.basename(wheel)}: no METADATA")
                body = z.read(meta[0]).decode("utf-8")
                # METADATA is headers, a blank line, then the README verbatim.
                packaged = body.split("\n\n", 1)[1] if "\n\n" in body else ""
            self.assertEqual(
                packaged.strip(), on_disk.strip(),
                f"{os.path.basename(wheel)} carries a README that differs from the tree — "
                "rebuild before uploading (a version cannot be re-uploaded to PyPI)",
            )


if __name__ == "__main__":
    unittest.main()
