"""Project translation memory.

`kaeris.lock` remembers a KEY and the hash of the English behind it. That catches an edited
string and nothing else. Rename `nav.save` to `toolbar.save` and the identical sentence is
translated and charged again. The same "Save changes" in two files is paid for twice. A
colleague who has just cloned the repository re-translates everything you already bought.

This remembers the TEXT, in one committed file, so reuse survives renames, moves and clones.
The map goes to the API as its `reuse` parameter — a path that already exists and already
does not charge for what it did not translate.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli as K       # noqa: E402
from kaeris import tm             # noqa: E402
from kaeris import incremental as inc   # noqa: E402

SIG = inc.settings_signature(tone="", icu=False, keep=[], model="m1", app_context="")
OTHER = inc.settings_signature(tone="formal", icu=False, keep=[], model="m1", app_context="")


def memory_with(**pairs):
    m = {"version": tm.VERSION, "entries": {}}
    tm.record(m, {k: k for k in pairs}, {"de": {k: v for k, v in pairs.items()}}, SIG)
    return m


class TestItRemembersTextNotKeys:

    def test_a_renamed_key_is_not_paid_for_twice(self):
        """The whole point. The lock keys off `nav.save`; rename it and the identical English
        looks brand new."""
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        hits = tm.lookup(m, {"toolbar.save": "Save changes"}, ["de"], SIG)
        assert hits == {"de": {"toolbar.save": "Änderungen speichern"}}

    def test_the_same_string_in_another_file_is_free(self):
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        hits = tm.lookup(m, {"checkout.confirm.save": "Save changes"}, ["de"], SIG)
        assert hits["de"]

    def test_a_string_it_has_never_seen_is_not_invented(self):
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        assert tm.lookup(m, {"a": "Delete account"}, ["de"], SIG) == {}

    def test_a_language_it_has_never_seen_is_not_invented(self):
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        assert tm.lookup(m, {"a": "Save changes"}, ["ja"], SIG) == {}


class TestSettingsArePartOfTheAnswer:
    """A string translated formally is not the same answer as one translated casually, and a
    glossary or model change means the old text was produced under rules that no longer apply."""

    def test_a_different_tone_does_not_reuse(self):
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        assert tm.lookup(m, {"a": "Save changes"}, ["de"], OTHER) == {}

    def test_a_different_model_does_not_reuse(self):
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        other_model = inc.settings_signature(tone="", icu=False, keep=[], model="m2",
                                             app_context="")
        assert tm.lookup(m, {"a": "Save changes"}, ["de"], other_model) == {}

    def test_the_signature_key_is_stable_across_runs(self):
        """settings_signature returns a dict, and a dict has no defined text form. Getting
        this wrong would not fail loudly — the memory would just stop matching itself."""
        a = tm.signature_key({"tone": "", "keep": ["b", "a"], "icu": False})
        b = tm.signature_key({"icu": False, "keep": ["b", "a"], "tone": ""})
        assert a == b, "the same settings hashed to two different keys"


class TestWhatItRefusesToRemember:

    def test_a_translation_identical_to_its_source_is_not_stored(self):
        """That is exactly what a language which fell back to the original looks like. Storing
        it would teach the memory to serve the untranslated text forever — the trap the browser
        memory fell into on 02.08.2026."""
        m = {"version": tm.VERSION, "entries": {}}
        added = tm.record(m, {"a": "Save changes"}, {"de": {"a": "Save changes"}}, SIG)
        assert added == 0
        assert tm.lookup(m, {"a": "Save changes"}, ["de"], SIG) == {}

    def test_an_empty_translation_is_not_stored(self):
        m = {"version": tm.VERSION, "entries": {}}
        assert tm.record(m, {"a": "Save"}, {"de": {"a": "   "}}, SIG) == 0

    def test_recording_the_same_pair_twice_adds_nothing(self):
        m = memory_with(**{"Save": "Speichern"})
        again = tm.record(m, {"x": "Save"}, {"de": {"x": "Speichern"}}, SIG)
        assert again == 0, "the counter reports work that did not happen"

    def test_a_corrected_translation_replaces_the_old_one(self):
        m = memory_with(**{"Save": "Speichern"})
        tm.record(m, {"x": "Save"}, {"de": {"x": "Sichern"}}, SIG)
        assert tm.lookup(m, {"x": "Save"}, ["de"], SIG)["de"]["x"] == "Sichern"


class TestTheFileItself:

    def test_a_damaged_file_does_not_block_a_translation(self, tmp_path):
        """It is a cache. A run must never fail because of one."""
        p = tmp_path / tm.FILENAME
        p.write_text("{ this is not json")
        assert tm.load(str(p)) == {"version": tm.VERSION, "entries": {}}

    def test_a_missing_file_is_simply_empty(self, tmp_path):
        assert tm.load(str(tmp_path / "nope.json"))["entries"] == {}

    def test_it_round_trips_and_stays_readable_in_a_diff(self, tmp_path):
        p = str(tmp_path / tm.FILENAME)
        m = memory_with(**{"Save changes": "Änderungen speichern"})
        tm.save(p, m, lambda path, body: open(path, "w", encoding="utf-8").write(body))
        raw = open(p, encoding="utf-8").read()
        assert "Änderungen speichern" in raw, "the file is unreadable to a human reviewing it"
        assert '"source": "Save changes"' in raw, "no way to tell what an entry is for"
        assert tm.lookup(tm.load(p), {"k": "Save changes"}, ["de"], SIG)["de"]["k"]

    def test_it_lives_next_to_the_source_by_default(self, tmp_path):
        src = tmp_path / "locales" / "en.json"
        src.parent.mkdir()
        src.write_text("{}")
        assert tm.default_path(str(src)) == str(src.parent / tm.FILENAME)
        assert tm.default_path(str(src), "/tmp/custom.json") == "/tmp/custom.json"

    def test_counting_reports_strings_and_pairs_separately(self):
        m = {"version": tm.VERSION, "entries": {}}
        tm.record(m, {"a": "Save", "b": "Delete"},
                  {"de": {"a": "Speichern", "b": "Löschen"},
                   "fr": {"a": "Enregistrer"}}, SIG)
        assert tm.count(m) == (2, 3)


class TestItIsWiredIntoTheRun:
    """A module nobody calls is a module that does nothing."""

    def test_the_flags_exist_and_are_documented(self):
        import re
        from pathlib import Path
        parser = K.build_parser()
        text = str(parser.format_help())
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        for flag in ("--tm", "--no-tm"):
            assert flag in readme, f"{flag} is not documented"
        assert re.search(r"kaeris-tm\.json", readme), "the file's name is nowhere in the README"

    def test_a_run_hands_the_memory_to_the_api_as_reuse(self, tmp_path, monkeypatch):
        """Through the real translate path: built, passed as `reuse`, and folded back after."""
        src = tmp_path / "en.json"
        src.write_text(json.dumps({"greeting": "Save changes"}), encoding="utf-8")
        tm_path = tmp_path / tm.FILENAME
        tm.save(str(tm_path), memory_with(**{"Save changes": "Änderungen speichern"}),
                lambda p, b: open(p, "w", encoding="utf-8").write(b))

        seen = {}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def key_info(self):
                return {"model_id": "m1"}

            def submit(self, fname, content, langs, glossary=None, **kw):
                seen["reuse"] = kw.get("reuse")
                return "job-1"

            def poll(self, job, **kw):
                return {"failed_langs": []}

            def download(self, job):
                return {"de.json": json.dumps({"greeting": "Änderungen speichern"}).encode()}

            def preview(self, job):
                return {"_source": {"greeting": "Save changes"},
                        "de": {"greeting": "Änderungen speichern"}}

            def receipt(self, job):
                raise RuntimeError("no receipt in this stand")

        monkeypatch.setattr(K, "_client", lambda args: FakeClient())
        monkeypatch.setattr(K, "KaerisClient", FakeClient)
        code = K.main(["translate", str(src), "--langs", "de", "--out", str(tmp_path)])
        assert code == 0
        assert seen["reuse"] == {"de": {"greeting": "Änderungen speichern"}}, (
            "the project memory never reached the API, so it was paid for again")

    def test_no_tm_switches_it_off_completely(self, tmp_path, monkeypatch):
        src = tmp_path / "en.json"
        src.write_text(json.dumps({"greeting": "Save changes"}), encoding="utf-8")
        tm_path = tmp_path / tm.FILENAME
        tm.save(str(tm_path), memory_with(**{"Save changes": "Änderungen speichern"}),
                lambda p, b: open(p, "w", encoding="utf-8").write(b))
        before = tm_path.read_text(encoding="utf-8")
        seen = {}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def key_info(self):
                return {"model_id": "m1"}

            def submit(self, fname, content, langs, glossary=None, **kw):
                seen["reuse"] = kw.get("reuse")
                return "job-1"

            def poll(self, job, **kw):
                return {"failed_langs": []}

            def download(self, job):
                return {"de.json": b"{}"}

            def preview(self, job):
                return {"_source": {"greeting": "Save changes"}, "de": {"greeting": "Neu"}}

            def receipt(self, job):
                raise RuntimeError("none")

        monkeypatch.setattr(K, "_client", lambda args: FakeClient())
        monkeypatch.setattr(K, "KaerisClient", FakeClient)
        K.main(["translate", str(src), "--langs", "de", "--out", str(tmp_path), "--no-tm"])
        assert seen["reuse"] is None, "--no-tm still sent the memory"
        assert tm_path.read_text(encoding="utf-8") == before, "--no-tm still wrote the memory"


class TestTheIncrementalPathUsesItToo:
    """`--only-new` needs it MORE than a plain run: the lock already skips unchanged keys, so
    what is left to pay for is exactly the renames and moves the lock cannot see."""

    def _stand(self, tmp_path, monkeypatch, seen):
        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def key_info(self):
                return {"model_id": "m1"}

            def submit(self, fname, content, langs, glossary=None, **kw):
                seen.setdefault("reuse", []).append(kw.get("reuse"))
                seen.setdefault("sent", []).append(json.loads(content.decode()))
                return "job-1"

            def poll(self, job, **kw):
                return {"failed_langs": []}

            def download(self, job):
                return {"de.json": json.dumps({"toolbar.save": "Speichern"}).encode()}

            def preview(self, job):
                return {}

            def receipt(self, job):
                raise RuntimeError("none")

        monkeypatch.setattr(K, "_client", lambda args: FakeClient())
        monkeypatch.setattr(K, "KaerisClient", FakeClient)

    def test_a_renamed_key_is_served_from_memory(self, tmp_path, monkeypatch):
        src = tmp_path / "en.json"
        src.write_text(json.dumps({"toolbar.save": "Save changes"}), encoding="utf-8")
        tm.save(str(tmp_path / tm.FILENAME), memory_with(**{"Save changes": "Speichern"}),
                lambda p, b: open(p, "w", encoding="utf-8").write(b))
        seen = {}
        self._stand(tmp_path, monkeypatch, seen)
        K.main(["translate", str(src), "--langs", "de", "--out", str(tmp_path), "--only-new"])
        assert seen["reuse"][0] == {"de": {"toolbar.save": "Speichern"}}, (
            "--only-new never offered the project memory, so the rename was paid for again")


class TestWhereItDeliberatelyDoesNotApply:

    def test_formats_the_cli_cannot_flatten_are_left_alone(self, tmp_path, monkeypatch):
        """Looking a string UP needs the source flattened locally, and this package can only
        flatten JSON and ARB. Recording for the others would grow a file that is written and
        never read — worse than none, because it looks like a feature."""
        class Args:
            no_tm = False
            tm = None
        for name in ("messages.po", "Localizable.strings", "strings.xml", "guide.md"):
            mem, path = K._tm_open(Args(), str(tmp_path / name))
            assert mem is None and path is None, f"{name} got a memory it can never use"
        mem, path = K._tm_open(Args(), str(tmp_path / "en.json"))
        assert mem is not None and path
