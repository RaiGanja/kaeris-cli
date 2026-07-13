"""Tests for F15 — CLI multi-source / glob / namespace-aware output, and
--only-new extended to ARB. Stdlib unittest, no network: KaerisClient is
replaced with a FakeClient stub so nothing ever touches the real API."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli
from kaeris import incremental as inc


class FakeClient:
    """Stub for KaerisClient.submit/poll/download/preview — records every
    submit() call (filename/content/languages) and echoes the submitted
    content back as the "translation" on download(), so tests can assert on
    both what was sent and what got written."""

    calls = []

    def __init__(self, *a, **kw):
        pass

    def submit(self, filename, content, languages, glossary=None, verify=False,
               back_lang="en", tone="", icu=False, reuse=None):
        FakeClient.calls.append({
            "filename": filename,
            "content": content,
            "languages": list(languages),
        })
        return f"job-{len(FakeClient.calls)}"

    def poll(self, job_id, on_progress=None, interval=1.0, max_wait=1800):
        return {"status": "done", "failed_langs": []}

    def download(self, job_id):
        idx = int(job_id.rsplit("-", 1)[1]) - 1
        call = FakeClient.calls[idx]
        ext = os.path.splitext(call["filename"])[1]
        return {f"{lang}{ext}": call["content"] for lang in call["languages"]}

    def preview(self, job_id):
        return {}


class FailingClient(FakeClient):
    """Like FakeClient but reports any language in `fail_langs` as a failed
    (fell-back-to-source) translation, so tests can exercise the not-merged path."""

    fail_langs = set()

    def poll(self, job_id, on_progress=None, interval=1.0, max_wait=1800):
        idx = int(job_id.rsplit("-", 1)[1]) - 1
        langs = FakeClient.calls[idx]["languages"]
        return {"status": "done",
                "failed_langs": [l for l in langs if l in FailingClient.fail_langs]}


class ClientStubMixin:
    def setUp(self):
        FakeClient.calls = []
        self._real_client = cli.KaerisClient
        cli.KaerisClient = FakeClient
        cli._target_path.warned = False

    def tearDown(self):
        cli.KaerisClient = self._real_client


# ── _target_path ────────────────────────────────────────────────────────────

class TestTargetPath(unittest.TestCase):
    def setUp(self):
        cli._target_path.warned = False

    def test_flat_layout(self):
        dest = cli._target_path("locales/en.json", "de", None, "en")
        self.assertEqual(dest, os.path.join("locales", "de.json"))

    def test_flat_layout_with_out(self):
        dest = cli._target_path("locales/en.json", "de", "build", "en")
        self.assertEqual(dest, os.path.join("build", "de.json"))

    def test_namespace_layout(self):
        dest = cli._target_path("locales/en/common.json", "de", None, "en")
        self.assertEqual(dest, os.path.join("locales", "de", "common.json"))

    def test_namespace_layout_with_out_rebase(self):
        dest = cli._target_path("locales/en/common.json", "de", "build", "en")
        self.assertEqual(dest, os.path.join("build", "de", "common.json"))

    def test_fallback_layout_nests_by_lang(self):
        dest = cli._target_path("strings.json", "de", None, "en")
        self.assertEqual(dest, os.path.join("de", "strings.json"))

    def test_fallback_layout_with_out(self):
        dest = cli._target_path("strings.json", "de", "build", "en")
        self.assertEqual(dest, os.path.join("build", "de", "strings.json"))


# ── _resolve_sources: glob expansion + config list ─────────────────────────

class TestResolveSources(unittest.TestCase):
    def test_glob_expansion_dedup_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            en_dir = os.path.join(tmp, "locales", "en")
            os.makedirs(en_dir)
            common = os.path.join(en_dir, "common.json")
            auth = os.path.join(en_dir, "auth.json")
            for p in (common, auth):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"k": "v"}, f)

            pattern = os.path.join(tmp, "locales", "en", "*.json")
            files = cli._resolve_sources(pattern)
            files2 = cli._resolve_sources([pattern, pattern])  # dedupe check

            self.assertEqual(files, sorted([common, auth]))
            self.assertEqual(files2, files)

    def test_config_source_as_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.json")
            b = os.path.join(tmp, "b.json")
            for p in (a, b):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"k": "v"}, f)
            missing = os.path.join(tmp, "missing.json")

            files = cli._resolve_sources([b, a, missing])
            self.assertEqual(files, sorted([a, b]))  # missing file dropped

    def test_single_literal_path_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "en.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"k": "v"}, f)
            self.assertEqual(cli._resolve_sources(p), [p])


# ── end-to-end cmd_translate: glob + namespace output ──────────────────────

class TestCmdTranslateGlobAndNamespace(ClientStubMixin, unittest.TestCase):
    def test_multi_file_glob_writes_namespace_mirrored_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            en_dir = os.path.join(tmp, "locales", "en")
            os.makedirs(en_dir)
            with open(os.path.join(en_dir, "common.json"), "w", encoding="utf-8") as f:
                json.dump({"hello": "Hello"}, f)
            with open(os.path.join(en_dir, "auth.json"), "w", encoding="utf-8") as f:
                json.dump({"login": "Log in"}, f)

            pattern = os.path.join(tmp, "locales", "en", "*.json")
            parser = cli.build_parser()
            args = parser.parse_args(
                ["translate", pattern, "--langs", "de", "--source-lang", "en", "--quiet"]
            )
            code = cli.cmd_translate(args)

            self.assertEqual(code, 0)
            de_common = os.path.join(tmp, "locales", "de", "common.json")
            de_auth = os.path.join(tmp, "locales", "de", "auth.json")
            self.assertTrue(os.path.isfile(de_common), de_common)
            self.assertTrue(os.path.isfile(de_auth), de_auth)
            # two separate jobs, one per source file
            self.assertEqual(len(FakeClient.calls), 2)

    def test_config_source_list_resolves_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                os.makedirs("locales/en")
                with open("locales/en/common.json", "w", encoding="utf-8") as f:
                    json.dump({"hello": "Hello"}, f)
                with open("locales/en/auth.json", "w", encoding="utf-8") as f:
                    json.dump({"login": "Log in"}, f)
                with open(cli.CONFIG_FILENAME, "w", encoding="utf-8") as f:
                    json.dump({
                        "source": ["locales/en/common.json", "locales/en/auth.json"],
                        "source_lang": "en",
                        "langs": ["de"],
                    }, f)

                parser = cli.build_parser()
                args = parser.parse_args(["translate", "--quiet"])
                code = cli.cmd_translate(args)

                self.assertEqual(code, 0)
                self.assertTrue(os.path.isfile("locales/de/common.json"))
                self.assertTrue(os.path.isfile("locales/de/auth.json"))
                self.assertEqual(len(FakeClient.calls), 2)
            finally:
                os.chdir(cwd)


# ── --only-new extended to ARB ──────────────────────────────────────────────

class TestOnlyNewArb(ClientStubMixin, unittest.TestCase):
    def test_only_new_arb_submits_only_missing_keys_and_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            en_path = os.path.join(tmp, "en.arb")
            de_path = os.path.join(tmp, "de.arb")
            inc.dump_json({"greeting": "Hello", "farewell": "Bye", "note": "New note"}, en_path)
            inc.dump_json({"greeting": "Hallo", "farewell": "Tschuess"}, de_path)

            parser = cli.build_parser()
            args = parser.parse_args(
                ["translate", en_path, "--langs", "de", "--only-new", "--quiet"]
            )
            code = cli.cmd_translate(args)

            self.assertEqual(code, 0)
            # exactly one job submitted (only the missing key), for the .arb file
            self.assertEqual(len(FakeClient.calls), 1)
            call = FakeClient.calls[0]
            self.assertEqual(call["filename"], "en.arb")
            self.assertEqual(call["languages"], ["de"])
            submitted = json.loads(call["content"].decode("utf-8"))
            self.assertEqual(submitted, {"note": "New note"})  # subset only, no re-send

            merged = inc.load_json(de_path)
            self.assertEqual(merged["greeting"], "Hallo")       # untouched existing translation
            self.assertEqual(merged["farewell"], "Tschuess")    # untouched existing translation
            self.assertEqual(merged["note"], "New note")        # newly merged (echoed by FakeClient)

    def test_arb_metadata_excluded_from_submit_and_locale_stamped(self):
        """I1: ARB @-metadata must never be sent to the model; @@locale is
        stamped to the target and existing @key metadata survives the merge."""
        with tempfile.TemporaryDirectory() as tmp:
            en_path = os.path.join(tmp, "en.arb")
            de_path = os.path.join(tmp, "de.arb")
            inc.dump_json({
                "@@locale": "en",
                "itemCount": "{count} items",
                "@itemCount": {"placeholders": {"count": {"type": "int"}}},
                "greeting": "Hello",
            }, en_path)
            # partial target: carries @@locale + @itemCount metadata but no
            # translated leaves yet -> both greeting & itemCount are "missing".
            inc.dump_json({
                "@@locale": "de",
                "@itemCount": {"placeholders": {"count": {"type": "int"}}},
            }, de_path)

            parser = cli.build_parser()
            args = parser.parse_args(
                ["translate", en_path, "--langs", "de", "--only-new", "--quiet"]
            )
            code = cli.cmd_translate(args)
            self.assertEqual(code, 0)

            self.assertEqual(len(FakeClient.calls), 1)
            submitted = json.loads(FakeClient.calls[0]["content"].decode("utf-8"))
            self.assertIn("greeting", submitted)
            self.assertIn("itemCount", submitted)
            self.assertNotIn("@@locale", submitted)      # metadata NOT translatable
            self.assertNotIn("@itemCount", submitted)

            merged = inc.load_json(de_path)
            self.assertEqual(merged["@@locale"], "de")   # stamped to the target lang
            self.assertEqual(merged["@itemCount"],       # metadata preserved unchanged
                             {"placeholders": {"count": {"type": "int"}}})
            self.assertEqual(merged["greeting"], "Hello")        # echoed by FakeClient
            self.assertEqual(merged["itemCount"], "{count} items")

    def test_only_new_second_run_is_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            en_path = os.path.join(tmp, "en.arb")
            de_path = os.path.join(tmp, "de.arb")
            inc.dump_json({"greeting": "Hello"}, en_path)
            inc.dump_json({"greeting": "Hallo"}, de_path)

            parser = cli.build_parser()
            args = parser.parse_args(
                ["translate", en_path, "--langs", "de", "--only-new", "--quiet"]
            )
            code = cli.cmd_translate(args)

            self.assertEqual(code, 0)
            self.assertEqual(len(FakeClient.calls), 0)  # nothing missing -> no API call


# ── C1: multi-language lock poisoning (edited key must reach every lang) ─────

class TestIncrementalLockPoisoning(ClientStubMixin, unittest.TestCase):
    @staticmethod
    def _setup(tmp):
        en = os.path.join(tmp, "en.json")
        de = os.path.join(tmp, "de.json")
        fr = os.path.join(tmp, "fr.json")
        lock = os.path.join(tmp, "kaeris.lock")
        inc.dump_json({"greeting": "Hi there"}, en)   # source was EDITED from "Hello"
        inc.dump_json({"greeting": "Hallo"}, de)
        inc.dump_json({"greeting": "Bonjour"}, fr)
        old_hash = inc.hash_value("Hello")             # lock still records old text
        inc.dump_lock({"greeting": old_hash}, lock)
        return en, de, fr, lock, old_hash

    def test_edited_key_retranslated_for_all_langs_not_just_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            en, de, fr, lock, _ = self._setup(tmp)
            parser = cli.build_parser()
            args = parser.parse_args(
                ["translate", en, "--langs", "de,fr", "--only-new", "--quiet"]
            )
            code = cli.cmd_translate(args)
            self.assertEqual(code, 0)

            submitted_langs = [c["languages"][0] for c in FakeClient.calls]
            self.assertIn("de", submitted_langs)
            self.assertIn("fr", submitted_langs)   # fr NOT skipped — the C1 bug
            for c in FakeClient.calls:
                self.assertIn("greeting", json.loads(c["content"].decode("utf-8")))

    def test_failed_lang_leaves_key_stale_locked_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            en, de, fr, lock, old_hash = self._setup(tmp)
            cli.KaerisClient = FailingClient
            FailingClient.fail_langs = {"fr"}
            self.addCleanup(setattr, FailingClient, "fail_langs", set())

            parser = cli.build_parser()
            args = parser.parse_args(
                ["translate", en, "--langs", "de,fr", "--only-new", "--quiet"]
            )
            cli.cmd_translate(args)

            # fr failed to merge -> greeting must stay stale-locked so a re-run retries fr
            lock_after = inc.load_lock(lock)
            self.assertEqual(lock_after["greeting"], old_hash)


# ── backward compat: single flat-en.json run must behave exactly as before ──

class TestBackwardCompatSingleFile(ClientStubMixin, unittest.TestCase):
    def test_single_flat_file_single_lang_default_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "locales", "en.json")
            os.makedirs(os.path.dirname(src))
            with open(src, "w", encoding="utf-8") as f:
                json.dump({"hi": "Hi"}, f)

            parser = cli.build_parser()
            args = parser.parse_args(["translate", src, "--langs", "de", "--quiet"])
            code = cli.cmd_translate(args)

            self.assertEqual(code, 0)
            # default out dir = source's own dir; filename is <lang>.json
            expected = os.path.join(tmp, "locales", "de.json")
            self.assertTrue(os.path.isfile(expected), expected)
            self.assertEqual(len(FakeClient.calls), 1)
            self.assertEqual(FakeClient.calls[0]["languages"], ["de"])


if __name__ == "__main__":
    unittest.main()
