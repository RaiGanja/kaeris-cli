"""A dropped connection in the middle of a run is retried, not a traceback.

A client's run died with a bare ConnectionResetError while waiting for a job: the read of the
status answer broke, nothing caught it (only urllib's own URLError was), the CLI printed a
traceback and the job, already paid for, was left on the server. Reads (status, receipt,
preview, download) are repeated with a growing pause; polling carries on with the SAME job
id. A submit that may have reached the server is never sent twice. When the attempts run out
the message says the job is still there and what to run, and the exit code is not 0.
"""
import http.client
import io
import json
import urllib.error

import pytest

from kaeris import client as C
from kaeris import cli as K


class _Body:
    def __init__(self, payload=b"{}"):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


def _script(monkeypatch, steps):
    """Each request takes the next step: bytes (an answer), an exception raised on open
    ('open', exc) or while reading the body (exc alone)."""
    seen = []
    queue = list(steps)

    def fake(req, timeout=None):
        seen.append(req.full_url)
        step = queue.pop(0) if queue else queue_default
        if isinstance(step, tuple):
            raise step[1]
        return _Body(step)

    queue_default = BaseException("script ran out")
    monkeypatch.setattr(C, "_urlopen", fake)
    sleeps = []
    monkeypatch.setattr(C.time, "sleep", lambda s: sleeps.append(s))
    return seen, sleeps


def _status(state, **kw):
    return json.dumps({"status": state, **kw}).encode()


DROPS = [
    ConnectionResetError(10054, "forcibly closed"),
    ConnectionAbortedError(10053, "aborted"),
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    http.client.IncompleteRead(b"par"),
    TimeoutError("timed out"),
]


@pytest.mark.parametrize("drop", DROPS, ids=lambda e: type(e).__name__)
def test_a_drop_on_the_first_status_request_is_waited_out(monkeypatch, drop):
    seen, sleeps = _script(monkeypatch, [drop, _status("done")])
    c = C.KaerisClient(api_key=None)
    assert c.poll("J1", interval=0)["status"] == "done"
    assert seen == [c.api_url + "/api/status/J1"] * 2          # the same job, asked again
    assert sleeps and sleeps[0] > 0


def test_a_drop_on_a_late_status_request_keeps_polling_the_same_job(monkeypatch):
    seen, sleeps = _script(monkeypatch, [
        _status("running"), _status("running"), _status("running"),
        ConnectionResetError(10054, "reset"), ConnectionResetError(10054, "reset"),
        _status("done")])
    c = C.KaerisClient(api_key=None)
    assert c.poll("J9", interval=0)["status"] == "done"
    assert len(seen) == 6 and set(seen) == {c.api_url + "/api/status/J9"}
    assert sleeps[-2] < sleeps[-1] or sleeps[-2] <= sleeps[-1]  # the pause grows


def test_a_drop_wrapped_in_urlerror_is_the_same_thing(monkeypatch):
    _script(monkeypatch, [("open", urllib.error.URLError(ConnectionResetError(10054, "r"))),
                          _status("done")])
    assert C.KaerisClient(api_key=None).poll("J1", interval=0)["status"] == "done"


def test_a_drop_while_downloading_is_retried(monkeypatch):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("de.json", '{"a": "b"}')
    seen, _ = _script(monkeypatch, [ConnectionResetError(10054, "r"), buf.getvalue()])
    assert C.KaerisClient(api_key=None).download("J1") == {"de.json": b'{"a": "b"}'}
    assert seen == [C.DEFAULT_API + "/api/download/J1"] * 2


def test_when_the_attempts_run_out_the_job_is_named_and_the_advice_is_given(monkeypatch):
    seen, _ = _script(monkeypatch, [ConnectionResetError(10054, "r")] * 50)
    c = C.KaerisClient(api_key=None)
    with pytest.raises(C.KaerisError) as ei:
        c.poll("J7", interval=0)
    msg = str(ei.value)
    assert "J7" in msg and "--only-new" in msg
    assert len(seen) == C.NET_RETRIES + 1


def test_the_cli_ends_with_a_message_and_a_failing_code_not_a_traceback(monkeypatch, tmp_path,
                                                                        capsys):
    def routed(req, timeout=None):
        url = req.full_url
        if url.endswith("/api/languages"):
            return _Body(json.dumps({"de": "German"}).encode())
        if url.endswith("/api/translate"):
            return _Body(json.dumps({"job_id": "J5", "edit_token": "t"}).encode())
        if "/api/status/" in url:
            return _Body(ConnectionResetError(10054, "r"))
        return _Body(b"{}")
    monkeypatch.setattr(C, "_urlopen", routed)
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    (tmp_path / "en.json").write_text('{"a": "Hello"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = K.main(["translate", "en.json", "--langs", "de", "--quiet"])
    out = capsys.readouterr()
    assert code != 0
    assert "Traceback" not in out.err + out.out
    assert "J5" in out.err and "--only-new" in out.err


def test_a_submit_that_may_have_arrived_is_not_sent_a_second_time(monkeypatch):
    seen, _ = _script(monkeypatch, [ConnectionResetError(10054, "r"),
                                    json.dumps({"job_id": "X"}).encode()])
    with pytest.raises(C.KaerisError) as ei:
        C.KaerisClient(api_key=None).submit("en.json", b'{"a":"b"}', ["de"])
    assert len(seen) == 1                       # not repeated: it might be paid for twice
    assert "--only-new" in str(ei.value)


def test_a_submit_that_never_connected_is_tried_again(monkeypatch):
    seen, _ = _script(monkeypatch, [("open", ConnectionRefusedError(10061, "refused")),
                                    json.dumps({"job_id": "X"}).encode()])
    assert C.KaerisClient(api_key=None).submit("en.json", b'{"a":"b"}', ["de"]) == "X"
    assert len(seen) == 2
