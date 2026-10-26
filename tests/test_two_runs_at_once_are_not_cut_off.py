"""Two `kaeris translate --jobs 4` at once: both died with HTTP 429 within seven seconds
(a customer run, 26.10.2026).

The API server logged no 429 at all that day: the limit that fired is the edge's per-address
rate limit, which answers with an HTML page ("error code: 1015") and clears within a minute.
Eight jobs polling once a second from one address is enough to trip it. Only the submit was
retried on a 429 — every read (status, receipt, preview, download) ended the file at once.

Now a read waits out a 429 too (the server's Retry-After when there is one, a growing pause
when there is not), polling slows down once the limit has been hit, and a limit that does not
let go ends with a sentence that says what to do about it.
"""
import http.server
import json
import socketserver
import threading

import pytest

from kaeris import client as kc
from kaeris.client import KaerisClient, KaerisError

EDGE_429 = b"<!DOCTYPE html><html class='no-js ie6 oldie'><title>Rate limited</title>error code: 1015</html>"


def _server(answers):
    """answers: list of (code, headers); the last one repeats."""
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            code, headers = answers[min(len(hits), len(answers) - 1)]
            hits.append(code)
            if code == 200:
                body, ctype = json.dumps({"status": "done", "progress": 1, "total": 1}).encode(), \
                    "application/json"
            else:
                body, ctype = EDGE_429, "text/html"
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", hits


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr(kc.time, "sleep", slept.append)
    return slept


def test_a_rate_limited_status_poll_waits_and_carries_on(waits):
    srv, url, hits = _server([(429, {}), (429, {}), (200, {})])
    try:
        status = KaerisClient(api_url=url).poll("j1", interval=0)
    finally:
        srv.shutdown()
    assert status["status"] == "done"
    assert hits == [429, 429, 200]
    assert len(waits) == 2 and all(w >= 1 for w in waits)


def test_the_servers_retry_after_is_honoured(waits):
    srv, url, _ = _server([(429, {"Retry-After": "7"}), (200, {})])
    try:
        KaerisClient(api_url=url).poll("j1", interval=0)
    finally:
        srv.shutdown()
    assert 7 <= waits[0] < 10


def test_a_limit_that_does_not_let_go_says_what_to_do(waits):
    srv, url, hits = _server([(429, {})])
    try:
        with pytest.raises(KaerisError) as e:
            KaerisClient(api_url=url).poll("j1", interval=0)
    finally:
        srv.shutdown()
    msg = str(e.value)
    assert "429" in msg
    assert "--jobs" in msg and "at the same time" in msg, msg
    assert sum(waits) <= 5 * 60, "a read must not wait forever on a limit"


def test_polling_slows_down_once_the_limit_was_hit(waits):
    srv, url, _ = _server([(429, {"Retry-After": "1"}), (200, {})])
    client = KaerisClient(api_url=url)
    try:
        client.poll("j1", interval=1.0)
    finally:
        srv.shutdown()
    assert client.poll_interval(1.0) > 1.0
