"""`kaeris check` on an Xcode String Catalog.

Every other format the firewall checks is one file per language, so "is the translation there"
means "open <lang>.json". A catalog is one file holding the source AND every translation, so
both sides come out of the same parse and there is nothing to resolve on disk.

Worth having for its own sake: Xcode does not fail a build for a missing translation, and it
says nothing at all when a Russian plural carries only the two forms English had.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli as K            # noqa: E402
from kaeris import xcstrings as xc     # noqa: E402


def unit(v):
    return {"stringUnit": {"state": "translated", "value": v}}


def write(tmp_path, strings, source="en"):
    p = tmp_path / "Localizable.xcstrings"
    p.write_text(json.dumps({"sourceLanguage": source, "version": "1.0", "strings": strings},
                            ensure_ascii=False), encoding="utf-8")
    return str(p)


def run(path, langs, *extra):
    """Through the real argv, not the helpers."""
    r = subprocess.run(
        [sys.executable, "-m", "kaeris", "check", "--source", path, "--langs", langs, *extra],
        capture_output=True, text=True, env={**os.environ, "NO_COLOR": "1"})
    return r.returncode, r.stdout + r.stderr


class TestReadingBothSidesFromOneFile:

    def test_a_complete_catalog_passes(self, tmp_path):
        p = write(tmp_path, {
            "hello": {"localizations": {"en": unit("Hello"), "de": unit("Hallo")}},
            "bye": {"localizations": {"en": unit("Goodbye"), "de": unit("Tschüss")}},
        })
        code, out = run(p, "de")
        assert code == 0, out
        assert "complete" in out

    def test_a_missing_translation_fails_the_build(self, tmp_path):
        p = write(tmp_path, {
            "hello": {"localizations": {"en": unit("Hello"), "de": unit("Hallo")}},
            "bye": {"localizations": {"en": unit("Goodbye")}},
        })
        code, out = run(p, "de")
        assert code == 1, out
        assert "bye" in out and "missing" in out

    def test_a_language_absent_from_the_catalog_is_every_key_missing(self, tmp_path):
        """Adding a language to kaeris.json before translating must say so, not pass."""
        p = write(tmp_path, {"hello": {"localizations": {"en": unit("Hello")}}})
        code, out = run(p, "fr")
        assert code == 1, out
        assert "missing" in out

    def test_a_key_with_no_localization_counts_as_source(self, tmp_path):
        """Xcode's convention for a freshly extracted string. Treating it as absent would
        report a brand-new project as having no source strings at all."""
        p = write(tmp_path, {"Save": {}, "Cancel": {"localizations": {"de": unit("Abbrechen")}}})
        _, by_lang = xc.load(p)
        assert by_lang["en"]["Save"] == "Save"

    def test_untranslatable_entries_are_not_demanded(self, tmp_path):
        p = write(tmp_path, {
            "hello": {"localizations": {"en": unit("Hello"), "de": unit("Hallo")}},
            "debug": {"shouldTranslate": False, "localizations": {"en": unit("DEBUG")}},
        })
        code, out = run(p, "de")
        assert code == 0, out

    def test_a_file_that_is_not_a_catalog_is_refused_clearly(self, tmp_path):
        p = tmp_path / "Localizable.xcstrings"
        p.write_text('{"hello": "world"}')
        code, out = run(str(p), "de")
        assert code == 2, out
        assert "String Catalog" in out


class TestTheChecksThatXcodeDoesNotDo:

    def test_a_lost_placeholder_fails(self, tmp_path):
        p = write(tmp_path, {"greeting": {"localizations": {
            "en": unit("Hello {name}"), "ru": unit("Привет")}}})
        code, out = run(p, "ru")
        assert code == 1, out
        assert "{name}" in out

    def test_a_plural_missing_the_forms_russian_needs_fails(self, tmp_path):
        """The reason this is worth having. Xcode accepts a Russian catalog carrying only
        English's one/other and never says a word; the app then reads wrong to every user
        whose count ends in 2, 3 or 4."""
        p = write(tmp_path, {"%lld items": {"localizations": {
            "en": {"variations": {"plural": {"one": unit("%lld item"),
                                             "other": unit("%lld items")}}},
            "ru": {"variations": {"plural": {"one": unit("%lld элемент"),
                                             "other": unit("%lld элементов")}}}}}})
        code, out = run(p, "ru")
        assert code == 1, out
        assert "few" in out and "many" in out, out

    def test_a_complete_russian_plural_passes(self, tmp_path):
        p = write(tmp_path, {"%lld items": {"localizations": {
            "en": {"variations": {"plural": {"one": unit("%lld item"),
                                             "other": unit("%lld items")}}},
            "ru": {"variations": {"plural": {
                "one": unit("%lld элемент"), "few": unit("%lld элемента"),
                "many": unit("%lld элементов"), "other": unit("%lld элемента")}}}}}})
        code, out = run(p, "ru")
        assert code == 0, out


class TestWhatTheDeveloperReads:

    def test_our_bookkeeping_never_appears_in_the_output(self, tmp_path):
        """Sub-keys are addressed as `%lld items⟦plural⟧`. That encoding is ours; printing it
        raw asks the developer to decode it to find which line of their file is wrong."""
        p = write(tmp_path, {"%lld items": {"localizations": {
            "en": {"variations": {"plural": {"one": unit("%lld item"),
                                             "other": unit("%lld items")}}},
            "ru": {"variations": {"plural": {"one": unit("%lld элемент"),
                                             "other": unit("%lld элементов")}}}}}})
        code, out = run(p, "ru")
        assert "⟦" not in out and "⟧" not in out, out
        assert "%lld items (plural)" in out, out

    @pytest.mark.parametrize("raw,pretty", [
        ("plain", "plain"),
        ("k⟦plural⟧", "k (plural)"),
        ("k⟦device:iphone⟧", "k (device iphone)"),
        ("k⟦sub:count⟧⟦plural⟧", "k (sub count, plural)"),
        ("  k⟦plural⟧: message about k⟦plural⟧", "  k (plural): message about k (plural)"),
    ])
    def test_the_decoder_covers_every_shape(self, raw, pretty):
        assert K._pretty_key(raw) == pretty


class TestItAgreesWithTheServer:
    """If the two parsers drift, `check` and the API disagree about the same file — one says a
    string is missing while the other has just translated it, and the developer cannot tell
    which is lying."""

    CORPUS = {
        "plain": {"localizations": {"en": unit("Hello")}},
        "keyless": {},
        "%lld items": {"localizations": {"en": {"variations": {"plural": {
            "one": unit("%lld item"), "other": unit("%lld items")}}}}},
        "tap": {"localizations": {"en": {"variations": {"device": {
            "iphone": unit("Tap"), "ipad": unit("Click")}}}}},
        "cart": {"localizations": {"en": {
            "stringUnit": {"state": "translated", "value": "%#@n@ inside"},
            "substitutions": {"n": {"argNum": 1, "formatSpecifier": "lld",
                                    "variations": {"plural": {"one": unit("%lld item"),
                                                              "other": unit("%lld items")}}}}}}},
        "skipme": {"shouldTranslate": False, "localizations": {"en": unit("DEBUG")}},
    }

    def test_the_source_side_matches_byte_for_byte(self, tmp_path):
        backend = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "backend")
        if not os.path.isfile(os.path.join(backend, "translator.py")):
            pytest.skip("backend/translator.py not importable — parity runs in the backend CI")
        sys.path.insert(0, backend)
        import translator as t

        p = write(tmp_path, self.CORPUS)
        _, by_lang = xc.load(p)
        theirs = t._parse_xcstrings(open(p, encoding="utf-8").read(), {})
        assert by_lang["en"] == theirs, (
            "the CLI and the API read the same catalog differently:\n"
            f"  only in CLI: {sorted(set(by_lang['en']) - set(theirs))}\n"
            f"  only in API: {sorted(set(theirs) - set(by_lang['en']))}")
