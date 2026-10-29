"""`kaeris translate --dry-run`: the bill before the run.

`kaeris quota` shows what is left AFTER the money is spent; people were afraid of a bill they
could not see in advance. --dry-run prices the run the way the server does and says what is
left, without translating or sending anything.

The pricing rule is the server's (backend/main.py `_meter_cost_one`): the characters actually
sent to the model, plus twice the whole job when verify is on. The pricing page's own example
is a test below: 10,000 characters into 5 languages is 50,000, or 150,000 with the meaning check.

Every test drives main(argv), and the stub client refuses `submit`: a dry run that sent a file
would fail here, not in somebody's invoice.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli as K                      # noqa: E402
from kaeris import incremental as inc            # noqa: E402
from kaeris import tm                            # noqa: E402
from kaeris.client import KaerisError            # noqa: E402

PRO = {"tier": "premium", "model_id": "m1", "char_limit": 100000, "month_cap": 15_000_000,
       "month_used": 12_345, "month_remaining": 14_987_655, "month_resets": "2026-12-01"}
ANON = {"tier": "anonymous", "model_id": "m1", "char_limit": 10000}
FREE = {"tier": "free", "model_id": "m1", "char_limit": 50000, "month_cap": 1_500_000,
        "month_used": 1_499_000, "month_remaining": 1_000, "month_resets": "2026-12-01"}
BYOK = {"tier": "byok", "model_id": "m1", "char_limit": 1_000_000}
CONFIG = {"max_langs_anon": 8, "max_langs_free": 8, "verify_free_max_langs": 8,
          "byok_char_limit": 1_000_000}
LANGS = {"de": "German", "fr": "French", "es": "Spanish", "ja": "Japanese", "it": "Italian",
         "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "sv": "Swedish", "da": "Danish"}


class Server:
    """What the stub answers; the test rewrites it."""
    def __init__(self):
        self.key = dict(PRO)
        self.key_error = None
        self.config = dict(CONFIG)
        self.asked = []


@pytest.fixture()
def server(monkeypatch, tmp_path):
    srv = Server()

    class Stub:
        api_key = "k"
        openrouter_key = None

        def key_info(self):
            srv.asked.append("key_info")
            if srv.key_error:
                raise srv.key_error
            return srv.key

        def config(self):
            srv.asked.append("config")
            return srv.config

        def languages(self):
            return LANGS

        def submit(self, *a, **kw):
            raise AssertionError("a dry run sent a file to the translation API")

    monkeypatch.setattr(K, "_client", lambda args: Stub())
    monkeypatch.chdir(tmp_path)
    return srv


def write_json(path, obj):
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return str(path)


def chars(obj):
    return sum(len(v) for v in inc.flatten(obj).values())


def dry(argv, capsys):
    code = K.main(["translate", "--dry-run"] + argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def dry_json(argv, capsys):
    code, out, err = dry(argv + ["--json"], capsys)
    return code, json.loads(out), err


EN = {"app": {"title": "My Shop", "tagline": "Buy anything, anywhere"},
      "cart": {"one": "{count} item", "other": "{count} items"},
      "tips": ["Tap to open", "Swipe to delete"],
      "checkout": "Checkout"}


class TestThePriceOfAWholeFile:

    def test_characters_times_languages(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        code, rep, _ = dry_json([src, "--langs", "de,fr,es"], capsys)
        assert code == 0
        assert rep["characters"] == chars(EN) * 3
        assert rep["files"][0]["languages"]["de"]["charged"] == chars(EN)
        assert rep["files"][0]["jobs"] == [{"langs": ["de", "fr", "es"], "chars": chars(EN)}]

    def test_the_pricing_page_example(self, server, capsys, tmp_path):
        """'10,000 characters into 5 languages is 50,000, or 150,000 checked.'"""
        obj = {"a": "x" * 5000, "b": "y" * 5000}
        src = write_json(tmp_path / "en.json", obj)
        langs = "de,fr,es,ja,it"
        _, rep, _ = dry_json([src, "--langs", langs], capsys)
        assert rep["characters"] == 50_000
        _, rep, _ = dry_json([src, "--langs", langs, "--verify"], capsys)
        assert rep["characters"] == 150_000

    def test_it_says_what_is_left_and_what_will_be_left(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "en.json", EN)
        code, out, _ = dry([src, "--langs", "de,fr"], capsys)
        total = chars(EN) * 2
        assert code == 0
        assert f"{total:,} characters will be charged" in out
        assert "14,987,655 of 15,000,000 left" in out
        assert f"after this run {14_987_655 - total:,} left" in out
        assert "2026-12-01" in out

    def test_it_asks_the_server_what_quota_asks(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "en.json", EN)
        dry([src, "--langs", "de"], capsys)
        assert "key_info" in server.asked

    def test_it_writes_nothing_and_makes_no_output_folder(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "en.json", EN)
        before = sorted(os.listdir(str(tmp_path)))
        dry([src, "--langs", "de", "--out", "translated"], capsys)
        assert sorted(os.listdir(str(tmp_path))) == before

    def test_no_language_no_file_is_still_refused_like_a_real_run(self, server, capsys):
        code, _, err = dry([], capsys)
        assert code == 1
        assert "No source file" in err


class TestOnlyNewCountsOnlyWhatWouldBeSent:

    def test_a_partly_translated_target(self, server, capsys, tmp_path):
        """de has two of the four keys; only the other two are sent (and priced)."""
        src = write_json(tmp_path / "locales" / "en.json", EN)
        write_json(tmp_path / "locales" / "de.json",
                   {"app": {"title": "Mein Shop"}, "checkout": "Zur Kasse"})
        _, rep, _ = dry_json([src, "--langs", "de", "--only-new", "--assume-current"], capsys)
        row = rep["files"][0]["languages"]["de"]
        todo = {k: v for k, v in inc.flatten(EN).items() if k not in ("app.title", "checkout")}
        assert row["keys_to_translate"] == len(todo)
        assert row["keys_up_to_date"] == 2
        assert row["charged"] == sum(len(v) for v in todo.values())
        assert rep["files"][0]["mode"] == "only new and edited"

    def test_a_language_with_no_file_yet_is_the_whole_file(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        _, rep, _ = dry_json([src, "--langs", "fr", "--only-new"], capsys)
        assert rep["files"][0]["languages"]["fr"]["charged"] == chars(EN)

    def test_an_edited_string_is_found_through_the_lock(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        de = {"app": {"title": "Mein Shop", "tagline": "Alles"}, "cart": {"one": "a", "other": "b"},
              "tips": ["x", "y"], "checkout": "Kasse"}
        write_json(tmp_path / "locales" / "de.json", de)
        old = json.loads(json.dumps(EN))
        old["checkout"] = "Pay"                      # the source said this when it was translated
        lock = inc.build_lock(inc.hash_flat(inc.flatten(old)),
                              inc.settings_signature("", False, [], "m1", ""),
                              {"de": inc.hash_flat(inc.flatten(old))})
        inc.dump_lock(lock, str(tmp_path / "locales" / "kaeris.lock"))
        _, rep, _ = dry_json([src, "--langs", "de", "--only-new"], capsys)
        row = rep["files"][0]["languages"]["de"]
        assert row["keys_to_translate"] == 1
        assert row["charged"] == len("Checkout")

    def test_a_custom_lock_path_is_read(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        write_json(tmp_path / "locales" / "de.json", {"checkout": "Kasse"})
        old = {"checkout": "Pay"}
        lock = inc.build_lock(inc.hash_flat(old), inc.settings_signature("", False, [], "m1", ""),
                              {"de": inc.hash_flat(old)})
        inc.dump_lock(lock, str(tmp_path / "my.lock"))
        _, rep, _ = dry_json([src, "--langs", "de", "--only-new", "--lock",
                              str(tmp_path / "my.lock")], capsys)
        # checkout was edited; the three other groups are missing from de
        row = rep["files"][0]["languages"]["de"]
        assert row["keys_up_to_date"] == 0
        assert row["charged"] == chars(EN)

    def test_changed_settings_count_every_key(self, server, capsys, tmp_path):
        """A different tone re-translates the locale in a real run, so it is priced as one."""
        src = write_json(tmp_path / "locales" / "en.json", EN)
        write_json(tmp_path / "locales" / "de.json", EN)
        h = inc.hash_flat(inc.flatten(EN))
        inc.dump_lock(inc.build_lock(h, inc.settings_signature("", False, [], "m1", ""), {"de": h}),
                      str(tmp_path / "locales" / "kaeris.lock"))
        _, rep, _ = dry_json([src, "--langs", "de", "--only-new"], capsys)
        assert rep["files"][0]["languages"]["de"]["charged"] == 0
        _, rep, _ = dry_json([src, "--langs", "de", "--only-new", "--tone", "formal"], capsys)
        assert rep["files"][0]["languages"]["de"]["charged"] == chars(EN)
        assert "settings" in rep["files"][0]["note"]

    def test_everything_up_to_date_is_zero_and_exit_zero(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        write_json(tmp_path / "locales" / "de.json", EN)
        code, out, _ = dry([src, "--langs", "de", "--only-new", "--assume-current"], capsys)
        assert code == 0
        assert "Total: 0 characters" in out

    def test_a_job_per_language_in_incremental_mode(self, server, capsys, tmp_path):
        """The real run sends one job per language here, so the per-run language cap is not met."""
        src = write_json(tmp_path / "locales" / "en.json", EN)
        _, rep, _ = dry_json([src, "--langs", "de,fr", "--only-new"], capsys)
        assert [j["langs"] for j in rep["files"][0]["jobs"]] == [["de"], ["fr"]]


class TestTheProjectMemory:

    def _remember(self, tmp_path, pairs):
        sig = inc.settings_signature("", False, [], "m1", "")
        memory = {"version": tm.VERSION, "entries": {}}
        tm.record(memory, {k: k for k in pairs}, {"de": dict(pairs)}, sig)
        tm.save(str(tmp_path / "locales" / tm.FILENAME), memory, K.write_atomic)

    def test_remembered_text_is_not_charged(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        self._remember(tmp_path, {"Checkout": "Zur Kasse", "Tap to open": "Tippen"})
        _, rep, _ = dry_json([src, "--langs", "de,fr"], capsys)
        de, fr = rep["files"][0]["languages"]["de"], rep["files"][0]["languages"]["fr"]
        assert de["keys_reused"] == 2
        assert de["charged"] == chars(EN) - len("Checkout") - len("Tap to open")
        assert de["reused_characters"] == len("Checkout") + len("Tap to open")
        assert fr["charged"] == chars(EN), "memory is per language: fr was never translated"
        assert rep["reused_characters"] == de["reused_characters"]

    def test_no_tm_ignores_it(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        self._remember(tmp_path, {"Checkout": "Zur Kasse"})
        _, rep, _ = dry_json([src, "--langs", "de", "--no-tm"], capsys)
        assert rep["files"][0]["languages"]["de"]["charged"] == chars(EN)

    def test_tm_path_is_read(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "locales" / "en.json", EN)
        self._remember(tmp_path, {"Checkout": "Zur Kasse"})
        os.replace(str(tmp_path / "locales" / tm.FILENAME), str(tmp_path / "elsewhere.json"))
        _, rep, _ = dry_json([src, "--langs", "de", "--tm", str(tmp_path / "elsewhere.json")], capsys)
        assert rep["files"][0]["languages"]["de"]["keys_reused"] == 1

    def test_verify_still_costs_twice_the_whole_job(self, server, capsys, tmp_path):
        """backend `_meter_cost_one`: verify re-checks the whole job whether or not lines were
        reused, so it is 2 x char_count, not 2 x what was translated."""
        src = write_json(tmp_path / "locales" / "en.json", EN)
        self._remember(tmp_path, {"Checkout": "Zur Kasse"})
        _, rep, _ = dry_json([src, "--langs", "de", "--verify"], capsys)
        row = rep["files"][0]["languages"]["de"]
        assert row["translated_characters"] == chars(EN) - len("Checkout")
        assert row["verify_characters"] == 2 * chars(EN)
        assert row["charged"] == row["translated_characters"] + 2 * chars(EN)


class TestSeveralFilesAndFormats:

    def test_files_add_up(self, server, capsys, tmp_path):
        a = write_json(tmp_path / "a" / "en.json", EN)
        b = write_json(tmp_path / "b" / "en.json", {"x": "Hello"})
        _, rep, _ = dry_json([a, b, "--langs", "de,fr"], capsys)
        assert [f["charged"] for f in rep["files"]] == [chars(EN) * 2, 10]
        assert rep["characters"] == chars(EN) * 2 + 10

    def test_arb_metadata_is_not_counted(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "app_en.arb", {
            "@@locale": "en", "hello": "Hello {name}",
            "@hello": {"description": "a long description that is not translated"}})
        _, rep, _ = dry_json([src, "--langs", "de"], capsys)
        assert rep["characters"] == len("Hello {name}")

    def test_arrays_and_plural_keys_are_counted(self, server, capsys, tmp_path):
        obj = {"items_one": "one item", "items_other": "many items", "list": ["aa", "bbb"]}
        src = write_json(tmp_path / "en.json", obj)
        _, rep, _ = dry_json([src, "--langs", "de"], capsys)
        assert rep["characters"] == len("one item") + len("many items") + 5
        assert rep["files"][0]["keys"] == 4

    def test_markdown_is_counted_as_the_server_reads_it(self, server, capsys, tmp_path):
        """The server keeps the \\r of a CRLF file in every block, and counts it."""
        p = tmp_path / "en.md"
        p.write_bytes(b"# Title\r\n\r\nSome text.\r\n")
        _, rep, _ = dry_json([str(p), "--langs", "de"], capsys)
        assert rep["characters"] == len("Title\r") + len("Some text.\r")

    def test_android_plurals_are_counted_per_quantity(self, server, capsys, tmp_path):
        p = tmp_path / "values" / "strings.xml"
        os.makedirs(str(p.parent))
        p.write_text('<resources><plurals name="n"><item quantity="one">%d file</item>'
                     '<item quantity="other">%d files</item></plurals></resources>', encoding="utf-8")
        _, rep, _ = dry_json([str(p), "--langs", "de"], capsys)
        assert rep["files"][0]["keys"] == 2
        assert rep["characters"] == len("%d file") + len("%d files")

    def test_a_broken_file_is_named_and_the_rest_is_still_priced(self, server, capsys, tmp_path):
        good = write_json(tmp_path / "good" / "en.json", {"a": "Hello"})
        bad = tmp_path / "bad" / "en.json"
        os.makedirs(str(bad.parent))
        bad.write_text("{not json", encoding="utf-8")
        code, out, err = dry([str(bad), good, "--langs", "de"], capsys)
        assert code == 1
        assert "bad" in err
        assert "5 characters" in out

    def test_a_file_with_nothing_to_translate_is_an_error_not_zero(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "en.json", {"n": 1})
        code, _, err = dry([src, "--langs", "de"], capsys)
        assert code == 1
        assert "no translatable strings" in err


class TestWhatThePlanSays:

    def test_anonymous_limits_come_from_the_server(self, server, capsys, tmp_path):
        server.key = dict(ANON)
        big = write_json(tmp_path / "en.json", {"a": "x" * 12000})
        code, rep, _ = dry_json([big, "--langs", "de"], capsys)
        assert code == 0, "the estimate is the result; a refusal it predicts is not a failure"
        assert [p["code"] for p in rep["problems"]] == ["file_too_large"]
        assert "10,000" in rep["problems"][0]["message"]

    def test_too_many_languages_for_the_free_plan(self, server, capsys, tmp_path):
        server.key = dict(ANON)
        src = write_json(tmp_path / "en.json", EN)
        code, out, _ = dry([src, "--langs", ",".join(LANGS)], capsys)
        assert code == 0
        assert "would refuse" in out and "10 languages" in out and "up to 8" in out

    def test_verify_language_cap(self, server, capsys, tmp_path):
        server.config = dict(CONFIG, max_langs_anon=20, max_langs_free=20)
        server.key = dict(ANON)
        src = write_json(tmp_path / "en.json", EN)
        _, rep, _ = dry_json([src, "--langs", ",".join(LANGS), "--verify"], capsys)
        assert [p["code"] for p in rep["problems"]] == ["verify_languages"]

    def test_a_limit_the_server_did_not_give_is_not_invented(self, server, capsys, tmp_path):
        server.key = {"tier": "anonymous"}
        server.config = {}
        big = write_json(tmp_path / "en.json", {"a": "x" * 120000})
        _, rep, _ = dry_json([big, "--langs", ",".join(LANGS)], capsys)
        assert rep["problems"] == []

    def test_no_key_says_remaining_is_unknown(self, server, capsys, tmp_path):
        server.key = dict(ANON)
        src = write_json(tmp_path / "en.json", EN)
        _, rep, _ = dry_json([src, "--langs", "de"], capsys)
        assert rep["volume"]["remaining"] is None
        assert "unknown" in rep["volume"]["note"]
        _, out, _ = dry([src, "--langs", "de"], capsys)
        assert "remaining unknown" in out

    def test_a_run_that_does_not_fit_the_month(self, server, capsys, tmp_path):
        server.key = dict(FREE)
        src = write_json(tmp_path / "en.json", {"a": "x" * 600})
        code, rep, _ = dry_json([src, "--langs", "de,fr"], capsys)
        assert code == 0
        assert rep["volume"]["remaining"] == 1000
        assert rep["volume"]["after"] == -200
        assert rep["problems"][0]["code"] == "volume"

    def test_lifetime_counts_characters_and_has_no_volume(self, server, capsys, tmp_path):
        server.key = dict(BYOK)
        src = write_json(tmp_path / "en.json", EN)
        _, rep, _ = dry_json([src, "--langs", "de"], capsys)
        assert rep["characters"] == chars(EN)
        assert rep["volume"]["remaining"] is None and "no monthly volume" in rep["volume"]["note"]
        _, out, _ = dry([src, "--langs", "de"], capsys)
        assert "will be translated" in out and "will be charged" not in out

    def test_pro_with_an_own_openrouter_key_is_served_like_lifetime(self, server, capsys,
                                                                    tmp_path, monkeypatch):
        class Own:
            api_key, openrouter_key = "k", "sk-or-v1-xxxxxxxxxxxxxxxxxxxx"
            def key_info(self): return server.key
            def config(self): return server.config
            def languages(self): return LANGS
        monkeypatch.setattr(K, "_client", lambda args: Own())
        src = write_json(tmp_path / "en.json", {"a": "x" * 150000})
        _, rep, _ = dry_json([src, "--langs", "de"], capsys)
        assert rep["problems"] == []
        assert rep["volume"]["remaining"] is None

    def test_an_unreachable_server_still_gives_the_count(self, server, capsys, tmp_path):
        server.key_error = KaerisError("Cannot reach https://kaeris.dev: timed out")
        src = write_json(tmp_path / "en.json", EN)
        code, rep, err = dry_json([src, "--langs", "de"], capsys)
        assert code == 0
        assert rep["characters"] == chars(EN)
        assert rep["volume"]["remaining"] is None and "unknown" in rep["volume"]["note"]
        assert "unknown" in err

    def test_a_rejected_key_is_an_error_like_quota(self, server, capsys, tmp_path):
        e = KaerisError("HTTP 401: Invalid API key")
        e.code = 401
        server.key_error = e
        src = write_json(tmp_path / "en.json", EN)
        code, _, err = dry([src, "--langs", "de"], capsys)
        assert code == 2
        assert "Invalid API key" in err


class TestTheOutput:

    def test_the_text_is_plain_ascii(self, server, capsys, tmp_path):
        """A cp1251/cp1252 console raises on anything else."""
        src = write_json(tmp_path / "en.json", {"a": "Привет"})
        _, out, _ = dry([src, "--langs", "de"], capsys)
        out.encode("ascii")

    def test_json_is_only_json_on_stdout(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "en.json", EN)
        code, out, _ = dry([src, "--langs", "de", "--json"], capsys)
        rep = json.loads(out)
        assert code == 0 and rep["dry_run"] is True
        assert set(rep) >= {"characters", "reused_characters", "verify", "plan", "volume",
                            "files", "problems"}

    def test_json_without_dry_run_is_refused(self, server, capsys, tmp_path):
        src = write_json(tmp_path / "en.json", EN)
        code = K.main(["translate", src, "--langs", "de", "--json"])
        assert code == 2
        assert "--dry-run" in capsys.readouterr().err

    def test_the_flags_are_documented_in_plain_ascii(self):
        """Help is printed to a legacy console too (the --digits flag says why)."""
        sub = next(a for a in K.build_parser()._actions if getattr(a, "choices", None))
        helps = {opt: a.help for a in sub.choices["translate"]._actions
                 for opt in a.option_strings}
        for flag in ("--dry-run", "--json"):
            assert helps[flag], flag
            helps[flag].encode("ascii")
