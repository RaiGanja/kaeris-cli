"""--receipt must work on both translate paths.

A documented flag that silently does nothing on the path the GitHub Action uses (`only-new`)
is worse than one that does not exist: the file's absence reads as "nothing happened".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli as K   # noqa: E402


def _stand(monkeypatch, tmp_path):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def key_info(self):
            return {"model_id": "m1"}

        def submit(self, fname, content, langs, glossary=None, **kw):
            return "job-1"

        def poll(self, job, **kw):
            return {"failed_langs": []}

        def download(self, job):
            return {"de.json": json.dumps({"a": "Speichern"}).encode()}

        def preview(self, job):
            return {"_source": {"a": "Save"}, "de": {"a": "Speichern"}}

        def receipt(self, job):
            return {"job_id": job, "model": "m1", "plan": "premium", "strings": 1,
                    "seconds": 0.1, "languages": {"requested": ["de"], "delivered": ["de"],
                                                  "failed": []},
                    "characters": {"metered": 4, "reused_not_charged": 0, "refunded": 0},
                    "settings": {}, "glossary": [], "quality": {}}

    monkeypatch.setattr(K, "_client", lambda args: FakeClient())
    monkeypatch.setattr(K, "KaerisClient", FakeClient)


def test_receipt_is_written_on_a_plain_run(tmp_path, monkeypatch):
    src = tmp_path / "en.json"
    src.write_text(json.dumps({"a": "Save"}), encoding="utf-8")
    _stand(monkeypatch, tmp_path)
    out = tmp_path / "r.json"
    K.main(["translate", str(src), "--langs", "de", "--out", str(tmp_path),
            "--receipt", str(out)])
    assert json.loads(out.read_text())["model"] == "m1"


def test_receipt_is_written_on_an_incremental_run(tmp_path, monkeypatch):
    """The path the shipped GitHub Action uses."""
    src = tmp_path / "en.json"
    src.write_text(json.dumps({"a": "Save"}), encoding="utf-8")
    _stand(monkeypatch, tmp_path)
    out = tmp_path / "r.json"
    K.main(["translate", str(src), "--langs", "de", "--out", str(tmp_path),
            "--only-new", "--receipt", str(out)])
    assert out.exists(), "--only-new wrote no receipt at all"
    assert json.loads(out.read_text())["model"] == "m1"
