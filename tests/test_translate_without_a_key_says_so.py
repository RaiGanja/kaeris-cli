import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from kaeris import cli
from kaeris.client import KaerisError


class _Stub:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def config(self):
        return {"max_langs_anon": 8}

    def key_info(self):
        return {"tier": "anonymous", "char_limit": 10000}

    def __getattr__(self, name):
        raise KaerisError("stub: " + name)


def _run(pre=(), env_key=None):
    with tempfile.TemporaryDirectory() as t:
        src = os.path.join(t, "en.json")
        with open(src, "w", encoding="utf-8") as f:
            f.write('{"a": "Hello"}')
        args = cli.build_parser().parse_args(list(pre) + ["translate", src, "--langs", "de", "--out", t])
        env = {k: v for k, v in os.environ.items() if k != "KAERIS_API_KEY"}
        if env_key:
            env["KAERIS_API_KEY"] = env_key
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli, "_client", side_effect=lambda a: _Stub(a.key or os.environ.get("KAERIS_API_KEY"))), \
                contextlib.redirect_stderr(buf):
            cli.cmd_translate(args)
    return buf.getvalue()


class TestNoKeyNotice(unittest.TestCase):
    def test_help_mentions_the_demo(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["translate", "--help"])
        self.assertIn("without a key", buf.getvalue().lower())

    def test_run_without_key_prints_limits_from_the_server(self):
        text = _run()
        self.assertIn("no key: free demo limits", text)
        self.assertIn("10,000", text)
        self.assertIn("8 languages", text)
        self.assertIn("kaeris.dev/developer", text)

    def test_run_with_key_is_silent_about_it(self):
        self.assertNotIn("free demo", _run(["--key", "kaerisp_x"]))
        self.assertNotIn("free demo", _run(env_key="kaerisp_x"))


if __name__ == "__main__":
    unittest.main()
