"""What `--apply-fixes` and `kaeris fix` did is in verify.json, not only in the log.

The GitHub Action used to read the CLI's printed lines to learn which lines were fixed and why
the others were not; a reworded message broke it silently. verify.json now carries
`_applied` and `_skipped` ([{lang, key, how, why}]) and `_summary.by_lang`, and
`kaeris fix --json` prints the same on stdout. The keys verify.json already had do not change.
"""
import io
import json
import unittest.mock as mock

import pytest

from kaeris import cli as K

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


REVIEW = {"de": {
    "save": _entry("Save changes", "Speichern Änderungen", meaning="word order",
                   fix="Änderungen speichern"),
    "greet": _entry("Welcome back, {name}", "Willkommen zurück", fix="Willkommen zurück!",
                    warnings=["{name}"]),
    "hero.title": _entry("We draw maps\nthat *listen*\nto walkers.",
                         "Wir zeichnen Karten,\ndie Wanderern *zuhören*.",
                         warnings=["line breaks changed: 2 in the source, 1 in the translation"]),
    "nav.sales": _entry("Contact sales", "Kontakt", fix="Vertrieb kontaktieren"),
    "nav.contact": _entry("Contact", "Kontakt", meaning="maybe too short"),
}}


def _verify_body():
    return {"de": {"save": "Save changes"}, "_review": REVIEW, "_files": {"de": "de.json"},
            "_summary": {"lines": 9, "to_review": 5, "error": 5, "review": 0, "unchecked": 0}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "locales").mkdir()
    (tmp_path / "locales" / "de.json").write_text(
        json.dumps(DE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (tmp_path / "locales" / "verify.json").write_text(
        json.dumps(_verify_body(), ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(K, "_client", lambda a: (_ for _ in ()).throw(AssertionError("network")))
    return tmp_path


def _main(*argv):
    err, out = io.StringIO(), io.StringIO()
    with mock.patch("sys.stderr", err), mock.patch("sys.stdout", out):
        code = K.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _verify(project):
    return json.loads((project / "locales" / "verify.json").read_text("utf-8"))


def test_kaeris_fix_records_what_it_applied_and_what_it_left(project):
    code, _out, err = _main("fix", "--from", "locales/verify.json")
    assert code == 0, err
    body = _verify(project)
    assert body["_applied"] == [
        {"lang": "de", "key": "save", "how": "fix", "why": "suggested fix"},
        {"lang": "de", "key": "hero.title", "how": "structure", "why": "line breaks / emphasis put back"},
        {"lang": "de", "key": "nav.sales", "how": "fix", "why": "suggested fix"},
    ]
    skipped = {r["key"]: r for r in body["_skipped"]}
    assert set(skipped) == {"greet", "nav.contact"}
    assert all(set(r) == {"lang", "key", "how", "why"} and r["how"] is None for r in body["_skipped"])
    assert "{name}" in skipped["greet"]["why"]
    assert "no fix suggested" in skipped["nav.contact"]["why"]


def test_the_keys_verify_json_already_had_do_not_change(project):
    before = _verify(project)
    _main("fix", "--from", "locales/verify.json")
    after = _verify(project)
    for key, value in before.items():
        assert after[key] == value, key
    assert set(after) - set(before) == {"_applied", "_skipped"}


def test_json_on_stdout_is_the_same_lists_and_only_json(project):
    code, out, err = _main("fix", "--from", "locales/verify.json", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert result["applied"] == _verify(project)["_applied"]
    assert result["skipped"] == _verify(project)["_skipped"]
    assert result["counts"] == {"applied": 3, "already": 0, "skipped": 2}
    assert result["dry_run"] is False
    assert "Applied 3" in err, "the people-readable report still goes to stderr"


def test_a_second_run_keeps_the_lines_that_are_fixed_in_the_list(project):
    _main("fix", "--from", "locales/verify.json")
    first = _verify(project)
    de = (project / "locales" / "de.json").read_text("utf-8")
    code, out, _err = _main("fix", "--from", "locales/verify.json", "--json")
    again = json.loads(out)
    assert code == 0
    assert [(r["lang"], r["key"]) for r in again["applied"]] == [
        (r["lang"], r["key"]) for r in first["_applied"]]
    assert {r["why"] for r in again["applied"]} == {"already in the file"}
    assert again["counts"] == {"applied": 0, "already": 3, "skipped": 2}
    assert (project / "locales" / "de.json").read_text("utf-8") == de


def test_dry_run_leaves_verify_json_alone(project):
    path = project / "locales" / "verify.json"
    before = path.read_bytes()
    code, out, _err = _main("fix", "--from", "locales/verify.json", "--dry-run", "--json")
    assert code == 0
    assert path.read_bytes() == before
    assert json.loads(out)["dry_run"] is True and len(json.loads(out)["applied"]) == 3


def test_a_verify_json_that_lists_nothing_is_not_rewritten_with_noise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = {"_review": {}, "_files": {"de": "de.json"}}
    (tmp_path / "verify.json").write_text(json.dumps(body), encoding="utf-8")
    code, out, _err = _main("fix", "--from", "verify.json", "--json")
    assert code == 0
    assert json.loads(out)["counts"] == {"applied": 0, "already": 0, "skipped": 0}


def test_a_run_over_several_files_records_each_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "de").mkdir()
    (tmp_path / "de" / "a.md").write_text("Notizen vom *Hafen*.\n", encoding="utf-8")
    (tmp_path / "de" / "b.md").write_text("Zweite Seite.\n", encoding="utf-8")
    section = lambda f, key, tr, fix: {"_files": {"de": f}, "_review": {"de": {
        key: _entry("src", tr, fix=fix)}}}
    body = {"docs/a.md": section("de/a.md", "md.0001", "Notizen vom *Hafen*.", "Notizen aus dem *Hafen*."),
            "docs/b.md": section("de/b.md", "md.0001", "Zweite Seite.", "Zweite Seite!")}
    (tmp_path / "verify.json").write_text(json.dumps(body), encoding="utf-8")
    code, out, err = _main("fix", "--from", "verify.json", "--json")
    assert code == 0, err
    written = json.loads((tmp_path / "verify.json").read_text("utf-8"))
    assert [r["key"] for r in written["docs/a.md"]["_applied"]] == ["md.0001"]
    assert set(json.loads(out)["sections"]) == {"docs/a.md", "docs/b.md"}


# ── translate --verify --apply-fixes, and the per-language counters ───────────────

PREVIEW = {
    "_source": {"save": "Save changes", "greet": "Welcome back, {name}"},
    "de": {"save": "Speichern Änderungen", "greet": "Willkommen zurück"},
    "fr": {"save": "Enregistrer", "greet": "Bon retour"},
    "_warnings": {"de": {"greet": ["{name}"]}},
    "_back": {"de": {"save": "Save changes", "greet": "Welcome back"},
              "fr": {"save": "Save", "greet": "Good return"}},
    "_meaning": {"de": {"save": {"verdict": "error", "issue": "word order",
                                 "fix": "Änderungen speichern"}}},
    "_review": {"de": {"error": ["save", "greet"], "review": [], "unchecked": [],
                       "to_review": 2, "lines": 2},
                "fr": {"error": [], "review": [], "unchecked": [], "to_review": 0, "lines": 2}},
}


class _Server:
    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German", "fr": "French"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        return "0"

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        return {f"{l}.json": json.dumps(PREVIEW[l], ensure_ascii=False, indent=2).encode()
                for l in ("de", "fr")}

    def preview(self, job):
        return PREVIEW

    def receipt(self, job):
        raise RuntimeError

    def quota(self):
        raise RuntimeError


@pytest.fixture
def translated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(K, "_client", lambda args: _Server())
    (tmp_path / "en.json").write_text(json.dumps(PREVIEW["_source"]), encoding="utf-8")
    return tmp_path


def test_summary_counts_each_language(translated):
    code, _out, err = _main("translate", "en.json", "--langs", "de,fr", "--verify", "--no-tm")
    assert code == 0, err
    summary = json.loads((translated / "verify.json").read_text("utf-8"))["_summary"]
    assert summary["lines"] == 4 and summary["to_review"] == 2, "the old totals are as they were"
    assert summary["by_lang"] == {
        "de": {"lines": 2, "to_review": 2, "error": 2, "review": 0, "unchecked": 0},
        "fr": {"lines": 2, "to_review": 0, "error": 0, "review": 0, "unchecked": 0}}


def test_apply_fixes_during_translate_writes_the_lists_too(translated):
    code, _out, err = _main("translate", "en.json", "--langs", "de,fr", "--verify", "--no-tm",
                            "--apply-fixes")
    assert code == 0, err
    body = json.loads((translated / "verify.json").read_text("utf-8"))
    assert body["_applied"] == [{"lang": "de", "key": "save", "how": "fix", "why": "suggested fix"}]
    assert [r["key"] for r in body["_skipped"]] == ["greet"]
    assert body["_skipped"][0]["how"] is None
    assert "no fix suggested" in body["_skipped"][0]["why"]
