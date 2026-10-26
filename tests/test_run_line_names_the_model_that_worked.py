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
