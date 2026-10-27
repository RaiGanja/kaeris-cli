"""`kaeris translate --model <id>` and `"models": {"de": "<id>"}` in kaeris.json (Lifetime).

The server decides who may choose (Lifetime/BYOK) and which ids are allowed; the CLI's part:
  * the flag and the config reach the request — `model` for the run, `models` per language;
  * a flag beats kaeris.json, a language's own entry beats both;
  * a malformed `models` is refused before anything is sent;
  * the model is part of the lock's settings, so choosing another one re-translates the whole
    locale under --only-new instead of leaving it half one model, half another;
  * --only-new sends one job per language, each with that language's model.
"""
import json
import os

import pytest

from kaeris import cli as K
from kaeris import incremental as inc
from kaeris.client import KaerisClient


def _form(**kw):
    body, _ = KaerisClient(api_url="https://kaeris.dev")._multipart("en.json", b"{}", ["de", "fr"], **kw)
    return body


def test_no_choice_sends_nothing_extra():
    body = _form()
    assert b'name="model"' not in body and b'name="models"' not in body


def test_the_choice_reaches_the_form():
    body = _form(model="openai/gpt-4.1", models={"de": "anthropic/claude-sonnet-4.5"})
    assert b'name="model"\r\n\r\nopenai/gpt-4.1\r\n' in body
    assert b'name="models"\r\n\r\n{"de": "anthropic/claude-sonnet-4.5"}\r\n' in body


class _Server:
    def __init__(self, seen, model_id="google/gemini-2.5-flash-lite"):
        self.seen, self.model_id = seen, model_id

    def key_info(self):
        return {"model_id": self.model_id}

    def languages(self):
        return {"de": "German", "fr": "French"}

    def submit(self, fname, content, langs, *a, **kw):
        self.seen.append({"langs": list(langs), **kw})
        return "j%d" % len(self.seen)

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        langs = self.seen[int(job[1:]) - 1]["langs"]
        return {f"{l}.json": json.dumps({"a": f"{l}-a"}).encode() for l in langs}

    def preview(self, job):
        return {}

    def receipt(self, job):
        raise RuntimeError


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "en.json").write_text(json.dumps({"a": "Save"}), encoding="utf-8")
    seen = []
    monkeypatch.setattr(K, "_client", lambda args: _Server(seen))
    return tmp_path, seen


def test_the_flag_reaches_submit(project):
    _, seen = project
    assert K.main(["translate", "en.json", "--langs", "de,fr", "--model", "openai/gpt-4.1"]) == 0
    assert seen[0]["model"] == "openai/gpt-4.1" and not seen[0].get("models"), seen


def test_kaeris_json_sets_the_model_and_one_per_language(project):
    root, seen = project
    (root / "kaeris.json").write_text(json.dumps(
        {"source": "en.json", "langs": ["de", "fr"], "model": "openai/gpt-4.1",
         "models": {"de": "anthropic/claude-sonnet-4.5", "ja": "google/gemini-2.5-pro"}}), encoding="utf-8")
    assert K.main(["translate"]) == 0
    assert seen[0]["model"] == "openai/gpt-4.1", seen
    # Only the languages of this run travel; ja is not in it.
    assert seen[0]["models"] == {"de": "anthropic/claude-sonnet-4.5"}, seen


def test_the_flag_beats_kaeris_json(project):
    root, seen = project
    (root / "kaeris.json").write_text(json.dumps({"source": "en.json", "langs": ["de"],
                                                  "model": "openai/gpt-4.1"}), encoding="utf-8")
    assert K.main(["translate", "--model", "openai/gpt-5"]) == 0
    assert seen[0]["model"] == "openai/gpt-5", seen


@pytest.mark.parametrize("bad", [["de", "x"], {"de": 3}, "openai/gpt-4.1"])
def test_a_malformed_models_entry_is_refused_before_sending(project, bad, capsys):
    root, seen = project
    (root / "kaeris.json").write_text(json.dumps({"source": "en.json", "langs": ["de"],
                                                  "models": bad}), encoding="utf-8")
    assert K.main(["translate"]) == 2
    assert not seen
    assert "models" in capsys.readouterr().err


def test_only_new_sends_each_language_its_own_model(project):
    root, seen = project
    (root / "kaeris.json").write_text(json.dumps(
        {"source": "en.json", "langs": ["de", "fr"], "only_new": True, "model": "openai/gpt-4.1",
         "models": {"de": "anthropic/claude-sonnet-4.5"}}), encoding="utf-8")
    assert K.main(["translate"]) == 0
    by_lang = {s["langs"][0]: s.get("model") for s in seen}
    assert by_lang == {"de": "anthropic/claude-sonnet-4.5", "fr": "openai/gpt-4.1"}, seen


def test_the_lock_records_the_chosen_model_and_a_change_retranslates(project, capsys):
    root, seen = project
    cfg = {"source": "en.json", "langs": ["de"], "only_new": True, "model": "openai/gpt-4.1"}
    (root / "kaeris.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert K.main(["translate"]) == 0
    lock = inc.lock_settings(inc.load_lock(str(root / "kaeris.lock")))
    assert "openai/gpt-4.1" in lock["model"], lock
    # Same model again: nothing to do.
    seen.clear()
    assert K.main(["translate"]) == 0
    assert not seen, seen
    # Another model: the whole locale again, said as a model change.
    cfg["model"] = "anthropic/claude-sonnet-4.5"
    (root / "kaeris.json").write_text(json.dumps(cfg), encoding="utf-8")
    capsys.readouterr()
    assert K.main(["translate"]) == 0
    assert seen and seen[0]["model"] == "anthropic/claude-sonnet-4.5", seen
    assert "model changed" in capsys.readouterr().err


def test_no_choice_keeps_the_servers_model_in_the_lock(project):
    root, seen = project
    (root / "kaeris.json").write_text(json.dumps({"source": "en.json", "langs": ["de"], "only_new": True}),
                                      encoding="utf-8")
    assert K.main(["translate"]) == 0
    lock = inc.lock_settings(inc.load_lock(str(root / "kaeris.lock")))
    assert lock["model"] == "google/gemini-2.5-flash-lite", lock
    assert "model" not in seen[0] or not seen[0]["model"], seen
