"""A server restart mid-run lost a file (a customer run, 26.10.2026).

The server reloaded while a 12-language job was 81% done. The job came back as an error —
"interrupted (the server process restarted) and can't finish. Nothing was charged — please try
again." — and the CLI printed it and moved on: the customer had to notice and re-run by hand.

The server already returns the job's reserved quota when it fails it this way, so sending the
same job again is not a second charge. The CLI now does that itself, at most twice, with a
pause, and says so. Any other failure is reported as before, without a retry.
"""
import http.server
import io
import json
import socketserver
import threading
import unittest.mock as mock

import pytest

from kaeris import cli as K
from kaeris import client as kc
from kaeris.client import KaerisClient, KaerisError, KaerisInterrupted

OLD_SERVER_MSG = ("Translation was interrupted (the server process restarted) and can't finish. "
                  "Nothing was charged — please try again.")


def _status_server(status):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(status).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.mark.parametrize("status", [
    {"status": "error", "error": "Interrupted.", "retryable": True},
    {"status": "error", "error": OLD_SERVER_MSG},                 # a server without the field
])
def test_a_job_that_died_with_the_server_is_told_apart(status):
    srv, url = _status_server(status)
    try:
        with pytest.raises(KaerisInterrupted):
            KaerisClient(api_url=url).poll("j1", interval=0)
    finally:
        srv.shutdown()


def test_an_ordinary_failure_is_not_an_interruption():
    srv, url = _status_server({"status": "error", "error": "File is not valid JSON",
                               "retryable": False})
    try:
        with pytest.raises(KaerisError) as e:
            KaerisClient(api_url=url).poll("j1", interval=0)
    finally:
        srv.shutdown()
    assert not isinstance(e.value, KaerisInterrupted)


class _Server:
    """Every job dies with the server until `restarts` of them have; then jobs finish."""

    def __init__(self, restarts, error=None):
        self.restarts = restarts
        self.error = error
        self.submits = 0

    def key_info(self):
        return {}

    def languages(self):
        return {"de": "German"}

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.submits += 1
        return str(self.submits)

    def poll(self, job, **kw):
        if self.error:
            raise self.error
        if int(job) <= self.restarts:
            raise KaerisInterrupted(OLD_SERVER_MSG)
        return {"failed_langs": []}

    def download(self, job):
        return {"de.json": b'{"hi": "Hallo"}'}

    def preview(self, job):
        return {}

    def receipt(self, job):
        return {}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "en.json").write_text('{"hi": "Hi"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    slept = []
    monkeypatch.setattr(K.time, "sleep", slept.append)
    return tmp_path, slept


def _run(srv, monkeypatch):
    monkeypatch.setattr(K, "_client", lambda args: srv)
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        code = K.main(["translate", "en.json", "--langs", "de"])
    return code, err.getvalue()


def test_an_interrupted_job_is_sent_again_and_lands(project, monkeypatch):
    tmp, slept = project
    srv = _Server(restarts=1)
    code, out = _run(srv, monkeypatch)
    assert code == 0, out
    assert srv.submits == 2
    assert (tmp / "de.json").is_file()
    assert "again" in out.lower(), out
    assert slept and all(s > 0 for s in slept), "sent again with no pause, into the same restart"


def test_it_gives_up_after_two_more_tries(project, monkeypatch):
    _, slept = project
    srv = _Server(restarts=99)
    code, out = _run(srv, monkeypatch)
    assert code == 1
    assert srv.submits == 3
    assert "interrupted" in out.lower(), out


def test_other_failures_are_not_sent_again(project, monkeypatch):
    srv = _Server(restarts=0, error=KaerisError("HTTP 400: File is not valid JSON"))
    code, _ = _run(srv, monkeypatch)
    assert code == 1
    assert srv.submits == 1
