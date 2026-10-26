"""`--verify` tells the person what it found (замер 0.1, 26.10.2026: U1, U3, U4, X2).

N1: the server flagged meaning errors (168 on the benchmark) and the CLI printed none of them —
it kept `_back` for verify.json and dropped `_distortion` on the floor. Also N7: numbers,
prices and "untranslated" were printed under a heading about placeholders that break the app.
"""
import io
import json
import types
import unittest.mock as mock

from kaeris import cli as K

PREVIEW = {
    "_warnings": {"de": {"greet": ["{name}"],
                         "price": ["number 15 from the source is missing or changed in the translation"]}},
    "_back": {"de": {"signin": "Log out", "greet": "Hello", "price": "15 euros"}},
    "_meaning": {"de": {"signin": {"verdict": "error", "issue": "opposite action",
                                   "explain": "The German says 'sign out'.",
                                   "fix": "Anmelden"}}},
    "_review": {"de": {"error": ["signin", "greet"], "review": ["price"], "unchecked": [],
                       "to_review": 3, "lines": 40}},
}


def _qa(data, verify=True):
    class _C:
        def preview(self, job):
            return data

    err = io.StringIO()
    args = types.SimpleNamespace(verify=verify, back_lang="en")
    with mock.patch("sys.stderr", err):
        K._show_qa(_C(), "j", args, "en.json")
    return err.getvalue(), args


def test_a_meaning_flag_is_printed_with_the_reason_and_the_fix():
    out, _ = _qa(PREVIEW)
    assert "⚠ meaning: [de] signin: opposite action" in out, out
    assert "The German says 'sign out'." in out
    assert "Anmelden" in out


def test_the_file_says_how_many_lines_to_review():
    out, _ = _qa(PREVIEW)
    assert "3 lines to review" in out, out


def test_two_headings_for_two_kinds_of_warning():
    out, _ = _qa(PREVIEW)
    assert "may break your app" in out
    assert "check the meaning" in out
    head_break = out.index("may break your app")
    head_meaning = out.index("check the meaning")
    greet = out.index("[de] greet: missing {name}")
    price = out.index("[de] price: number 15")
    assert head_break < greet, "плейсхолдер не под шапкой «may break your app»"
    assert head_meaning < price, "число не под шапкой «check the meaning»"
    assert not (head_break < price < head_meaning), "число напечатано среди поломок"


def test_verify_json_keeps_back_translations_and_adds_the_review(tmp_path):
    _, args = _qa(PREVIEW)
    args.out = str(tmp_path)
    K._write_verify(args, ["en.json"])
    body = json.loads((tmp_path / "verify.json").read_text(encoding="utf-8"))
    assert body["de"]["signin"] == "Log out", "обратные переводы — прежний формат, как был"
    line = body["_review"]["de"]["signin"]
    assert line["status"] == "error" and line["meaning"] == "opposite action"
    assert line["explain"] == "The German says 'sign out'." and line["fix"] == "Anmelden"
    assert body["_review"]["de"]["price"]["status"] == "review"
    summary = dict(body["_summary"])
    by_lang = summary.pop("by_lang")
    assert summary == {"lines": 40, "to_review": 3, "error": 2, "review": 1, "unchecked": 0}
    assert by_lang == {"de": {"lines": 40, "to_review": 3, "error": 2, "review": 1, "unchecked": 0}}


def test_no_verify_no_meaning_lines():
    out, _ = _qa({"_warnings": {}}, verify=False)
    assert "meaning:" not in out and "lines to review" not in out
