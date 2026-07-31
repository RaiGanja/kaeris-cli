# cli/tests/test_cli_glossary_context.py
"""The glossary and the app context, as a developer reaches them from the terminal.

Two things this pins down:

1. Regression: `kaeris check` read the glossary from a config key ("glossary") that
   `kaeris init` never writes and the config's own documentation never mentions — the
   documented, generated key is "keep". So every project with a working kaeris.json
   translated with a glossary and then checked WITHOUT one: the dropped-term detector
   was silently off in CI, which is exactly where it was supposed to run.

2. The app context (a short description of what the app is) reached the API from the web
   form but had no way in from the CLI, and neither did a flag literally named
   --glossary. Both are advertised product features, so both must exist where a
   developer actually works. The context also changes what the model produces, so the
   incremental lock has to treat a changed context like a changed tone: retranslate the
   locale instead of mixing two contexts in one file.
"""
import json
import os
import tempfile
import unittest

from kaeris import cli
from kaeris import incremental as inc
from kaeris.client import KaerisClient


def _write(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class _Project:
    """A tiny project whose German translation dropped a glossary term."""

    def __init__(self, tmp, config):
        self.dir = tmp
        self.src = os.path.join(tmp, "locales", "en.json")
        _write({"signin": "Sign in with GitHub"}, self.src)
        _write({"signin": "Anmelden mit Octocat"},
               os.path.join(tmp, "locales", "de.json"))
        self.config = os.path.join(tmp, "kaeris.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump(config, f)


class TestCheckReadsDocumentedGlossaryKey(unittest.TestCase):
    def _check(self, config, extra_argv=()):
        with tempfile.TemporaryDirectory() as t:
            p = _Project(t, config)
            args = cli.build_parser().parse_args(
                ["--config", p.config, "check", "--json", *extra_argv])
            return cli.cmd_check(args)

    def _base(self, **extra):
        cfg = {"source": "locales/en.json", "langs": ["de"], "out": "locales"}
        cfg.update(extra)
        return cfg

    def test_keep_from_config_catches_dropped_term(self):
        # "keep" is what `kaeris init` writes and what the config docs describe.
        cfg = self._base(keep=["GitHub"])
        with tempfile.TemporaryDirectory() as t:
            p = _Project(t, cfg)
            cfg["source"] = p.src
            cfg["out"] = os.path.join(t, "locales")
            with open(p.config, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            args = cli.build_parser().parse_args(
                ["--config", p.config, "check", "--json"])
            self.assertEqual(cli.cmd_check(args), 1)

    def test_legacy_glossary_key_still_honored(self):
        # Anyone who found the old undocumented key must not break on upgrade.
        with tempfile.TemporaryDirectory() as t:
            cfg = self._base(glossary=["GitHub"])
            p = _Project(t, cfg)
            cfg["source"], cfg["out"] = p.src, os.path.join(t, "locales")
            with open(p.config, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            args = cli.build_parser().parse_args(
                ["--config", p.config, "check", "--json"])
            self.assertEqual(cli.cmd_check(args), 1)

    def test_no_glossary_configured_passes(self):
        # The guard must be off when nothing was configured — otherwise the failure
        # above proves nothing about the glossary.
        with tempfile.TemporaryDirectory() as t:
            cfg = self._base()
            p = _Project(t, cfg)
            cfg["source"], cfg["out"] = p.src, os.path.join(t, "locales")
            with open(p.config, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            args = cli.build_parser().parse_args(
                ["--config", p.config, "check", "--json"])
            self.assertEqual(cli.cmd_check(args), 0)

    def test_glossary_flag_on_check_without_any_config(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            _write({"signin": "Sign in with GitHub"}, src)
            _write({"signin": "Anmelden mit Octocat"}, os.path.join(t, "de.json"))
            args = cli.build_parser().parse_args(
                ["check", "--source", src, "--langs", "de", "--out", t,
                 "--glossary", "GitHub", "--json"])
            self.assertEqual(cli.cmd_check(args), 1)


class TestGlossaryFlagOnTranslate(unittest.TestCase):
    def test_glossary_is_an_alias_of_keep(self):
        args = cli.build_parser().parse_args(
            ["translate", "--glossary", "KAERIS, GitHub"])
        self.assertEqual(cli._glossary(args), ["KAERIS", "GitHub"])

    def test_keep_still_works(self):
        args = cli.build_parser().parse_args(["translate", "--keep", "KAERIS"])
        self.assertEqual(cli._glossary(args), ["KAERIS"])


class TestAppContext(unittest.TestCase):
    def test_multipart_carries_app_context(self):
        c = KaerisClient(api_url="https://example.invalid", api_key="k")
        body, _ = c._multipart("en.json", b"{}", ["de"],
                               app_context="a bank app for teenagers")
        self.assertIn(b'name="app_context"', body)
        self.assertIn(b"a bank app for teenagers", body)

    def test_multipart_omits_empty_app_context(self):
        c = KaerisClient(api_url="https://example.invalid", api_key="k")
        body, _ = c._multipart("en.json", b"{}", ["de"])
        self.assertNotIn(b'name="app_context"', body)

    def test_context_flag_reaches_the_client(self):
        args = cli.build_parser().parse_args(
            ["translate", "--context", "a bank app for teenagers"])
        self.assertEqual(cli._app_context(args), "a bank app for teenagers")

    def test_context_is_capped_at_the_api_limit(self):
        # The API truncates to 300 chars; sending more just wastes the upload.
        args = cli.build_parser().parse_args(["translate", "--context", "x" * 500])
        self.assertEqual(len(cli._app_context(args)), 300)

    def test_changed_context_retranslates_the_locale(self):
        before = inc.settings_signature(app_context="a bank app")
        after = inc.settings_signature(app_context="a game for kids")
        self.assertTrue(inc.settings_changed(before, after))

    def test_same_context_does_not_retranslate(self):
        sig = inc.settings_signature(app_context="a bank app")
        self.assertFalse(inc.settings_changed(sig, dict(sig)))


class TestFlagsReachTheSubmit(unittest.TestCase):
    """Parsing a flag is not delivering it. These run the real `translate` command against
    the stub client and assert on what was actually put on the wire."""

    def setUp(self):
        from test_cli_f15 import FakeClient
        self.Fake = FakeClient
        FakeClient.calls = []
        self._real = cli.KaerisClient
        cli.KaerisClient = FakeClient
        cli._target_path.warned = False

    def tearDown(self):
        cli.KaerisClient = self._real

    def _translate(self, *extra):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            _write({"signin": "Sign in with GitHub"}, src)
            args = cli.build_parser().parse_args(
                ["translate", src, "--langs", "de", "--out", t, "--quiet", *extra])
            cli.cmd_translate(args)
        return self.Fake.calls[-1]

    def test_context_flag_is_submitted(self):
        self.assertEqual(self._translate("--context", "a bank app")["app_context"],
                         "a bank app")

    def test_glossary_flag_is_submitted(self):
        self.assertEqual(self._translate("--glossary", "GitHub")["glossary"], ["GitHub"])

    def test_context_from_config_is_submitted(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "en.json")
            _write({"signin": "Sign in with GitHub"}, src)
            cfg = os.path.join(t, "kaeris.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"source": src, "langs": ["de"], "out": t,
                           "context": "a bank app"}, f)
            args = cli.build_parser().parse_args(
                ["--config", cfg, "translate", "--quiet"])
            cli.cmd_translate(args)
        self.assertEqual(self.Fake.calls[-1]["app_context"], "a bank app")

    def test_nothing_is_submitted_when_not_asked_for(self):
        call = self._translate()
        self.assertEqual(call["app_context"], "")
        self.assertEqual(call["glossary"], [])


if __name__ == "__main__":
    unittest.main()
