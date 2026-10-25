"""The "new version available" hint: once a day, silent unless it is useful and safe.

No real network anywhere: the fetch and the clock are passed in."""
import io
import json
import os
import sys
import tempfile
import unittest

from kaeris import updatecheck as uc

DAY = 24 * 3600


class Tty(io.StringIO):
    def isatty(self):
        return True


def run(current="0.2.1", latest="0.2.9", env=None, now=1_000_000.0, cache=None, stream=None, fetch=None, **kw):
    calls = []

    def default_fetch(url, timeout):
        calls.append((url, timeout))
        if isinstance(latest, Exception):
            raise latest
        return latest

    stream = stream if stream is not None else Tty()
    d = cache or tempfile.mkdtemp()
    out = uc.maybe_notify("kaeris", current, stream=stream, env=env or {}, now=lambda: now,
                          fetch=fetch or default_fetch, cache_dir=d, **kw)
    return out, stream.getvalue(), calls, d


class Compare(unittest.TestCase):
    def test_newer_prints_one_line(self):
        out, text, calls, _ = run("0.2.1", "0.2.9")
        self.assertEqual(text, "A new version of kaeris is available: 0.2.1 → 0.2.9. "
                               "Update: pip install -U kaeris\n")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://pypi.org/pypi/kaeris/json")
        self.assertLessEqual(calls[0][1], 1.5)

    def test_equal_and_older_are_silent(self):
        self.assertEqual(run("0.2.9", "0.2.9")[1], "")
        self.assertEqual(run("0.3.0", "0.2.9")[1], "")

    def test_numbers_not_strings(self):
        # "0.2.10" < "0.2.9" as text, but it is the newer release.
        self.assertIn("0.2.10", run("0.2.9", "0.2.10")[1])
        self.assertEqual(run("0.2.10", "0.2.9")[1], "")

    def test_garbage_version_is_silent(self):
        self.assertEqual(run("0.2.1", "not-a-version")[1], "")
        self.assertEqual(run("0.2.1", None)[1], "")


class Silence(unittest.TestCase):
    def test_network_error_is_silent(self):
        out, text, calls, _ = run(latest=OSError("down"))
        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)

    def test_ci_env_disables_without_a_request(self):
        out, text, calls, _ = run(env={"CI": "true"})
        self.assertEqual((text, calls), ("", []))

    def test_opt_out_env(self):
        out, text, calls, _ = run(env={"KAERIS_NO_UPDATE_CHECK": "1"})
        self.assertEqual((text, calls), ("", []))

    def test_opt_out_zero_does_not_disable(self):
        self.assertIn("A new version", run(env={"KAERIS_NO_UPDATE_CHECK": "0"})[1])

    def test_not_a_tty_is_silent_and_makes_no_request(self):
        out, text, calls, _ = run(stream=io.StringIO())
        self.assertEqual((text, calls), ("", []))

    def test_tty_not_required_when_asked(self):
        out, text, calls, _ = run(stream=io.StringIO(), require_tty=False)
        self.assertIn("A new version", text)

    def test_machine_output_is_silent(self):
        out, text, calls, _ = run(quiet=True)
        self.assertEqual((text, calls), ("", []))


class Cache(unittest.TestCase):
    def test_second_run_within_24h_makes_no_request(self):
        _, t1, c1, d = run(now=1_000_000.0)
        _, t2, c2, _ = run(now=1_000_000.0 + DAY - 60, cache=d)
        self.assertEqual(len(c1), 1)
        self.assertEqual(c2, [])
        self.assertEqual(t2, "")

    def test_after_24h_asks_again(self):
        _, _, _, d = run(now=1_000_000.0)
        _, t, c, _ = run(now=1_000_000.0 + DAY + 1, cache=d)
        self.assertEqual(len(c), 1)
        self.assertIn("A new version", t)

    def test_failed_request_is_not_retried_on_every_run_but_within_the_hour(self):
        _, _, _, d = run(latest=OSError("x"), now=1_000_000.0)
        _, _, c, _ = run(now=1_000_000.0 + 60, cache=d)
        self.assertEqual(c, [])
        _, t, c, _ = run(now=1_000_000.0 + 3601, cache=d)
        self.assertEqual(len(c), 1)
        self.assertIn("A new version", t)

    def test_corrupt_cache_is_ignored(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "update-check.json"), "w") as f:
            f.write("{nope")
        self.assertIn("A new version", run(cache=d)[1])

    def test_unwritable_cache_dir_is_silent(self):
        f = tempfile.NamedTemporaryFile(delete=False)
        f.close()
        out, text, calls, _ = run(cache=os.path.join(f.name, "sub"))
        self.assertIn("A new version", text)  # still tells; just cannot remember


class Wiring(unittest.TestCase):
    def test_json_flag_never_prints_hint(self):
        from kaeris import cli
        seen = []
        orig = uc.maybe_notify
        uc.maybe_notify = lambda *a, **k: seen.append(k)
        try:
            cli._update_hint(type("A", (), {"json": True, "sarif": None})())
            cli._update_hint(type("A", (), {"json": False, "sarif": None})())
        finally:
            uc.maybe_notify = orig
        self.assertEqual([k.get("quiet") for k in seen], [True, False])


if __name__ == "__main__":
    unittest.main()
