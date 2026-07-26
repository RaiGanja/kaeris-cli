"""A redirect could hand the user's paid key to another server.

The CLI sends credentials as X-API-Key / X-OpenRouter-Key headers. urllib re-sends custom
headers to wherever a redirect points — Python strips Authorization across hosts, but it has
no reason to know ours are secrets. Proven by running it against a local server: a 302 to a
different port handed over `kaerisp_SECRET` in full.

That needs no attack on us to matter — a typo in --api-url, a captive portal, a corporate
proxy or a hijacked DNS record is enough. The client now refuses to follow a redirect that
changes host, port, or downgrades https to http, and says why.
"""
import http.server
import os
import socketserver
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris.client import KaerisClient, KaerisError, _SameHostRedirect


def _serve(handler_cls):
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class TestRedirectGuard(unittest.TestCase):
    def test_a_redirect_to_another_server_is_refused_and_nothing_leaks(self):
        received = {}

        class Catcher(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                received["key"] = self.headers.get("X-Api-Key")
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        catcher, catcher_port = _serve(Catcher)

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{catcher_port}/steal")
                self.end_headers()

            def log_message(self, *a):
                pass

        redirector, redirector_port = _serve(Redirector)
        try:
            client = KaerisClient(api_url=f"http://127.0.0.1:{redirector_port}",
                                  api_key="kaerisp_SECRET", timeout=10)
            with self.assertRaises(KaerisError) as caught:
                client.languages()
            self.assertIn("different server", str(caught.exception))
            self.assertIsNone(received.get("key"), "the API key reached the other server")
        finally:
            catcher.shutdown()
            redirector.shutdown()

    def test_a_redirect_on_the_same_endpoint_still_works(self):
        """Path-level redirects are ordinary and must keep working."""
        state = {}

        class Same(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/languages":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{state['port']}/moved")
                    self.end_headers()
                else:
                    body = b'{"ok":1}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv, port = _serve(Same)
        state["port"] = port
        try:
            client = KaerisClient(api_url=f"http://127.0.0.1:{port}", api_key="k", timeout=10)
            self.assertEqual(client.languages(), {"ok": 1})
        finally:
            srv.shutdown()

    def test_endpoint_comparison_covers_host_port_and_default_ports(self):
        ep = _SameHostRedirect._endpoint
        self.assertEqual(ep("https://kaeris.dev/api"), ("https", "kaeris.dev", 443))
        self.assertEqual(ep("http://kaeris.dev/api"), ("http", "kaeris.dev", 80))
        self.assertEqual(ep("https://KAERIS.dev:8443/x"), ("https", "kaeris.dev", 8443))

    def test_https_to_http_downgrade_is_refused(self):
        """Same hostname, but the key would travel in clear text.

        In practice the endpoint check fires first — https defaults to 443 and http to 80, so
        the pair already differs — and the explicit scheme check covers the case where a
        redirect keeps the port and only drops TLS. Both refuse; this asserts the refusal
        rather than which sentence explains it.
        """
        handler = _SameHostRedirect()

        class _Req:
            full_url = "https://kaeris.dev/api/languages"

            def get_full_url(self):
                return self.full_url

        with self.assertRaises(KaerisError):
            handler.redirect_request(_Req(), None, 302, "Found", {},
                                     "http://kaeris.dev/api/languages")

        # Same port, TLS dropped — this is the case the scheme check exists for.
        class _ReqPort:
            full_url = "https://kaeris.dev:8443/api/languages"

            def get_full_url(self):
                return self.full_url

        with self.assertRaises(KaerisError) as caught:
            handler.redirect_request(_ReqPort(), None, 302, "Found", {},
                                     "http://kaeris.dev:8443/api/languages")
        self.assertIn("unencrypted", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
