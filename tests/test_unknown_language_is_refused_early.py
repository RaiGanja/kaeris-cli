import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from kaeris import cli
from kaeris.client import KaerisError

LANGS = {"de": "German", "ja": "Japanese", "pt": "Portuguese", "zh-Hant": "Chinese (Traditional)"}


class _Stub:
    api_key = "kaerisp_x"

    def __init__(self, langs=LANGS, fail=False):
        self._langs, self._fail = langs, fail
        self.__dict__["sent"] = False

    def languages(self):
        if self._fail:
            raise KaerisError("offline")
        return self._langs

    def __getattr__(self, name):
        # Anything past the language check means a file was about to be sent.
        self.__dict__["sent"] = True
        raise KaerisError("stub: " + name)


def _run(langs, stub):
    with tempfile.TemporaryDirectory() as t:
        src = os.path.join(t, "en.json")
        with open(src, "w", encoding="utf-8") as f:
            f.write('{"a": "Hello"}')
        args = cli.build_parser().parse_args(["translate", src, "--langs", langs, "--out", t])
        buf = io.StringIO()
        with mock.patch.object(cli, "_client", return_value=stub), contextlib.redirect_stderr(buf):
            code = cli.cmd_translate(args)
    return code, buf.getvalue()


class TestUnknownLanguage(unittest.TestCase):
    def test_a_language_the_server_does_not_offer_is_refused_before_sending(self):
        stub = _Stub()
        code, text = _run("xx", stub)
        self.assertEqual(code, 1)
        self.assertIn("Unknown language code: xx", text)
        self.assertIn("kaeris languages", text)
        self.assertFalse(stub.sent)

    def test_every_unknown_code_is_named(self):
        _, text = _run("de,xx,qq", _Stub())
        self.assertIn("xx, qq", text)

    def test_a_regional_spelling_of_an_offered_language_passes(self):
        _, text = _run("pt-BR,zh-Hant,ja", _Stub())
        self.assertNotIn("Unknown language", text)

    def test_nothing_is_refused_when_the_list_cannot_be_fetched(self):
        _, text = _run("xx", _Stub(fail=True))
        self.assertNotIn("Unknown language", text)

    def test_key_help_warns_about_shell_history(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--help"])
        self.assertIn("shell history", " ".join(buf.getvalue().split()))


if __name__ == "__main__":
    unittest.main()
