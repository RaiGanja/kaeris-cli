"""«N glossary term(s) never appear in your source» — once per run, about the whole run.

A customer re-run (26.10.2026): one JSON file and three Markdown pages with seven --keep
terms. The warning was printed after EVERY file and named terms that the other files did use
— four warnings, every one of them partly false.
"""
import io
import unittest.mock as mock

import pytest

from kaeris import cli as K

TEXTS = {"a.md": "# Northwind\n\nJoin us on Glassmoor.\n", "b.md": "# Northwind\n\nRidgeline page.\n",
         "c.md": "# Notes\n\nNothing here.\n"}
KEEP = "Northwind,Glassmoor,Ridgeline,Harborlight"


class _Server:
    def __init__(self):
        self.jobs = []

    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.jobs.append((fname, content.decode("utf-8"), glossary))
        return str(len(self.jobs) - 1)

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        _, text, _ = self.jobs[int(job)]
        return {"de.md": text.encode()}

    def preview(self, job):
        return {}

    def receipt(self, job):
        _, text, glossary = self.jobs[int(job)]
        return {"glossary": [{"term": t, "found_in_source": t in text} for t in glossary or []]}


@pytest.fixture
def site(tmp_path, monkeypatch):
    srv = _Server()
    monkeypatch.setattr(K, "_client", lambda args: srv)
    en = tmp_path / "en"
    en.mkdir()
    for name, text in TEXTS.items():
        (en / name).write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return srv


def _run(*argv):
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        code = K.main(["translate", *argv])
    return code, err.getvalue()


def test_one_warning_naming_only_what_no_file_uses(site):
    code, out = _run("en/*.md", "--langs", "de", "--keep", KEEP)
    assert code == 0
    lines = [l for l in out.splitlines() if "never appear" in l]
    assert len(lines) == 1, out
    assert "Harborlight" in lines[0]
    for used in ("Northwind", "Glassmoor", "Ridgeline"):
        assert used not in lines[0], lines[0]


def test_nothing_said_when_every_term_is_used_somewhere(site):
    _, out = _run("en/*.md", "--langs", "de", "--keep", "Northwind,Glassmoor,Ridgeline")
    assert "never appear" not in out
