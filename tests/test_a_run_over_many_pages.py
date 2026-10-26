"""A run over a folder of Markdown pages (client reports 1, 8, 9; замер 0.2 R12-R14).

  1. `"src/content/work/en/**/*.md"` said "No source file(s) found" (exit 1) — `**` was not
     recursive, and with a subfolder present only the subfolder's pages were taken.
  8. --verify wrote verify.json once per page, so only the last page's survived — and it was
     written inside the content collection.
  9. "Wrote 9 files to …/work/en" named the source folder; the files were in work/<lang>/.
"""
import io
import json
import os
import unittest.mock as mock

import pytest

from kaeris import cli as K


class _Server:
    def __init__(self):
        self.jobs = []

    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German", "ja": "Japanese"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.jobs.append((fname, content.decode("utf-8"), langs, kw))
        return str(len(self.jobs) - 1)

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        _, text, langs, _ = self.jobs[int(job)]
        return {f"{l}.md": f"[{l}] {text}".encode() for l in langs}

    def preview(self, job):
        fname, _, langs, kw = self.jobs[int(job)]
        return {"_back": {l: {"md.0001": f"back of {fname}"} for l in langs}} if kw.get("verify") else {}

    def receipt(self, job):
        raise RuntimeError


@pytest.fixture
def site(tmp_path, monkeypatch):
    srv = _Server()
    monkeypatch.setattr(K, "_client", lambda args: srv)
    en = tmp_path / "src" / "content" / "work" / "en"
    (en / "sub").mkdir(parents=True)
    (en / "a.md").write_text("# A\n", encoding="utf-8")
    (en / "sub" / "b.md").write_text("# B\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path, srv


def _run(*argv):
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        code = K.main(["translate", *argv])
    return code, err.getvalue()


def test_double_star_takes_every_level(site):
    tmp, srv = site
    code, _ = _run("src/content/work/en/**/*.md", "--langs", "de")
    assert code == 0
    assert sorted(f for f, *_ in srv.jobs) == ["a.md", "b.md"]
    assert (tmp / "src/content/work/de/a.md").is_file()
    assert (tmp / "src/content/work/de/sub/b.md").is_file()


def test_double_star_without_a_subfolder(site):
    tmp, srv = site
    (tmp / "src/content/work/en/sub/b.md").unlink()
    (tmp / "src/content/work/en/sub").rmdir()
    code, _ = _run("src/content/work/en/**/*.md", "--langs", "de")
    assert code == 0 and [f for f, *_ in srv.jobs] == ["a.md"]


def test_one_verify_file_for_the_whole_run_outside_the_content(site):
    tmp, _ = site
    _run("src/content/work/en/**/*.md", "--langs", "de,ja", "--verify")
    assert not (tmp / "src/content/work/en/verify.json").exists()
    back = json.loads((tmp / "verify.json").read_text("utf-8"))
    assert set(back) == {"src/content/work/en/a.md", "src/content/work/en/sub/b.md"}
    assert back["src/content/work/en/a.md"]["ja"] == {"md.0001": "back of a.md"}


def test_the_wrote_line_names_where_the_files_went(site):
    _, _ = site
    _, err = _run("src/content/work/en/a.md", "--langs", "de,ja")
    wrote = [l for l in err.splitlines() if "Wrote" in l]
    assert wrote and os.path.join("work", "en") not in wrote[0], wrote
