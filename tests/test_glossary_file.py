"""kaeris-glossary.json: a term and how each language must say it.

`--keep` could only say "never translate this". A project that calls its shared area a
"workspace" also needs to say "in German that is «Arbeitsbereich», every time" — and needs CI to
notice the release where one string says «Arbeitsplatz». This file pins down:

  * the file format and its refusals (a broken glossary is exit 2, never a silent no-op);
  * where a term counts in the source: a whole word, so "Pro" is not found in "product";
  * how a translation is judged: the agreed word, or an inflected form of it; a language whose
    words change shape (Russian, Polish, Finnish…) gets a warning instead of a red build when
    even the stem is not there, because "not found" there may still be correct grammar;
  * `kaeris check` picks the file up by itself and fails the build on a wrong term;
  * `translate` sends the terms, and a changed term re-translates only the strings that use it
    and do not already say it the new way — the lock and the project memory both.
"""
import json
import os

import pytest

from kaeris import cli as K
from kaeris import glossary as G
from kaeris import incremental as inc


WS = {"term": "workspace",
      "translations": {"de": "Arbeitsbereich", "fr": "espace de travail",
                       "ru": "рабочее пространство", "ja": "ワークスペース"},
      "pos": "noun", "note": "the shared project area"}


def _gl(*entries):
    return G.parse({"terms": list(entries)})


# ── the file ─────────────────────────────────────────────────────────────────

def test_a_file_reads_into_entries():
    g = _gl(WS, {"term": "KAERIS", "keep": True})
    assert [e.term for e in g.entries] == ["workspace"]
    assert g.keep == ["KAERIS"]
    assert g.entries[0].renderings("de") == ["Arbeitsbereich"]
    assert g.entries[0].renderings("de-AT") == ["Arbeitsbereich"]   # the language's base entry
    assert g.entries[0].renderings("it") == []                      # no rule for Italian


def test_a_bare_list_is_a_glossary_too():
    assert [e.term for e in G.parse([WS]).entries] == ["workspace"]


def test_variants_are_a_list():
    g = _gl({"term": "sign in", "translations": {"de": ["Anmelden", "anmelden"]}})
    assert g.entries[0].renderings("de") == ["Anmelden", "anmelden"]


@pytest.mark.parametrize("bad,why", [
    ({"terms": [{"translations": {"de": "x"}}]}, "term"),
    ({"terms": [{"term": "x", "translations": "Arbeitsbereich"}]}, "translations"),
    ({"terms": [{"term": "x", "translations": {"de": 3}}]}, "translations"),
    ({"terms": [{"term": "x"}]}, "translations"),
    ({"terms": "workspace"}, "terms"),
    ({"terms": [{"term": "x", "translations": {"de": "y"}, "case_sensitive": "yes"}]}, "case_sensitive"),
    ({"terms": [WS, dict(WS)]}, "twice"),
])
def test_a_broken_glossary_is_refused_with_the_reason(bad, why):
    with pytest.raises(G.GlossaryError) as e:
        G.parse(bad)
    assert why in str(e.value)


# ── where a term counts in the source ────────────────────────────────────────

@pytest.mark.parametrize("src", ["Open the workspace", "Workspace settings", "2 workspaces",
                                 "workspace."])
def test_the_term_is_found_as_a_word(src):
    assert G.term_in(src, _gl(WS).entries[0])


@pytest.mark.parametrize("src", ["Open the workspaces2go app", "myworkspace", "Workspacer"])
def test_a_word_that_merely_contains_the_term_does_not_count(src):
    assert not G.term_in(src, _gl(WS).entries[0])


def test_pro_is_not_found_in_product():
    pro = _gl({"term": "Pro", "case_sensitive": True, "translations": {"de": "Pro"}}).entries[0]
    for src in ("Our product line", "Project plan", "$40k per project", "pro tips"):
        assert not G.term_in(src, pro), src
    assert G.term_in("Upgrade to Pro", pro)


# ── how a translation is judged ──────────────────────────────────────────────

def _faults(src, tr, lang, *entries):
    return G.faults(src, tr, lang, _gl(*(entries or (WS,))).entries)


def test_the_agreed_word_passes():
    assert _faults("Open the workspace", "Arbeitsbereich öffnen", "de") == []


def test_an_inflected_form_passes():
    assert _faults("Workspace name", "Name des Arbeitsbereichs", "de") == []
    assert _faults("Leave the workspace", "Выйти из рабочего пространства", "ru") == []
    assert _faults("In this workspace", "В этом рабочем пространстве", "ru") == []


def test_a_wrong_word_is_an_error_where_words_keep_their_shape():
    out = _faults("Open the workspace", "Ouvrir le bureau", "fr")
    assert [f["severity"] for f in out] == ["error"]
    assert out[0]["msg"] == 'glossary: "workspace" should be "espace de travail"'


def test_a_wrong_word_is_a_warning_where_words_change_shape():
    out = _faults("Open the workspace", "Открыть рабочий стол", "ru")
    assert [f["severity"] for f in out] == ["warn"]
    assert out[0]["msg"].startswith('glossary: "workspace" should be "рабочее пространство"')
    assert "inflected" in out[0]["msg"]


def test_german_compounds_are_not_a_false_error():
    # "Projektarbeitsbereich" is the agreed word inside a compound: German is judged by
    # occurrence, and a miss there is a warning, not a red build.
    assert _faults("Project workspace", "Projektarbeitsbereich", "de") == []
    out = _faults("Open the workspace", "Arbeitsplatz öffnen", "de")
    assert [f["severity"] for f in out] == ["warn"]


def test_languages_without_spaces_are_matched_inside_the_text():
    assert _faults("Open the workspace", "ワークスペースを開く", "ja") == []
    assert [f["severity"] for f in _faults("Open the workspace", "作業場を開く", "ja")] == ["error"]


def test_no_rule_for_the_language_means_no_finding():
    assert _faults("Open the workspace", "Apri lo spazio", "it") == []


def test_a_term_not_in_the_source_asks_nothing():
    assert _faults("Open the file", "Datei öffnen", "de") == []


def test_any_listed_variant_passes():
    e = {"term": "sign in", "translations": {"fr": ["se connecter", "connexion"]}}
    assert _faults("Sign in", "Connexion", "fr", e) == []


def test_a_longer_term_owns_its_words():
    label = {"term": "label", "translations": {"fr": "maison de disques"}}
    ship = {"term": "shipping label", "translations": {"fr": "étiquette d'expédition"}}
    assert _faults("Print the shipping label", "Imprimer l'étiquette d'expédition", "fr",
                   label, ship) == []


def test_a_case_sensitive_term_warns_on_a_recased_rendering():
    pro = {"term": "Pro", "case_sensitive": True, "translations": {"fr": "Pro"}}
    out = _faults("Upgrade to Pro", "Passer à PRO", "fr", pro)
    assert [f["severity"] for f in out] == ["warn"]
    assert "case" in out[0]["msg"]


# ── kaeris check ─────────────────────────────────────────────────────────────

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "en.json").write_text(json.dumps({"open": "Open the workspace",
                                                  "save": "Save"}), encoding="utf-8")
    (tmp_path / "fr.json").write_text(json.dumps({"open": "Ouvrir le bureau",
                                                  "save": "Enregistrer"}), encoding="utf-8")
    (tmp_path / "kaeris.json").write_text(json.dumps({"source": "en.json", "langs": ["fr"]}),
                                          encoding="utf-8")
    return tmp_path


def test_check_picks_up_the_glossary_file_and_fails(project, capsys):
    (project / "kaeris-glossary.json").write_text(json.dumps({"terms": [WS]}), encoding="utf-8")
    assert K.main(["check"]) == 1
    err = capsys.readouterr().err
    assert 'open: glossary: "workspace" should be "espace de travail"' in err


def test_check_without_a_glossary_file_is_unchanged(project):
    assert K.main(["check"]) == 0


def test_check_takes_the_file_from_a_flag(project, capsys):
    (project / "terms.json").write_text(json.dumps([WS]), encoding="utf-8")
    assert K.main(["check", "--glossary-file", "terms.json", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["faults"] == [{"lang": "fr", "key": "open", "severity": "error",
                              "msg": 'glossary: "workspace" should be "espace de travail"'}]


def test_check_takes_the_file_from_kaeris_json(project):
    (project / "terms.json").write_text(json.dumps([WS]), encoding="utf-8")
    (project / "kaeris.json").write_text(json.dumps(
        {"source": "en.json", "langs": ["fr"], "glossary_file": "terms.json"}), encoding="utf-8")
    assert K.main(["check"]) == 1


def test_a_broken_glossary_file_is_exit_2(project, capsys):
    (project / "kaeris-glossary.json").write_text('{"terms": [{"term": 1}]}', encoding="utf-8")
    assert K.main(["check"]) == 2
    assert "kaeris-glossary.json" in capsys.readouterr().err


def test_a_named_file_that_is_missing_is_exit_2(project, capsys):
    assert K.main(["check", "--glossary-file", "nope.json"]) == 2
    assert "nope.json" in capsys.readouterr().err


def test_keep_entries_join_the_keep_list(project, capsys):
    (project / "en.json").write_text(json.dumps({"open": "Open KAERIS"}), encoding="utf-8")
    (project / "fr.json").write_text(json.dumps({"open": "Ouvrir Kairos"}), encoding="utf-8")
    (project / "kaeris-glossary.json").write_text(json.dumps(
        {"terms": [{"term": "KAERIS", "keep": True}]}), encoding="utf-8")
    assert K.main(["check"]) == 1
    assert "glossary term dropped: KAERIS" in capsys.readouterr().err


# ── translate: the terms travel, the lock and the memory follow them ──────────

class _Server:
    def __init__(self, seen, answers):
        self.seen, self.answers = seen, answers

    def key_info(self):
        return {"model_id": "m"}

    def languages(self):
        return {"de": "German", "fr": "French"}

    def submit(self, fname, content, langs, *a, **kw):
        self.seen.append({"langs": list(langs), "keys": sorted(inc.flatten(json.loads(content))),
                          **kw})
        return "j%d" % len(self.seen)

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        sent = self.seen[int(job[1:]) - 1]
        return {f"{l}.json": json.dumps({k: self.answers[l][k] for k in sent["keys"]}).encode()
                for l in sent["langs"]}

    def preview(self, job):
        return {}

    def receipt(self, job):
        raise RuntimeError


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "en.json").write_text(json.dumps(
        {"open": "Open the workspace", "save": "Save", "leave": "Leave the workspace"}),
        encoding="utf-8")
    (tmp_path / "kaeris.json").write_text(json.dumps(
        {"source": "en.json", "langs": ["de"], "only_new": True}), encoding="utf-8")
    seen = []
    answers = {"de": {"open": "Arbeitsbereich öffnen", "save": "Speichern",
                      "leave": "Arbeitsbereich verlassen"}}
    monkeypatch.setattr(K, "_client", lambda args: _Server(seen, answers))
    return tmp_path, seen, answers


def _glossary(root, de):
    (root / "kaeris-glossary.json").write_text(json.dumps(
        {"terms": [{"term": "workspace", "translations": {"de": de}, "note": "shared area"}]}),
        encoding="utf-8")


def test_translate_sends_the_terms(run):
    root, seen, _ = run
    _glossary(root, "Arbeitsbereich")
    assert K.main(["translate"]) == 0
    assert seen[0]["terms"] == [{"term": "workspace", "translations": {"de": ["Arbeitsbereich"]},
                                 "note": "shared area"}]


def test_no_glossary_sends_no_terms(run):
    _, seen, _ = run
    assert K.main(["translate"]) == 0
    assert not seen[0].get("terms")


def test_a_new_term_already_honoured_costs_nothing(run):
    root, seen, _ = run
    assert K.main(["translate"]) == 0
    seen.clear()
    _glossary(root, "Arbeitsbereich")        # the existing German already says it
    assert K.main(["translate"]) == 0
    assert not seen, seen


def test_a_changed_term_retranslates_only_the_strings_that_use_it(run, capsys):
    root, seen, answers = run
    _glossary(root, "Arbeitsbereich")
    assert K.main(["translate"]) == 0
    seen.clear()
    answers["de"].update(open="Workspace öffnen", leave="Workspace verlassen")
    _glossary(root, "Workspace")
    capsys.readouterr()
    assert K.main(["translate"]) == 0
    assert [s["keys"] for s in seen] == [["leave", "open"]], seen
    assert "glossary" in capsys.readouterr().err
    de = json.loads((root / "de.json").read_text(encoding="utf-8"))
    assert de == {"open": "Workspace öffnen", "save": "Speichern",
                  "leave": "Workspace verlassen"}
    # …and the next run has nothing left to do.
    seen.clear()
    assert K.main(["translate"]) == 0
    assert not seen, seen


def test_a_glossary_change_is_not_a_settings_change(run):
    root, _, _ = run
    assert K.main(["translate"]) == 0
    before = inc.lock_settings(inc.load_lock(str(root / "kaeris.lock")))
    _glossary(root, "Arbeitsbereich")
    assert K.main(["translate"]) == 0
    assert inc.lock_settings(inc.load_lock(str(root / "kaeris.lock"))) == before


def test_the_project_memory_does_not_serve_a_string_against_the_glossary():
    g = _gl({"term": "workspace", "translations": {"de": "Workspace"}})
    hits = {"de": {"open": "Arbeitsbereich öffnen", "save": "Speichern"}}
    source = {"open": "Open the workspace", "save": "Save"}
    assert G.filter_reuse(hits, source, g.entries) == {"de": {"save": "Speichern"}}
