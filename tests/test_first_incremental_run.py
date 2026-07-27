"""The first incremental run over an existing project buried every edit made before it.

The normal way into this product is `kaeris translate` with no flags: it writes locale files
and no lock. Weeks later the English strings have moved on, and the user adopts the GitHub
Action — which passes only-new: true. That first incremental run has no baseline, so a key that
already exists in the target is skipped whatever the source now says. Skipping is the cheap,
expected behaviour and stays. What cannot stay is what happened next: the run wrote a lock
stamped with the CURRENT source hashes, so from then on those stale translations looked
current forever, and the edits could never be detected again by anything.

Reproduced against the published 0.2.8 with real translations:

    day 1   kaeris translate en.json --langs de,fr        → files, no lock
    day 2   edit "Hello" → "Hi there, welcome back"
    day 3   kaeris translate en.json --langs de --only-new
            → "de: up to date", lock records the hash of the NEW string,
              de.json still holds the translation of the old one — permanently

A key present in the target with no lock entry is not evidence that it matches the source; it
is the absence of evidence. It is still not translated (nobody's money is spent on a guess),
but it is not locked either, and the run says so out loud. --assume-current is the caller
stating the files really are in sync, which locks them and silences the notice.
"""
import io
import json
import os
import tempfile
import unittest.mock as mock

import pytest

from kaeris import cli
from kaeris import incremental as inc


def test_unverified_keys_are_the_ones_present_with_no_baseline():
    source = {"a": "one", "b": "two", "c": "three"}
    existing = {"a": "eins", "b": "zwei"}
    lock = {"a": inc.hash_value("one")}
    assert inc.unverified_keys(source, existing, lock) == {"b"}


def test_nothing_is_unverified_on_a_first_ever_run():
    """No targets yet — every key is missing and will be translated, so nothing is guessed."""
    assert inc.unverified_keys({"a": "one"}, {}, {}) == set()


def test_a_key_with_no_baseline_is_still_not_translated():
    """The cheap behaviour is the point of --only-new and does not change."""
    todo = inc.changed_or_missing_keys({"a": "one"}, {"a": "eins"}, {})
    assert todo == {}


def _run(tmp, *extra):
    en = os.path.join(tmp, "en.json")
    de = os.path.join(tmp, "de.json")
    inc.dump_json({"greeting": "Hi there, welcome back"}, en)
    inc.dump_json({"greeting": "Hallo"}, de)          # перевод старой строки, замка нет
    args = cli.build_parser().parse_args(["translate", en, "--langs", "de", "--only-new", *extra])
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        code = cli.cmd_translate(args)
    lock_path = inc.default_lock_path(en)
    lock = json.load(open(lock_path)) if os.path.exists(lock_path) else {}
    return code, err.getvalue(), lock


def test_the_run_refuses_to_claim_an_unverified_key_is_current():
    with tempfile.TemporaryDirectory() as tmp:
        code, out, lock = _run(tmp)
        assert code == 0
        assert "greeting" not in lock.get("keys", {}), \
            "замок объявил актуальным перевод, который никто не проверял"
        assert "greeting" not in (lock.get("langs", {}).get("de") or {})


def test_the_run_says_out_loud_what_it_could_not_verify():
    with tempfile.TemporaryDirectory() as tmp:
        _, out, _ = _run(tmp)
        assert "kaeris.lock" in out and "de" in out
        assert "--assume-current" in out, "человеку не сказали, чем это лечится"


def test_assume_current_locks_them_and_stays_quiet():
    with tempfile.TemporaryDirectory() as tmp:
        code, out, lock = _run(tmp, "--assume-current")
        assert code == 0
        assert lock["keys"]["greeting"] == inc.hash_value("Hi there, welcome back")
        assert "--assume-current" not in out


def test_after_a_normal_run_the_notice_is_gone():
    """Once a key has a baseline it is verified, and the next run is silent about it."""
    with tempfile.TemporaryDirectory() as tmp:
        en = os.path.join(tmp, "en.json")
        inc.dump_json({"greeting": "Hello"}, en)
        lock_path = inc.default_lock_path(en)
        inc.dump_lock(inc.build_lock({"greeting": inc.hash_value("Hello")}, {},
                                     {"de": {"greeting": inc.hash_value("Hello")}}), lock_path)
        inc.dump_json({"greeting": "Hallo"}, os.path.join(tmp, "de.json"))
        args = cli.build_parser().parse_args(["translate", en, "--langs", "de", "--only-new"])
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            cli.cmd_translate(args)
        assert "--assume-current" not in err.getvalue()
