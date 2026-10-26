"""With several jobs in flight (--jobs) a run can hit the per-minute submit limit of a plan
(anonymous 10, free 20 a minute). A 429 used to end that file with "HTTP 429"; the job was
never started, so the honest answer is to wait as long as the server says and send it again."""
import http.server
import json
import os
import socketserver
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import client as kc
from kaeris.client import KaerisClient, KaerisError


def _server(answers):
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            code, headers = answers[min(len(hits), len(answers) - 1)]
            hits.append(code)
            body = json.dumps({"job_id": "j1"} if code == 200 else
                              {"detail": "Rate limit exceeded: 10 per 1 minute"}).encode()
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1], hits


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr(kc.time, "sleep", slept.append)
    return slept


def test_a_429_is_waited_out_and_sent_again(waits):
    srv, port, hits = _server([(429, {"Retry-After": "7"}), (200, {})])
    try:
        job = KaerisClient(api_url=f"http://127.0.0.1:{port}").submit("en.json", b"{}", ["de"])
    finally:
        srv.shutdown()
    assert job == "j1"
    assert hits == [429, 200]
    assert waits == [7]


def test_the_wait_is_bounded_and_the_error_still_says_why(waits):
    srv, port, hits = _server([(429, {"Retry-After": "3600"})])
    try:
        with pytest.raises(KaerisError, match="429"):
            KaerisClient(api_url=f"http://127.0.0.1:{port}").submit("en.json", b"{}", ["de"])
    finally:
        srv.shutdown()
    assert len(hits) == 4                       # the first try and three more
    assert all(w <= 60 for w in waits)


def test_other_errors_are_not_retried(waits):
    srv, port, hits = _server([(402, {})])
    try:
        with pytest.raises(KaerisError, match="402"):
            KaerisClient(api_url=f"http://127.0.0.1:{port}").submit("en.json", b"{}", ["de"])
    finally:
        srv.shutdown()
    assert hits == [402] and waits == []
