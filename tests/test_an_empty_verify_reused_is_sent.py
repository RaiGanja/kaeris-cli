"""0.2.39: with --verify and a remembered line, the CLI always names `verify_reused`.

The server (release-1029) skips the meaning check on remembered lines only for a client that
sends the field, `{}` included; a request with `reuse` and no field is read as an old client and
the whole file is checked and charged. 0.2.38 left the field out when the list was empty, which
is exactly the fully verified project, so those runs paid for the whole file.
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import client as C   # noqa: E402
from kaeris import __version__   # noqa: E402
from kaeris import cli as K      # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_verify_reads_only_what_changed import Stand, memory, project, run   # noqa: E402


def _post(monkeypatch, **kw):
    """Run one submit against a fake socket; return (form fields, request headers)."""
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen["body"] = req.data
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return Resp(b'{"job_id": "j1"}')

    monkeypatch.setattr(C, "_urlopen", fake)
    C.KaerisClient("https://x.invalid", api_key="k").submit("en.json", b"{}", ["de"], **kw)
    body = seen["body"].decode("utf-8")
    fields = dict(re.findall(r'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, re.S))
    return fields, seen["headers"]


def test_the_client_names_an_empty_verify_reused(monkeypatch):
    fields, _ = _post(monkeypatch, verify=True, reuse={"de": {"a": "x"}}, verify_reused={})
    assert json.loads(fields["verify_reused"]) == {}


def test_the_client_names_it_even_when_the_caller_passed_nothing(monkeypatch):
    fields, _ = _post(monkeypatch, verify=True, reuse={"de": {"a": "x"}})
    assert json.loads(fields["verify_reused"]) == {}


def test_the_client_still_sends_the_lines_it_was_given(monkeypatch):
    fields, _ = _post(monkeypatch, verify=True, reuse={"de": {"a": "x"}},
                      verify_reused={"de": ["a"]})
    assert json.loads(fields["verify_reused"]) == {"de": ["a"]}


def test_nothing_is_sent_without_verify_or_without_reuse(monkeypatch):
    for kw in ({"verify": False, "reuse": {"de": {"a": "x"}}}, {"verify": True}):
        fields, _ = _post(monkeypatch, **kw)
        assert "verify_reused" not in fields, kw


def test_the_user_agent_carries_the_version(monkeypatch):
    _, headers = _post(monkeypatch)
    assert headers["user-agent"] == "kaeris-cli/" + __version__


def test_a_fully_verified_project_sends_an_empty_verify_reused(tmp_path, monkeypatch):
    st = Stand()
    st.install(monkeypatch)
    src = project(tmp_path, memory=memory(verified=("a", "b", "c")))
    assert run(src, "--verify") == 0
    assert st.last["reuse"]
    assert st.last["verify_reused"] == {}, "the server would read this run as an old client"
