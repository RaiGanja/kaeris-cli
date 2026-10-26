"""`--apply-fixes` and `kaeris fix`: what --verify found is written into the files, for free.

Before, verify.json listed each line to read and a corrected translation where the meaning
check had one, and the developer copied every fix by hand into every locale file — while a
lost line break stayed lost. A fix is written only if the detectors find nothing in it that
breaks the app (placeholders, tags, ICU, line breaks, *emphasis*) and nothing new; a lost line
break or a torn emphasis with no fix is put back like the server does it. No network: a line
that needs a new translation is named, not bought behind the developer's back.
"""
import io
import json
import unittest.mock as mock

import pytest

from kaeris import cli as K
from kaeris import fixes as F

SRC = {"nav": {"contact": "Contact", "sales": "Contact sales"},
       "hero": {"title": "We draw maps\nthat *listen*\nto walkers."},
       "greet": "Welcome back, {name}",
       "save": "Save changes"}
DE = {"nav": {"contact": "Kontakt", "sales": "Kontakt"},
      "hero": {"title": "Wir zeichnen Karten,\ndie Wanderern *zuhören*."},
      "greet": "Willkommen zurück",
      "save": "Speichern Änderungen"}


def _entry(src, tr, **kw):
    return {"status": "error", "source": src, "translation": tr, **kw}


REVIEW = {
    "de": {
        # The meaning check's fix, and it is clean.
        "save": _entry("Save changes", "Speichern Änderungen", meaning="word order",
                       fix="Änderungen speichern"),
        # A fix that loses the placeholder is not written.
        "greet": _entry("Welcome back, {name}", "Willkommen zurück", fix="Willkommen zurück!",
                        warnings=["{name}"]),
        # No fix, a lost line break: put back without a model.
        "hero.title": _entry("We draw maps\nthat *listen*\nto walkers.",
                             "Wir zeichnen Karten,\ndie Wanderern *zuhören*.",
                             warnings=["line breaks changed: 2 in the source, 1 in the translation"]),
        # The same German text under two keys: only this key changes.
        "nav.sales": _entry("Contact sales", "Kontakt", fix="Vertrieb kontaktieren"),
        # Nothing to apply: named, with what it would take.
        "nav.contact": _entry("Contact", "Kontakt", meaning="maybe too short"),
    }
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "locales").mkdir()
    (tmp_path / "locales" / "en.json").write_text(json.dumps(SRC, indent=2), encoding="utf-8")
    (tmp_path / "locales" / "de.json").write_text(
        json.dumps(DE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    body = {"de": {"save": "Save changes"}, "_review": REVIEW,
            "_files": {"de": "de.json"}}
    (tmp_path / "locales" / "verify.json").write_text(json.dumps(body, ensure_ascii=False),
                                                      encoding="utf-8")

    def no_network(*a, **k):
        raise AssertionError("kaeris fix went to the network")
    monkeypatch.setattr(K, "_client", no_network)
    return tmp_path


def _main(*argv):
    err, out = io.StringIO(), io.StringIO()
    with mock.patch("sys.stderr", err), mock.patch("sys.stdout", out):
        code = K.main(list(argv))
    return code, err.getvalue() + out.getvalue()


def test_kaeris_fix_writes_what_passes_and_names_what_does_not(project):
    code, out = _main("fix", "--from", "locales/verify.json")
    assert code == 0, out
    de = json.loads((project / "locales" / "de.json").read_text("utf-8"))
    assert de["save"] == "Änderungen speichern"
    assert de["hero"]["title"].count("\n") == 2 and "*zuhören*" in de["hero"]["title"]
    assert de["greet"] == "Willkommen zurück", "a fix without {name} reached the file"
    assert de["nav"] == {"contact": "Kontakt", "sales": "Vertrieb kontaktieren"}
    assert "Applied 3" in out, out
    assert "[de] greet" in out and "{name}" in out, "why the fix was refused is not said"
    assert "[de] nav.contact" in out and "quota" in out, "the paid path is not named"


def test_the_file_keeps_its_layout(project):
    before = (project / "locales" / "de.json").read_text("utf-8")
    _main("fix", "--from", "locales/verify.json")
    after = (project / "locales" / "de.json").read_text("utf-8")
    assert len(after.splitlines()) == len(before.splitlines())


def test_a_second_run_changes_nothing(project):
    _main("fix", "--from", "locales/verify.json")
    first = (project / "locales" / "de.json").read_text("utf-8")
    code, out = _main("fix", "--from", "locales/verify.json")
    assert code == 0
    assert (project / "locales" / "de.json").read_text("utf-8") == first
    assert "already" in out


def test_dry_run_writes_nothing(project):
    before = (project / "locales" / "de.json").read_text("utf-8")
    code, out = _main("fix", "--from", "locales/verify.json", "--dry-run")
    assert code == 0 and "Would apply 3" in out, out
    assert (project / "locales" / "de.json").read_text("utf-8") == before


def test_a_markdown_page_is_fixed_in_place(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "de").mkdir()
    page = "# Notizen\n\nNotizen vom *Hafen*.\n\nZweiter Absatz bleibt.\n"
    (tmp_path / "de" / "a.md").write_text(page, encoding="utf-8")
    body = {"_files": {"de": "de/a.md"}, "_review": {"de": {"md.0002": _entry(
        "Notes from the *harbor*.", "Notizen vom *Hafen*.", fix="Notizen aus dem *Hafen*.")}}}
    (tmp_path / "verify.json").write_text(json.dumps(body), encoding="utf-8")
    code, out = _main("fix", "--from", "verify.json")
    assert code == 0, out
    assert (tmp_path / "de" / "a.md").read_text("utf-8") == page.replace(
        "Notizen vom *Hafen*.", "Notizen aus dem *Hafen*.")


def test_an_old_verify_file_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = {"_review": {"de": {"k": {"status": "error", "fix": "x"}}}}
    (tmp_path / "verify.json").write_text(json.dumps(body), encoding="utf-8")
    code, out = _main("fix", "--from", "verify.json")
    assert code == 0
    assert "translate --verify" in out


def test_a_missing_verify_file_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = _main("fix", "--from", "nope.json")
    assert code == 2 and "nope.json" in out


# ── translate --verify --apply-fixes, and what verify.json now carries ────────────

PREVIEW = {
    "_source": {"save": "Save changes", "greet": "Welcome back, {name}"},
    "de": {"save": "Speichern Änderungen", "greet": "Willkommen zurück"},
    "_warnings": {"de": {"greet": ["{name}"]}},
    "_back": {"de": {"save": "Save changes", "greet": "Welcome back"}},
    "_meaning": {"de": {"save": {"verdict": "error", "issue": "word order",
                                 "fix": "Änderungen speichern"}}},
    "_review": {"de": {"error": ["save", "greet"], "review": [], "unchecked": [],
                       "to_review": 2, "lines": 2}},
}


class _Server:
    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        return "0"

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        return {"de.json": json.dumps(PREVIEW["de"], ensure_ascii=False, indent=2).encode()}

    def preview(self, job):
        return PREVIEW

    def receipt(self, job):
        raise RuntimeError

    def quota(self):
        raise RuntimeError


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(K, "_client", lambda args: _Server())
    (tmp_path / "en.json").write_text(json.dumps(PREVIEW["_source"]), encoding="utf-8")
    return tmp_path


def test_verify_json_carries_the_line_and_its_file(run):
    code, out = _main("translate", "en.json", "--langs", "de", "--verify", "--no-tm")
    assert code == 0, out
    body = json.loads((run / "verify.json").read_text("utf-8"))
    line = body["_review"]["de"]["save"]
    assert line["source"] == "Save changes" and line["translation"] == "Speichern Änderungen"
    assert body["_files"] == {"de": "de.json"}
    assert json.loads((run / "de.json").read_text("utf-8"))["save"] == "Speichern Änderungen", \
        "without --apply-fixes the file is left as translated"


def test_apply_fixes_writes_them_after_the_run(run):
    code, out = _main("translate", "en.json", "--langs", "de", "--verify", "--apply-fixes",
                      "--no-tm")
    assert code == 0, out
    de = json.loads((run / "de.json").read_text("utf-8"))
    assert de["save"] == "Änderungen speichern"
    assert de["greet"] == "Willkommen zurück"
    assert "Applied 1" in out, out


def test_apply_fixes_without_verify_says_so(run):
    code, out = _main("translate", "en.json", "--langs", "de", "--apply-fixes", "--no-tm")
    assert code == 0
    assert "--apply-fixes needs --verify" in out


# ── консоль: ни одна строка не пропадает молча ────────────────────────────────────

def test_every_flagged_line_is_counted_in_the_console():
    warnings = {"bn": {f"k{i}": ["line breaks changed: 1 in the source, 0 in the translation"]
                       for i in range(7)}}
    warnings["bn"]["hero.title"] = ["emphasis *…* broken: a stray * the source does not have"]
    data = {"_warnings": warnings}

    class _C:
        def preview(self, job):
            return data
    import types
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        K._show_qa(_C(), "j", types.SimpleNamespace(verify=False, back_lang="en"), "en.json")
    out = err.getvalue()
    assert "[bn] hero.title: emphasis" in out, out


def test_lines_past_the_limit_are_counted_not_dropped():
    warnings = {"bn": {f"k{i:02}": ["{name}"] for i in range(30)}}

    class _C:
        def preview(self, job):
            return {"_warnings": warnings}
    import types
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        K._show_qa(_C(), "j", types.SimpleNamespace(verify=False, back_lang="en"), "en.json")
    out = err.getvalue()
    shown = out.count("[bn] k")
    assert shown < 30
    assert f"{30 - shown} more" in out, out


# ── выбор: что пишется и почему нет ───────────────────────────────────────────────

def test_a_fix_that_tears_the_emphasis_is_refused_and_the_structure_mend_wins():
    entry = _entry("Notes from\nthe *harbor*.", "Märkmed *sadamast*.", fix="Märkmed sadamast*.")
    new, how, why = F.choose(entry, "et")
    assert (new, how) == ("Märkmed\n*sadamast*.", "structure"), (new, how, why)


def test_a_utf16_strings_file_keeps_its_encoding_and_line_endings(tmp_path, monkeypatch):
    # Xcode writes .strings as UTF-16 with \r\n; a fix must not hand back UTF-8 with \n.
    monkeypatch.chdir(tmp_path)
    text = '"save" = "Speichern Änderungen";\r\n"open" = "Öffnen";\r\n'
    (tmp_path / "de.strings").write_bytes(text.encode("utf-16"))
    body = {"_files": {"de": "de.strings"}, "_review": {"de": {"save": _entry(
        "Save changes", "Speichern Änderungen", fix="Änderungen speichern")}}}
    (tmp_path / "verify.json").write_text(json.dumps(body), encoding="utf-8")
    code, out = _main("fix", "--from", "verify.json")
    assert code == 0, out
    raw = (tmp_path / "de.strings").read_bytes()
    assert raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff")
    assert raw.decode("utf-16") == text.replace("Speichern Änderungen", "Änderungen speichern")
