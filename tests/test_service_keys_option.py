"""`_`-keys are left untranslated by the server; --translate-service-keys asks for them (R20)."""
import json

from kaeris import cli as K
from kaeris.client import KaerisClient


def _form(**kw):
    body, _ = KaerisClient(api_url="https://kaeris.dev")._multipart(
        "en.json", b"{}", ["de"], **kw)
    return body


def test_the_default_sends_nothing_extra():
    assert b'name="translate_service_keys"' not in _form()


def test_the_option_reaches_the_form():
    assert b'name="translate_service_keys"\r\n\r\ntrue' in _form(translate_service_keys=True)


def test_the_flag_reaches_submit(tmp_path, monkeypatch):
    seen = {}

    class _S:
        def key_info(self):
            return {}

        def languages(self):
            return {"de": "German"}

        def submit(self, *a, **kw):
            seen.update(kw)
            return "j"

        def poll(self, job, **kw):
            return {"failed_langs": []}

        def download(self, job):
            return {"de.json": b"{}"}

        def preview(self, job):
            return {}

        def receipt(self, job):
            raise RuntimeError

    monkeypatch.setattr(K, "_client", lambda args: _S())
    src = tmp_path / "en.json"
    src.write_text(json.dumps({"_status": "source"}), encoding="utf-8")
    K.main(["translate", str(src), "--langs", "de", "--translate-service-keys"])
    assert seen.get("translate_service_keys") is True
