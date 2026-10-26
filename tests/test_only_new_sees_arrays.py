"""--only-new did not see strings inside JSON arrays (замер 0.2, R16) and dropped --verify (R15).

Reproduced on 0.2.29: a pricing form keeps its budget ranges in an array. The price in the
first item changed and a third item was added; `kaeris translate en.json --langs de --only-new`
answered "Everything already up to date", and de.json kept the OLD price with no third item.
The GitHub Action runs with only-new: true by default, so every CI run shipped the stale price.

The same run with --verify silently did no verification at all: no back-translation was asked
for and verify.json was never written, while the plain run writes it.
"""
import json

import pytest

from kaeris import cli as K
from kaeris import incremental as inc


def _translate(node, lang):
    if isinstance(node, str):
        return f"{lang.upper()}:{node}"
    if isinstance(node, list):
        return [_translate(v, lang) for v in node]
    if isinstance(node, dict):
        return {k: _translate(v, lang) for k, v in node.items()}
    return node


class _Server:
    """Answers like the real API: the document comes back in the shape it was sent."""

    def __init__(self):
        self.submits = []
        self._jobs = {}

    def key_info(self):
        return {"model_id": "m1"}

    def languages(self):
        return {"de": "German", "fr": "French"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        sent = json.loads(content.decode("utf-8"))
        job = f"job-{len(self.submits)}"
        self.submits.append({"doc": sent, "langs": langs, **kw})
        self._jobs[job] = (sent, langs, kw)
        return job

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        sent, langs, _ = self._jobs[job]
        return {f"{l}.json": json.dumps(_translate(sent, l), ensure_ascii=False).encode()
                for l in langs}

    def preview(self, job):
        sent, langs, kw = self._jobs[job]
        flat = inc.flatten(sent)
        data = {"_source": flat}
        for l in langs:
            data[l] = {k: f"{l.upper()}:{v}" for k, v in flat.items()}
        if kw.get("verify"):
            data["_back"] = {l: {k: f"back:{v}" for k, v in flat.items()} for l in langs}
        return data

    def receipt(self, job):
        raise RuntimeError("no receipt in this stand")


@pytest.fixture
def server(monkeypatch):
    s = _Server()
    monkeypatch.setattr(K, "_client", lambda args: s)
    return s


V1 = {"budgets": ["Under $15k", "$15k – $40k"], "title": "Budget"}
V2 = {"budgets": ["Below $10k", "$15k – $40k", "Not sure yet"], "title": "Budget"}


def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _run(*argv):
    return K.main(["translate", *argv])


def test_strings_inside_an_array_are_translatable_keys():
    assert inc.flatten(V1) == {"budgets.0": "Under $15k", "budgets.1": "$15k – $40k",
                               "title": "Budget"}


def test_an_edited_and_an_added_array_item_reach_the_target(tmp_path, server):
    en = tmp_path / "en.json"
    _write(en, V1)
    assert _run(str(en), "--langs", "de", "--only-new") == 0
    de = tmp_path / "de.json"
    assert json.loads(de.read_text("utf-8"))["budgets"] == ["DE:Under $15k", "DE:$15k – $40k"]

    _write(en, V2)
    assert _run(str(en), "--langs", "de", "--only-new") == 0
    got = json.loads(de.read_text("utf-8"))
    assert got["budgets"] == ["DE:Below $10k", "DE:$15k – $40k", "DE:Not sure yet"], got
    assert got["title"] == "DE:Budget"
    second = server.submits[-1]["doc"]
    assert "Budget" not in json.dumps(second), "an unchanged string was paid for again"


def test_a_nested_array_keeps_its_shape(tmp_path, server):
    en = tmp_path / "en.json"
    _write(en, {"form": {"budgets": ["Under $15k"], "label": "Budget"}})
    _run(str(en), "--langs", "de", "--only-new")
    _write(en, {"form": {"budgets": ["Under $15k", "Not sure yet"], "label": "Budget"}})
    _run(str(en), "--langs", "de", "--only-new")
    got = json.loads((tmp_path / "de.json").read_text("utf-8"))
    assert got == {"form": {"budgets": ["DE:Under $15k", "DE:Not sure yet"],
                            "label": "DE:Budget"}}


def test_a_shorter_source_array_shortens_the_target(tmp_path, server):
    en = tmp_path / "en.json"
    _write(en, V2)
    _run(str(en), "--langs", "de", "--only-new")
    _write(en, {"budgets": ["Below $5k", "$15k – $40k"], "title": "Budget"})
    _run(str(en), "--langs", "de", "--only-new")
    got = json.loads((tmp_path / "de.json").read_text("utf-8"))
    assert got["budgets"] == ["DE:Below $5k", "DE:$15k – $40k"]


def test_numbers_inside_an_array_survive_the_merge(tmp_path, server):
    en = tmp_path / "en.json"
    _write(en, {"steps": ["One", 2, None]})
    _run(str(en), "--langs", "de", "--only-new")
    _write(en, {"steps": ["One", 2, None, "Four"]})
    _run(str(en), "--langs", "de", "--only-new")
    got = json.loads((tmp_path / "de.json").read_text("utf-8"))
    assert got["steps"] == ["DE:One", 2, None, "DE:Four"]


def test_only_new_keeps_verify(tmp_path, server):
    en = tmp_path / "en.json"
    _write(en, {"a": "Save"})
    assert _run(str(en), "--langs", "de,fr", "--only-new", "--verify") == 0
    assert server.submits and all(s.get("verify") for s in server.submits), server.submits
    back = json.loads((tmp_path / "verify.json").read_text("utf-8"))
    assert back == {"de": {"a": "back:Save"}, "fr": {"a": "back:Save"}}, \
        "one verify.json for the run, holding every language"
