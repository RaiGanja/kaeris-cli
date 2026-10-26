"""A run over several files waited for each one before sending the next (client report, 0.2.31).

Ember: en.json + 3 devlog pages, 6 languages, --verify: 23-26 minutes. Measured 26.10.2026 on a
local server, same input before the fix (en.json 84 strings + 2 pages, fa/bn/ja, --verify):
558 s, of which the pages 77 s and 114 s were spent queued behind each other and the JSON. The
server already runs the jobs side by side; the CLI never gave it more than one.

`--jobs N` (default 4) keeps up to N jobs in flight. What the person reads — the written files,
QA, receipts, verify.json — still comes out file by file, in order. `--only-new` sends one job
per language; those go side by side too.
"""
import io
import json
import threading
import time
import unittest.mock as mock

import pytest

from kaeris import cli as K
from kaeris import tm as tmem


class _Server:
    """Answers like the API; poll() takes a while so overlapping jobs can be seen."""

    def __init__(self, delay=0.3):
        self.delay = delay
        self.jobs = []
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0

    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German", "ja": "Japanese", "fr": "French"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        with self.lock:
            self.jobs.append((fname, content.decode("utf-8"), list(langs), kw))
            return str(len(self.jobs) - 1)

    def poll(self, job, **kw):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(self.delay)
        with self.lock:
            self.in_flight -= 1
        return {"failed_langs": []}

    def download(self, job):
        fname, text, langs, _ = self.jobs[int(job)]
        if fname.endswith(".json"):
            src = json.loads(text)
            return {f"{l}.json": json.dumps({k: f"[{l}] {v}" for k, v in src.items()}).encode()
                    for l in langs}
        return {f"{l}.md": f"[{l}] {text}".encode() for l in langs}

    def preview(self, job):
        fname, text, langs, _ = self.jobs[int(job)]
        if fname.endswith(".json"):
            src = json.loads(text)
            return {l: {k: f"[{l}] {v}" for k, v in src.items()} for l in langs}
        return {}

    def receipt(self, job):
        raise RuntimeError


@pytest.fixture
def site(tmp_path, monkeypatch):
    srv = _Server()
    monkeypatch.setattr(K, "_client", lambda args: srv)
    pages = tmp_path / "devlog" / "en"
    pages.mkdir(parents=True)
    for name in ("a.md", "b.md", "c.md"):
        (pages / name).write_text(f"# {name}\n", encoding="utf-8")
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "en.json").write_text('{"hi": "Hello", "bye": "Bye"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path, srv


def _run(*argv):
    err, out = io.StringIO(), io.StringIO()
    with mock.patch("sys.stderr", err), mock.patch("sys.stdout", out):
        code = K.main(["translate", *argv])
    return code, err.getvalue(), out.getvalue()


def test_files_are_translated_side_by_side(site):
    tmp, srv = site
    started = time.time()
    code, err, _ = _run("devlog/en/*.md", "i18n/en.json", "--langs", "de,ja")
    assert code == 0, err
    assert srv.peak == 4, f"jobs in flight at once: {srv.peak}"
    # Four jobs of 0.3 s each, overlapped — not 1.2 s back to back.
    assert time.time() - started < 1.0
    for lang in ("de", "ja"):
        for name in ("a.md", "b.md", "c.md"):
            assert (tmp / "devlog" / lang / name).read_text("utf-8").startswith(f"[{lang}]")
        assert (tmp / "i18n" / f"{lang}.json").is_file()


def test_the_report_still_reads_file_by_file(site):
    _, _ = site
    _, err, out = _run("devlog/en/*.md", "--langs", "de")
    heads = [l for l in err.splitlines() if l.startswith("→ ──")]
    assert [h.split("──")[1].strip().replace("\\", "/") for h in heads] == \
        ["devlog/en/a.md", "devlog/en/b.md", "devlog/en/c.md"]
    written = [l.replace("\\", "/") for l in out.splitlines()]
    assert [w.rsplit("/", 1)[1] for w in written] == ["a.md", "b.md", "c.md"]


def test_jobs_one_is_the_old_one_at_a_time(site):
    _, srv = site
    code, err, _ = _run("devlog/en/*.md", "--langs", "de", "--jobs", "1")
    assert code == 0, err
    assert srv.peak == 1


def test_jobs_comes_from_the_config_too(site):
    tmp, srv = site
    (tmp / "kaeris.json").write_text(json.dumps({"jobs": 2}), encoding="utf-8")
    _run("devlog/en/*.md", "--langs", "de")
    assert srv.peak == 2


def test_files_sharing_one_memory_keep_each_others_strings(site):
    tmp, srv = site
    (tmp / "i18n" / "other.json").write_text('{"x": "Thanks"}', encoding="utf-8")
    code, err, _ = _run("i18n/en.json", "i18n/other.json", "--langs", "de", "--tm", "tm.json")
    assert code == 0, err
    memory = tmem.load(str(tmp / "tm.json"))
    strings, _pairs = tmem.count(memory)
    assert strings == 3, "a file finishing later must not wipe what the other one recorded"


def test_only_new_sends_the_languages_side_by_side(site):
    tmp, srv = site
    code, err, _ = _run("i18n/en.json", "--langs", "de,ja,fr", "--only-new")
    assert code == 0, err
    assert srv.peak == 3
    assert sorted(l for _, _, langs, _ in srv.jobs for l in langs) == ["de", "fr", "ja"]
    for lang in ("de", "ja", "fr"):
        assert json.loads((tmp / "i18n" / f"{lang}.json").read_text("utf-8"))["hi"] == f"[{lang}] Hello"
    merged = [l for l in err.splitlines() if "merged" in l]
    assert [m.split(":")[0][-2:] for m in merged] == ["de", "ja", "fr"], "reported in order"


def test_only_new_one_language_failing_does_not_lose_the_others(site, monkeypatch):
    tmp, srv = site
    real = srv.download

    def download(job):
        if srv.jobs[int(job)][2] == ["ja"]:
            raise K.KaerisError("HTTP 500: boom")
        return real(job)

    monkeypatch.setattr(srv, "download", download)
    code, err, _ = _run("i18n/en.json", "--langs", "de,ja,fr", "--only-new")
    assert code == 1
    assert "boom" in err
    assert (tmp / "i18n" / "de.json").is_file() and (tmp / "i18n" / "fr.json").is_file()
    assert not (tmp / "i18n" / "ja.json").exists()
    # ja was not merged, so the next run must still see it as pending.
    lock = json.loads((tmp / "i18n" / "kaeris.lock").read_text("utf-8"))
    assert "hi" not in (lock.get("langs", {}).get("ja") or {})
