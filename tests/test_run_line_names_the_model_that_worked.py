"""The Run: line names the model that actually translated (замер 0.2, R29).

When the primary model was down and the fallback did the work, the receipt said
"models_served": ["openai/gpt-4.1-mini"] while the CLI printed the configured
google/gemini-2.5-flash-lite — the one place a customer reads which model ran told them the
wrong one.
"""
from kaeris import cli as K


def _rec(**kw):
    rec = {"model": "google/gemini-2.5-flash-lite", "plan": "premium", "strings": 1,
           "seconds": 1, "languages": {"requested": ["de"], "delivered": ["de"], "failed": []}}
    rec.update(kw)
    return rec


def test_the_fallback_is_named():
    lines, _ = K._fmt_receipt(_rec(models_served=["openai/gpt-4.1-mini"]))
    assert lines[0].startswith("Run: openai/gpt-4.1-mini ·"), lines[0]


def test_both_are_named_when_both_worked():
    lines, _ = K._fmt_receipt(_rec(models_served=["google/gemini-2.5-flash-lite",
                                                  "openai/gpt-4.1-mini"]))
    assert "google/gemini-2.5-flash-lite + openai/gpt-4.1-mini" in lines[0]


def test_an_older_server_without_the_field_still_reads():
    lines, _ = K._fmt_receipt(_rec())
    assert lines[0].startswith("Run: google/gemini-2.5-flash-lite ·")


# A live run on 27.10.2026: the backup answered in 1-4 of 10 runs and the Run: line named it
# like any other model, saying neither that it was the backup nor why. The server now sends
# `fallback`.
_FB = {"model": "openai/gpt-4.1-mini", "batches": 1, "languages": ["de"],
       "reason": "primary failed on attempt 1: HTTP 429: Provider returned error"}


def test_the_backup_is_called_a_backup():
    lines, _ = K._fmt_receipt(_rec(models_served=["openai/gpt-4.1-mini"], fallback=_FB))
    assert "openai/gpt-4.1-mini (backup)" in lines[0], lines[0]


def test_the_reason_for_the_backup_is_one_line():
    line = K._fallback_line(_rec(models_served=["openai/gpt-4.1-mini"], fallback=_FB))
    assert line and "google/gemini-2.5-flash-lite" in line and "HTTP 429" in line, line
    assert "de" in line, line


def test_no_backup_no_line():
    assert K._fallback_line(_rec(models_served=["google/gemini-2.5-flash-lite"])) is None
    lines, _ = K._fmt_receipt(_rec(models_served=["google/gemini-2.5-flash-lite"]))
    assert "(backup)" not in lines[0]
