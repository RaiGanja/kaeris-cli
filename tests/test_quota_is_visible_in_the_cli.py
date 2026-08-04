"""The CLI was the one channel that could not see the monthly volume at all.

`client.key_info()` has existed all along, but only to read `model_id` for the lock file — the
month_* fields it also returns were never shown. A customer whose translations run from CI
never opens the site, so the first thing they ever heard about their monthly volume was a run
being refused, mid-release.

Every test here drives `main(argv)`, not the helper functions. On 03.08.2026 the opposite cost
a day three times over: delete the CALL and every unit test on the function stays green.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import cli as K


def _info(**over):
    base = {"tier": "premium", "model_id": "gpt-4o-mini", "char_limit": 200000,
            "month_cap": 30_000_000, "month_used": 1_200_000,
            "month_remaining": 28_800_000, "month_resets": "2026-09-01"}
    base.update(over)
    return base


@pytest.fixture()
def fake_server(monkeypatch):
    """Stand in for /api/key/info. Returns a box the test can rewrite per case."""
    box = {"info": _info(), "raises": None}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def key_info(self):
            if box["raises"]:
                raise box["raises"]
            return box["info"]

    monkeypatch.setattr(K, "KaerisClient", FakeClient)
    monkeypatch.setattr(K, "_client", lambda args: FakeClient())
    return box


def _run(argv, capsys):
    code = K.main(argv)
    cap = capsys.readouterr()
    return code, cap.out + cap.err


# ── kaeris quota ──────────────────────────────────────────────────────────────

class TestTheQuotaCommand:

    def test_it_answers_before_a_big_run(self, fake_server, capsys):
        """The question is 'will this run fit', and it has to be answerable in advance."""
        code, out = _run(["quota"], capsys)
        assert code == 0, out
        assert "1.2M" in out, f"never says how much is used: {out}"
        assert "30M" in out, f"never says what the plan bought: {out}"
        assert "28.8M" in out, f"never says how much is LEFT: {out}"
        assert "2026-09-01" in out, f"never says when it comes back: {out}"

    def test_it_floors_rather_than_rounds_up(self, fake_server, capsys):
        """29,786,587 shown as '30M' reads as 'nothing spent yet' — the opposite of the truth.
        Same rule the site's badge follows."""
        fake_server["info"] = _info(month_used=29_786_587, month_remaining=213_413)
        code, out = _run(["quota"], capsys)
        assert "29.7M" in out, f"rounded the used figure the wrong way: {out}"
        assert "30M" not in out.split("of")[0], f"'29.7M' was printed as '30M': {out}"

    def test_no_key_is_explained_rather_than_left_blank(self, fake_server, capsys):
        fake_server["info"] = {"tier": "none", "char_limit": 10000}
        code, out = _run(["quota"], capsys)
        assert code == 0, out
        assert "kaeris.dev/developer" in out, (
            f"a reader with no key is told nothing and given nowhere to go: {out}")

    def test_the_tier_name_the_live_server_actually_returns(self, fake_server, capsys):
        """Caught against production, not here: with no key the server answers tier
        "anonymous", not "none". The first version branched on the tier name and so told
        someone with no key at all about Lifetime/BYOK and their own OpenRouter tokens."""
        fake_server["info"] = {"tier": "anonymous", "char_limit": 10000}
        code, out = _run(["quota"], capsys)
        assert code == 0, out
        assert "kaeris.dev/developer" in out, (
            f"an anonymous reader was not pointed at a free key: {out}")
        assert "OpenRouter" not in out, (
            f"someone with no key was told about BYOK tokens: {out}")

    def test_an_unknown_future_tier_does_not_invent_an_explanation(self, fake_server, capsys):
        """A tier this CLI version has never heard of must not be described as anything in
        particular — a pinned CLI outlives the plan list."""
        fake_server["info"] = {"tier": "enterprise2027", "char_limit": 10000}
        code, out = _run(["--key", "kaerisp_x", "quota"], capsys)
        assert code == 0, out
        assert "OpenRouter" not in out, f"guessed that an unknown plan is BYOK: {out}"
        assert "pricing" in out, f"nowhere to go from an unknown plan: {out}"

    def test_byok_is_told_it_has_no_cap_not_shown_a_zero(self, fake_server, capsys):
        """Lifetime runs on the customer's own OpenRouter key and has no monthly volume.
        Printing '0 left' would read as an exhausted plan."""
        fake_server["info"] = {"tier": "byok", "char_limit": 1000000}
        # --key is a global flag, so it goes before the subcommand — same as every other
        # command in this CLI.
        code, out = _run(["--key", "kaerisp_x", "quota"], capsys)
        assert code == 0, out
        assert "0 left" not in out, f"an uncapped plan was shown as exhausted: {out}"
        assert "no monthly volume cap" in out.lower(), out

    def test_an_unreachable_server_fails_loudly_here(self, fake_server, capsys):
        """Unlike the line after a translation, THIS command has nothing else to report —
        silence would look like an answer."""
        fake_server["raises"] = K.KaerisError("connection refused")
        code, out = _run(["quota"], capsys)
        assert code == 2, out
        assert "connection refused" in out


# ── The line after a translation ──────────────────────────────────────────────

class TestTheLineAfterATranslation:

    def test_a_finished_run_reports_the_month(self, fake_server, monkeypatch, capsys, tmp_path):
        """Wired into cmd_translate, not just defined next to it."""
        seen = {}
        monkeypatch.setattr(K, "_translate_one", lambda c, p, l, a: 0)
        monkeypatch.setattr(K, "_resolve_sources", lambda raw: [str(tmp_path / "en.json")])
        monkeypatch.setattr(K, "_show_quota", lambda c: seen.setdefault("called", True))
        K.main(["translate", str(tmp_path / "en.json"), "--langs", "de"])
        assert seen.get("called"), (
            "cmd_translate never reports the monthly volume — the CLI is still blind to it")

    def test_the_incremental_path_reports_it_too(self, fake_server, monkeypatch, capsys, tmp_path):
        """--only-new returns from a different place. It used to skip the line entirely, which
        is exactly the 'shipped ≠ reached every channel' trap."""
        src = tmp_path / "en.json"
        src.write_text('{"a": "hello"}')
        seen = {}
        monkeypatch.setattr(K, "_translate_incremental", lambda c, p, o, l, a: 0)
        monkeypatch.setattr(K, "_show_quota", lambda c: seen.setdefault("called", True))
        K.main(["translate", str(src), "--langs", "de", "--only-new"])
        assert seen.get("called"), "--only-new runs report nothing about the month"

    def test_it_is_a_warning_once_the_month_is_nearly_gone(self, fake_server, capsys):
        fake_server["info"] = _info(month_used=29_000_000, month_remaining=1_000_000)
        K._show_quota(K.KaerisClient())
        out = capsys.readouterr().err
        assert "!" in out, f"96% used was reported as an aside: {out}"
        assert "pricing" in out, f"nowhere to go from a nearly-empty month: {out}"

    def test_it_stays_quiet_early_in_the_month(self, fake_server, capsys):
        K._show_quota(K.KaerisClient())
        out = capsys.readouterr().err
        assert "!" not in out, f"a third of the month spent was reported as a problem: {out}"
        assert "1.2M" in out

    def test_a_dead_server_never_costs_a_delivered_translation(self, fake_server, capsys):
        """The line is a courtesy. Files are already on disk by the time it runs."""
        fake_server["raises"] = K.KaerisError("gateway timeout")
        K._show_quota(K.KaerisClient())          # must not raise

    def test_an_older_server_without_the_fields_says_nothing(self, fake_server, capsys):
        """month_* only exists since 04.08.2026. A pinned CLI against an old self-hosted API
        must degrade to silence, not to '0 left'."""
        fake_server["info"] = {"tier": "premium", "char_limit": 200000}
        K._show_quota(K.KaerisClient())
        assert "left" not in capsys.readouterr().err


# ── The numbers themselves ────────────────────────────────────────────────────

class TestTheThresholds:

    def test_the_levels_land_exactly_on_the_published_percentages(self):
        cap = 1000
        for used, expected in ((0, "ok"), (799, "ok"), (800, "warn"), (949, "warn"),
                               (950, "crit"), (1000, "crit")):
            info = {"month_cap": cap, "month_used": used, "month_remaining": cap - used,
                    "month_resets": "2026-09-01"}
            assert K._quota_line(info)[1] == expected, f"{used}/{cap} was not '{expected}'"

    def test_the_thresholds_are_the_published_ones(self):
        assert (K.QUOTA_WARN_PCT, K.QUOTA_CRIT_PCT) == (80, 95)

    def test_char_formatting_never_overstates_what_is_left(self):
        assert K._fmt_chars(29_786_587) == "29.7M"
        assert K._fmt_chars(1_000_000) == "1M"
        assert K._fmt_chars(45_600) == "45k"
        assert K._fmt_chars(999) == "999"
        assert K._fmt_chars(-5) == "0"


# ── Xcode String Catalog: one file, every language ────────────────────────────

class TestTheMergedCatalogLandsInOnePlace:
    """A .xcstrings holds every language at once, so the archive carries ONE member. Deriving
    a language from its name would file "Localizable.xcstrings" under a language called
    "Localizable" and write it to a directory of that name — a path no Xcode project has."""

    def test_it_is_written_over_the_source_catalog(self, tmp_path):
        src = tmp_path / "Localizable.xcstrings"
        src.write_text('{"sourceLanguage":"en","strings":{}}')
        written = K._write_members({"Localizable.xcstrings": b'{"merged": true}'},
                                   str(src), None, "en")
        assert written == [str(src)], written
        assert src.read_bytes() == b'{"merged": true}'

    def test_out_dir_is_honoured(self, tmp_path):
        src = tmp_path / "app" / "Localizable.xcstrings"
        src.parent.mkdir()
        src.write_text("{}")
        out = tmp_path / "build"
        written = K._write_members({"Localizable.xcstrings": b'{"merged": true}'},
                                   str(src), str(out), "en")
        assert written == [str(out / "Localizable.xcstrings")], written
        assert src.read_text() == "{}", "the source was overwritten despite --out"

    def test_per_language_formats_are_untouched(self, tmp_path):
        """The normal path must keep working exactly as before."""
        src = tmp_path / "en.json"
        src.write_text("{}")
        written = K._write_members({"de.json": b"{}", "fr.json": b"{}"}, str(src), None, "en")
        assert sorted(os.path.basename(p) for p in written) == ["de.json", "fr.json"]
