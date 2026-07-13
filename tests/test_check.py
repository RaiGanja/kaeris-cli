"""Offline tests for the `kaeris check` i18n firewall logic — no API calls."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import check as chk


def test_find_placeholders_simple():
    assert chk.find_placeholders("Hi {name}, you have {count} items") == {"{name}", "{count}"}


def test_find_placeholders_printf_and_positional():
    assert chk.find_placeholders("%s scored %d points") == {"%s", "%d"}
    assert chk.find_placeholders("%1$s scored %2$d points") == {"%1$s", "%2$d"}


def test_find_placeholders_double_brace():
    assert chk.find_placeholders("Hello {{name}}") == {"{name}"}


def test_find_placeholders_icu_plural_ignores_subclauses():
    src = "{count, plural, one {# item} other {# items}}"
    assert chk.find_placeholders(src) == {"{count}"}


def test_find_placeholders_no_placeholder():
    assert chk.find_placeholders("Just plain text") == set()


def test_find_placeholders_dollar_template_is_own_family():
    # M2: ${name} is a distinct family from {name} — a ${x} -> {x} change is a
    # real runtime break and must surface as a mismatch, not be silently equal.
    assert chk.find_placeholders("Hi ${name}") == {"${name}"}
    assert chk.find_placeholders("Hi ${name} and {count}") == {"${name}", "{count}"}
    src = chk.find_placeholders("Hi ${name}")
    tgt = chk.find_placeholders("Hi {name}")
    assert src != tgt


def test_find_placeholders_colon_shortcode_not_a_placeholder():
    # M1: emoji shortcodes / prose colons are NOT placeholders (the removed
    # :word: family used to false-positive here and fail CI on plain text).
    assert chk.find_placeholders("Great job :smile:") == set()
    assert chk.find_placeholders("Meet at 12:30, ratio 3:2") == set()


def test_diff_locale_missing_key():
    source = {"a": "Hi {name}", "b": "Bye"}
    target = {"a": "Hallo {name}"}
    missing, extra, issues = chk.diff_locale(source, target)
    assert missing == ["b"]
    assert extra == []
    assert issues == []


def test_diff_locale_placeholder_mismatch():
    source = {"a": "Hi {name}"}
    target = {"a": "Salut {nom}"}
    missing, extra, issues = chk.diff_locale(source, target)
    assert missing == []
    assert issues == [{"key": "a", "missing": ["{name}"], "added": ["{nom}"]}]


def test_diff_locale_extra_key():
    source = {"a": "Hi"}
    target = {"a": "Hola", "b": "Stale"}
    missing, extra, issues = chk.diff_locale(source, target)
    assert extra == ["b"]
    assert missing == []
    assert issues == []


def test_diff_locale_clean():
    source = {"a": "Hi {name}", "b": "Bye"}
    target = {"a": "Hallo {name}", "b": "Tschüss"}
    missing, extra, issues = chk.diff_locale(source, target)
    assert missing == extra == issues == []


def test_check_locales_ok_false_then_true():
    source = {"a": "Hi {name}", "b": "Bye"}
    targets = {
        "de": {"a": "Hallo {name}"},              # missing "b"
        "fr": {"a": "Salut {nom}", "b": "Au revoir"},  # placeholder mismatch on "a"
    }
    result = chk.check_locales(source, ["de", "fr"], lambda lang: targets.get(lang))
    assert result["ok"] is False
    assert result["missing"] == {"de": ["b"]}
    assert result["placeholder_issues"] == [
        {"lang": "fr", "key": "a", "missing": ["{name}"], "added": ["{nom}"]}
    ]

    fixed = {
        "de": {"a": "Hallo {name}", "b": "Tschüss"},
        "fr": {"a": "Salut {name}", "b": "Au revoir"},
    }
    result2 = chk.check_locales(source, ["de", "fr"], lambda lang: fixed.get(lang))
    assert result2["ok"] is True
    assert result2["missing"] == {}
    assert result2["placeholder_issues"] == []


def test_check_locales_missing_file():
    source = {"a": "Hi"}
    result = chk.check_locales(source, ["ja"], lambda lang: None)
    assert result["ok"] is False
    assert result["missing_files"] == ["ja"]
    assert result["missing"] == {"ja": ["a"]}
