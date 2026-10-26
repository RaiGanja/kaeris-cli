"""Android strings land in res/values-<lang>/ (замер 0.2, R22).

On 0.2.29 `kaeris translate app/src/main/res/values/strings.xml --langs de` wrote
res/values/strings/strings.xml: the archive member is `values-de/strings.xml`, the language was
read from its file name ("strings"), and the result went where Android never looks.
"""
import os

import pytest

from kaeris import cli as K

XML = b'<?xml version="1.0" encoding="utf-8"?>\n<resources><string name="a">Save</string></resources>\n'


class _Server:
    def key_info(self):
        return {"model_id": "m1"}

    def languages(self):
        return {"de": "German", "pt-BR": "Portuguese (Brazil)"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.langs = langs
        return "job"

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        return {f"values-{l}/strings.xml": XML.replace(b"Save", l.encode()) for l in self.langs}

    def preview(self, job):
        return {}

    def receipt(self, job):
        raise RuntimeError


@pytest.fixture
def res(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "_client", lambda args: _Server())
    values = tmp_path / "app" / "src" / "main" / "res" / "values"
    values.mkdir(parents=True)
    (values / "strings.xml").write_bytes(XML)
    return values.parent


def test_next_to_values(res):
    assert K.main(["translate", str(res / "values" / "strings.xml"), "--langs", "de,pt-BR"]) == 0
    assert (res / "values-de" / "strings.xml").read_bytes() == XML.replace(b"Save", b"de")
    assert (res / "values-pt-BR" / "strings.xml").is_file()
    assert not (res / "values" / "strings").exists()
    assert sorted(os.listdir(res / "values")) == ["strings.xml"]


def test_with_out(res, tmp_path):
    out = tmp_path / "out"
    K.main(["translate", str(res / "values" / "strings.xml"), "--langs", "de", "--out", str(out)])
    assert (out / "values-de" / "strings.xml").is_file()
