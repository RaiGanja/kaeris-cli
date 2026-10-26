"""The npm package (`npm/`) is a port of `kaeris check`, and its tests replay answers this package
gave. Those answers are files in the repository; if the Python code changes and they do not, the
port is being measured against something that is no longer true.

  npm/test/baseline, corpus, pairs.json   what `kaeris check` and the detectors answer
  npm/src/tables.js                        the patterns and word lists of detectors.py

Regenerate with `python npm/test/gen_baselines.py --write` and `python npm/tools/gen_tables.py`.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kaeris import __version__  # noqa: E402


def _run(script, *args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, *script), *args], capture_output=True,
                       text=True, encoding="utf-8", cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


def test_the_npm_package_has_the_version_of_the_python_one():
    with open(os.path.join(ROOT, "npm", "package.json"), encoding="utf-8") as f:
        assert json.load(f)["version"] == __version__
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        assert re.search(r'^version = "%s"$' % re.escape(__version__), f.read(), re.M)


def test_the_generated_tables_are_those_of_detectors_py():
    code, out = _run(("npm", "tools", "gen_tables.py"), "--check")
    assert code == 0, "npm/src/tables.js is out of date: run python npm/tools/gen_tables.py\n" + out


def test_the_recorded_answers_are_those_of_this_code():
    # The recorded JSON errors are worded by Python 3.13+ ("Illegal trailing comma ..."); an older
    # interpreter words two of them differently, so the comparison is made where the answers were
    # written. The node suite checks the port against the same files on every Node version.
    if sys.version_info < (3, 13):
        return
    code, out = _run(("npm", "test", "gen_baselines.py"), "--check")
    assert code == 0, "npm/test is out of date: run python npm/test/gen_baselines.py --write\n" + out
