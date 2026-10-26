"""«4 glossary term(s) never appear in your source» — while all four were in it (a customer
run, 26.10.2026, after a job had been cut off by a server restart).

The warning was built only from the receipts of jobs that FINISHED, and a receipt describes
what that job was sent:
  * the file that used the terms was the one whose job died — no receipt, so nothing ever said
    "found", and the other files (which do not use them) said "absent";
  * the re-run with --only-new sent only the missing keys, and none of those used the terms —
    so the receipt truthfully said "not in what you sent me", and the CLI told the customer
    "not in your source".
Absence is now judged against the whole source files of the run, read locally.
"""
import io
import json
import unittest.mock as mock

import pytest

from kaeris import cli as K
from kaeris.client import KaerisError

KEEP = "Northwind,Glassmoor"


class _Server:
    def __init__(self, fail=()):
        self.jobs = []
        self.fail = set(fail)

    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.jobs.append((fname, content.decode("utf-8"), glossary))
        return str(len(self.jobs) - 1)

    def poll(self, job, **kw):
        if self.jobs[int(job)][0] in self.fail:
            raise KaerisError("HTTP 400: something else went wrong")
        return {"failed_langs": []}

    def download(self, job):
        fname, text, _ = self.jobs[int(job)]
        return {"de" + fname[fname.rfind("."):]: text.encode()}

    def preview(self, job):
        return {}

    def receipt(self, job):
        _, text, glossary = self.jobs[int(job)]
        return {"glossary": [{"term": t, "found_in_source": t.lower() in text.lower()}
                             for t in glossary or []]}


def _run(srv, monkeypatch, *argv):
    monkeypatch.setattr(K, "_client", lambda args: srv)
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        K.main(["translate", *argv])
    return [l for l in err.getvalue().splitlines() if "never appear" in l]


def test_a_file_whose_job_failed_still_counts_as_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "a.md").write_text("# Northwind\n\nJoin us on Glassmoor.\n", encoding="utf-8")
    (tmp_path / "en" / "b.md").write_text("# Notes\n\nNothing here.\n", encoding="utf-8")
    lines = _run(_Server(fail={"a.md"}), monkeypatch, "en/*.md", "--langs", "de", "--keep", KEEP)
    assert lines == [], lines


def test_only_new_sends_a_part_but_the_source_is_the_whole_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "en.json").write_text(json.dumps({
        "title": "Welcome to Northwind", "cta": "Join Glassmoor", "new": "Plain new text"}),
        encoding="utf-8")
    (tmp_path / "de.json").write_text(json.dumps({
        "title": "Willkommen bei Northwind", "cta": "Glassmoor beitreten"}), encoding="utf-8")
    srv = _Server()
    lines = _run(srv, monkeypatch, "en.json", "--langs", "de", "--only-new", "--keep", KEEP)
    assert srv.jobs and "Northwind" not in srv.jobs[0][1], "the test needs a part without the terms"
    assert lines == [], lines


def test_a_term_really_missing_is_still_named(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "en.json").write_text(json.dumps({"title": "Welcome to Northwind"}), encoding="utf-8")
    lines = _run(_Server(), monkeypatch, "en.json", "--langs", "de", "--keep", "Northwind,Harborlight")
    assert len(lines) == 1 and "Harborlight" in lines[0] and "Northwind" not in lines[0], lines
