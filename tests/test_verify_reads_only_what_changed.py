"""--verify checks what changed; a remembered line is checked once and then trusted.

Measured 28.10.2026 on a 239-string file, three languages: a re-run with the project memory
and --verify, nothing edited, charged 54 942 characters and made 90 model calls, every run —
the meaning check re-read every remembered line. The server now checks only what it
translates (plus what it is asked to); this file pins the CLI half:

  * an edited line is sent to be translated and therefore checked;
  * an unchanged line whose memory entry passed verify is reused and not checked again;
  * an unchanged line that was never checked is reused (no second translation) but named in
    `verify_reused`, so the server checks it once — "skip" never becomes "never";
  * a line that verify flagged stays unchecked in the memory and is checked again next run;
  * --verify-all re-checks everything;
  * a different model or glossary keep-list invalidates the memory, `v` included;
  * --only-new keeps --verify (R15).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli as K       # noqa: E402
from kaeris import tm             # noqa: E402
from kaeris import incremental as inc   # noqa: E402

SRC = {"a": "Save changes", "b": "Delete account", "c": "Hello {name}"}
DE = {"a": "Änderungen speichern", "b": "Konto löschen", "c": "Hallo {name}"}


def sig(model="m1", keep=()):
    return inc.settings_signature(tone="", icu=False, keep=list(keep), model=model, app_context="")


class Stand:
    """A fake API: records what each submit carried and answers with DE. `review` is what the
    server's meaning check said, as {lang: {"error": [...], "review": [...]}}."""

    def __init__(self, review=None, model="m1", warnings=None):
        self.calls = []
        self.review = review or {}
        self.model = model
        self.warnings = warnings or {}

    def install(self, monkeypatch):
        stand = self

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def key_info(self):
                return {"model_id": stand.model}

            def config(self):
                return {}

            def languages(self):
                return {"de": "German"}

            def submit(self, fname, content, langs, glossary=None, **kw):
                sent = json.loads(content.decode())
                stand.calls.append({"sent": sent, **kw})
                return "job-%d" % len(stand.calls)

            def poll(self, job, **kw):
                return {"failed_langs": []}

            def download(self, job):
                sent = stand.calls[-1]["sent"]
                return {"de.json": json.dumps({k: DE[k] for k in sent}).encode()}

            def preview(self, job):
                call = stand.calls[int(job.split("-")[1]) - 1]
                sent = call["sent"]
                data = {"_source": dict(sent), "de": {k: DE[k] for k in sent}}
                if call.get("verify"):
                    rv = {"error": [], "review": [], "unchecked": []}
                    for st, ks in (stand.review.get("de") or {}).items():
                        rv[st] = [k for k in ks if k in sent]
                    rv["lines"] = len(sent)
                    data["_review"] = {"de": rv}
                    data["_back"] = {"de": {k: SRC[k] for k in sent}}
                if stand.warnings:
                    data["_warnings"] = stand.warnings
                return data

            def receipt(self, job):
                raise RuntimeError("no receipt in this stand")

        monkeypatch.setattr(K, "_client", lambda args: FakeClient())
        monkeypatch.setattr(K, "KaerisClient", FakeClient)
        monkeypatch.setattr(K, "_unknown_langs", lambda c, l: [])

    @property
    def last(self):
        return self.calls[-1]


def project(tmp_path, src=SRC, memory=None):
    p = tmp_path / "en.json"
    p.write_text(json.dumps(src), encoding="utf-8")
    if memory is not None:
        tm.save(str(tmp_path / tm.FILENAME), memory,
                lambda path, body: open(path, "w", encoding="utf-8").write(body))
    return p


def memory(verified=(), keys=("a", "b", "c"), s=None):
    m = {"version": tm.VERSION, "entries": {}}
    flat = {k: SRC[k] for k in keys}
    tm.record(m, flat, {"de": {k: DE[k] for k in keys}}, s or sig())
    tm.mark_verified(m, flat, "de", [k for k in verified if k in flat], s or sig())
    return m


def run(src, *extra):
    return K.main(["translate", str(src), "--langs", "de", "--out", os.path.dirname(str(src)),
                   *extra])


class TestWholeFile:

    def test_an_unchanged_verified_line_is_not_checked_again(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=("a", "b", "c")))
        assert run(src, "--verify") == 0
        assert st.last["reuse"] == {"de": DE}
        assert not st.last.get("verify_reused"), "verified lines were sent to be checked again"
        assert not st.last.get("verify_all")

    def test_a_line_never_checked_is_reused_and_checked_once(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=("a",)))
        run(src, "--verify")
        assert st.last["reuse"] == {"de": DE}, "an unchecked line was translated (and paid) again"
        assert sorted(st.last["verify_reused"]["de"]) == ["b", "c"]
        # …and it is remembered as checked: the next run does not ask again.
        run(src, "--verify")
        assert not st.last.get("verify_reused"), "the same lines were checked twice"

    def test_an_edited_line_is_translated_and_so_checked(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        edited = dict(SRC, b="Delete your account")
        global DE
        old = DE
        DE = dict(DE, b="Ihr Konto löschen")
        try:
            src = project(tmp_path, src=edited, memory=memory(verified=("a", "b", "c")))
            run(src, "--verify")
            assert "b" not in st.last["reuse"]["de"], "the edited line was served from memory"
            assert st.last["verify"] is True
            assert not st.last.get("verify_reused")
        finally:
            DE = old

    def test_a_flagged_line_stays_unchecked_and_is_checked_again(self, tmp_path, monkeypatch):
        st = Stand(review={"de": {"review": ["c"]}})
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=()))
        run(src, "--verify")
        run(src, "--verify")
        assert st.last["verify_reused"] == {"de": ["c"]}, (
            "a line the meaning check flagged was marked as checked")

    def test_a_line_the_detectors_flag_is_not_marked_checked(self, tmp_path, monkeypatch):
        st = Stand(warnings={"de": {"c": ["{name}"]}})
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=()))
        run(src, "--verify")
        run(src, "--verify")
        assert st.last["verify_reused"] == {"de": ["c"]}

    def test_verify_all_rechecks_everything(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=("a", "b", "c")))
        run(src, "--verify", "--verify-all")
        assert st.last["verify_all"] is True
        assert st.last["reuse"] == {"de": DE}, "--verify-all re-translated (and charged) the file"

    def test_without_verify_nothing_about_checks_is_sent(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=()))
        run(src)
        assert st.last["reuse"] == {"de": DE}
        assert not st.last.get("verify_reused") and not st.last.get("verify_all")


class TestTheMemoryKey:

    def test_a_new_model_invalidates_the_memory_and_its_checks(self, tmp_path, monkeypatch):
        st = Stand(model="m2")
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=("a", "b", "c")))
        run(src, "--verify")
        assert not st.last.get("reuse"), "a line written by another model was reused"
        assert not st.last.get("verify_reused")

    def test_a_new_keep_list_invalidates_the_memory(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path, memory=memory(verified=("a", "b", "c")))
        run(src, "--verify", "--keep", "KAERIS")
        assert not st.last.get("reuse"), "a line translated under another glossary was reused"

    def test_a_changed_translation_loses_its_check(self):
        m = memory(verified=("a",))
        tm.record(m, {"a": SRC["a"]}, {"de": {"a": "Speichern"}}, sig())
        assert tm.unverified(m, {"a": SRC["a"]}, {"de": {"a": "Speichern"}}, sig()) == {"de": ["a"]}


class TestOnlyNew:

    def test_only_new_keeps_verify(self, tmp_path, monkeypatch):
        """R15: --only-new used to drop --verify silently."""
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path)
        run(src, "--only-new", "--verify")
        assert st.calls and st.last["verify"] is True

    def test_only_new_marks_what_it_checked(self, tmp_path, monkeypatch):
        st = Stand()
        st.install(monkeypatch)
        src = project(tmp_path)
        run(src, "--only-new", "--verify")
        m = tm.load(str(tmp_path / tm.FILENAME))
        assert tm.unverified(m, SRC, {"de": DE}, sig()) == {}, "checked lines were not marked"
