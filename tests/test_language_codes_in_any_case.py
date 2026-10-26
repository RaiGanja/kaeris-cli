"""Language codes typed the way file names are written: fr-ca, pt_pt, zh-hant.

A client's `-l fr-ca,pt-pt,zh-hant` was refused by the server, and in a 43-language run those
three vanished: the server listed them under `warnings` in its answer, which the client never
read, and the run reported success with 40 files. Now the CLI sends BCP-47 case (fr-CA, pt-PT,
zh-Hant), refuses by name a code the server does not offer, and prints whatever the server
says it left out. `--lang-case lower` only changes the names of the files written.
"""
import io
import json
import re
from unittest import mock

import pytest

from kaeris import cli as K

OFFERED = {"de": "German", "pt": "Portuguese", "pt-PT": "Portuguese (Portugal)",
           "fr": "French", "fr-CA": "French (Canada)", "zh-Hant": "Chinese (Traditional)"}


class _Server:
    def __init__(self, warnings=None):
        self.jobs = []
        self._warnings = warnings or []

    def key_info(self):
        return {}

    def languages(self):
        return OFFERED

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.jobs.append(list(langs))
        return str(len(self.jobs) - 1)

    def job_warnings(self, job):
        return self._warnings

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        return {f"{l}.json": json.dumps({"a": "x"}).encode() for l in self.jobs[int(job)]}

    def preview(self, job):
        return {}

    def receipt(self, job):
        raise RuntimeError


def _run(srv, monkeypatch, *argv):
    monkeypatch.setattr(K, "_client", lambda args: srv)
    err, out = io.StringIO(), io.StringIO()
    with mock.patch("sys.stderr", err), mock.patch("sys.stdout", out):
        code = K.main(["translate", *argv])
    return code, err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "en.json").write_text('{"a": "Hello"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _json_files(project):
    return sorted(p.name for p in (project / "i18n").iterdir() if p.name != "en.json"
                  and p.suffix == ".json")


def test_lower_case_codes_go_to_the_server_in_bcp47_case(project, monkeypatch):
    srv = _Server()
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "fr-ca,pt-pt,zh-hant")
    assert code == 0, err
    assert srv.jobs == [["fr-CA", "pt-PT", "zh-Hant"]]
    assert _json_files(project) == ["fr-CA.json", "pt-PT.json", "zh-Hant.json"]


def test_underscores_and_shouting_are_read_too_and_repeats_collapse(project, monkeypatch):
    srv = _Server()
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "FR_ca, pt_PT,ZH-HANT,fr-CA")
    assert code == 0, err
    assert srv.jobs == [["fr-CA", "pt-PT", "zh-Hant"]]


def test_lang_case_lower_renames_files_but_not_the_codes_sent(project, monkeypatch):
    srv = _Server()
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "fr-CA,pt-PT,zh-Hant",
                     "--lang-case", "lower")
    assert code == 0, err
    assert srv.jobs == [["fr-CA", "pt-PT", "zh-Hant"]]
    assert _json_files(project) == ["fr-ca.json", "pt-pt.json", "zh-hant.json"]


def test_lang_case_lower_with_lower_input_still_sends_bcp47(project, monkeypatch):
    srv = _Server()
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "fr-ca,zh-hant",
                     "--lang-case", "lower")
    assert code == 0, err
    assert srv.jobs == [["fr-CA", "zh-Hant"]]
    assert _json_files(project) == ["fr-ca.json", "zh-hant.json"]


def test_a_regional_code_the_server_does_not_offer_is_refused_by_name_not_dropped(
        project, monkeypatch):
    """pt-BR passed the old base-only check ('pt' is offered) and the server dropped it."""
    srv = _Server()
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "de,pt-br,fr-ca,xx")
    assert code == 1
    assert "pt-BR" in err and "xx" in err
    assert "fr-CA" not in err.split("Unknown language", 1)[-1].split("\n")[0]
    assert srv.jobs == []


def test_what_the_server_says_it_left_out_is_printed(project, monkeypatch):
    srv = _Server(warnings=["Unknown languages ignored: qq-zz"])
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "de,fr-ca")
    assert "qq-zz" in err


def test_the_client_keeps_the_servers_warnings(monkeypatch):
    from kaeris import client as C
    body = json.dumps({"job_id": "j1", "edit_token": "t",
                       "warnings": ["Unknown languages ignored: qq"]}).encode()

    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body

    monkeypatch.setattr(C, "_urlopen", lambda req, timeout=None: _R())
    c = C.KaerisClient(api_key="k")
    assert c.submit("en.json", b'{"a":"b"}', ["de"]) == "j1"
    assert c.job_warnings("j1") == ["Unknown languages ignored: qq"]


# ── --lang-case lower reaches every path that writes a file ──────────────────

def _names(project):
    return sorted(p.name for p in (project / "i18n").iterdir()
                  if p.suffix == ".json" and p.name not in ("en.json", "kaeris-tm.json"))


def test_only_new_with_lang_case_lower_writes_the_lower_case_name(project, monkeypatch):
    """A run with both flags wrote zh-Hant.json and broke a case-sensitive build."""
    srv = _Server()
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "zh-Hant,fr-CA",
                     "--only-new", "--lang-case", "lower")
    assert code == 0, err
    assert srv.jobs == [["zh-Hant"], ["fr-CA"]]
    assert _names(project) == ["fr-ca.json", "zh-hant.json"]


def test_only_new_with_lang_case_lower_from_kaeris_json(project, monkeypatch):
    (project / "kaeris.json").write_text(json.dumps(
        {"source": "i18n/en.json", "langs": ["zh-Hant"], "lang_case": "lower"}), encoding="utf-8")
    code, err = _run(_Server(), monkeypatch, "--only-new")
    assert code == 0, err
    assert _names(project) == ["zh-hant.json"]


def test_a_second_only_new_run_updates_the_same_lower_case_file(project, monkeypatch):
    for _ in range(2):
        code, err = _run(_Server(), monkeypatch, "i18n/en.json", "--langs", "zh-Hant",
                         "--only-new", "--lang-case", "lower")
        assert code == 0, err
    assert _names(project) == ["zh-hant.json"]


def test_several_files_with_lang_case_lower(project, monkeypatch):
    (project / "i18n" / "other.json").write_text('{"b": "Bye"}', encoding="utf-8")
    code, err = _run(_Server(), monkeypatch, "i18n/en.json", "i18n/other.json",
                     "--langs", "zh-Hant", "--lang-case", "lower")
    assert code == 0, err
    assert not [p for p in project.rglob("*") if "Hant" in p.name], list(project.rglob("*"))


def test_an_older_mixed_case_file_is_renamed_not_written_into(project, monkeypatch):
    """The file was made before the option was set. The option wins; the content stays."""
    (project / "i18n" / "zh-Hant.json").write_text('{"old": "舊"}', encoding="utf-8")
    (project / "i18n" / "en.json").write_text('{"old": "Old", "a": "Hello"}', encoding="utf-8")
    code, err = _run(_Server(), monkeypatch, "i18n/en.json", "--langs", "zh-Hant",
                     "--only-new", "--lang-case", "lower")
    assert code == 0, err
    names = sorted(p.name for p in (project / "i18n").iterdir())
    assert "zh-hant.json" in names and "zh-Hant.json" not in names, names
    assert "舊" in (project / "i18n" / "zh-hant.json").read_text("utf-8")


def test_a_whole_file_run_renames_an_older_mixed_case_file_too(project, monkeypatch):
    (project / "i18n" / "zh-Hant.json").write_text('{"a": "舊"}', encoding="utf-8")
    code, err = _run(_Server(), monkeypatch, "i18n/en.json", "--langs", "zh-Hant",
                     "--lang-case", "lower")
    assert code == 0, err
    assert sorted(p.name for p in (project / "i18n").iterdir()
                  if p.suffix == ".json" and p.name != "en.json") == ["zh-hant.json"]


def test_without_the_option_an_existing_name_is_still_kept(project, monkeypatch):
    (project / "i18n" / "zh-hant.json").write_text('{"a": "舊"}', encoding="utf-8")
    code, err = _run(_Server(), monkeypatch, "i18n/en.json", "--langs", "zh-Hant")
    assert code == 0, err
    assert _names(project) == ["zh-hant.json"]


def test_apply_fixes_finds_the_file_on_the_only_new_path(project, monkeypatch):
    srv = _Server()
    args_seen = {}
    real = K._write_verify

    def spy(args, files):
        args_seen.update(getattr(args, "verify_files", {}))
        return real(args, files)
    monkeypatch.setattr(K, "_write_verify", spy)
    code, err = _run(srv, monkeypatch, "i18n/en.json", "--langs", "zh-Hant", "--only-new",
                     "--lang-case", "lower", "--verify")
    assert code == 0, err
    (by_lang,) = args_seen.values()
    assert by_lang["zh-Hant"].endswith("zh-hant.json")
