"""The person who paid for the run must not be a stranger to its receipt.

/api/receipt used to answer anyone holding the job id, so it gave away the OWNER's plan,
spend, app context and glossary to whoever had the read-only share link (found on production
15.08.2026). The server now returns that half only to whoever presents the job's edit_token
— and this client used to read `job_id` out of the submit response and drop the token on the
floor, which would have quietly demoted every CLI user to a stranger to their own run: no
plan line, no spend, no "this glossary term was never in your file" warning.

Proven against a real HTTP server, not a mocked one: the header has to survive _headers(),
the opener and the redirect guard, and a unit test of the string would not have noticed if it
never reached the wire.
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris.client import KaerisClient

JOB = "b49d4c27-0e60-4b90-8e2b-b5af1cf9377c"
TOKEN = "240f0c9b02104a46974f70abcc1a64fd"


def _server(seen):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._json({"job_id": JOB, "edit_token": TOKEN})

        def do_GET(self):
            seen.append(self.headers.get("X-Job-Token"))
            self._json({"job_id": JOB, "status": "done"})

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class TestReceiptOwnership(unittest.TestCase):
    def test_the_token_from_submit_reaches_the_receipt_request(self):
        seen = []
        srv, port = _server(seen)
        try:
            c = KaerisClient(api_url=f"http://127.0.0.1:{port}")
            job = c.submit("en.json", b'{"a":"b"}', ["de"])
            self.assertEqual(job, JOB)
            self.assertEqual(c.last_edit_token, TOKEN)   # kept, not discarded
            c.receipt(job)
        finally:
            srv.shutdown()
        self.assertEqual(seen, [TOKEN])

    def test_a_client_that_did_not_start_the_job_sends_no_token(self):
        """Someone reading a shared job asks as a stranger — and must not send an empty
        header that a server could mistake for a token comparison against an empty one."""
        seen = []
        srv, port = _server(seen)
        try:
            KaerisClient(api_url=f"http://127.0.0.1:{port}").receipt(JOB)
        finally:
            srv.shutdown()
        self.assertEqual(seen, [None])

    def test_an_explicit_token_wins_over_the_remembered_one(self):
        seen = []
        srv, port = _server(seen)
        try:
            c = KaerisClient(api_url=f"http://127.0.0.1:{port}")
            c.submit("en.json", b'{"a":"b"}', ["de"])
            c.receipt(JOB, edit_token="another-token")
        finally:
            srv.shutdown()
        self.assertEqual(seen, ["another-token"])


if __name__ == "__main__":
    unittest.main()
