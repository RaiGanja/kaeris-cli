"""X1 (замер 0.1, 26.10.2026): `--verify` называет себя тем, чем он является.

"Verify meaning" обещало проверку смысла, а замер дал полноту 41%, термины 0 из 12, и в
CLI не было видно ни одного флага. Пока проверка — подсказка человеку, а не гарантия,
help говорит именно это: обратный перевод и проверка смысла ДЛЯ ВАШЕГО ревью.
"""
import os

from kaeris import cli as K

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _verify_help():
    p = K.build_parser()
    sub = next(a for a in p._actions if a.__class__.__name__ == "_SubParsersAction")
    t = sub.choices["translate"]
    return next(a.help for a in t._actions if "--verify" in a.option_strings)


def test_the_help_promises_a_review_aid_not_a_guarantee():
    h = _verify_help()
    assert "back-translation and meaning check for your review" in h.lower(), h
    assert "Verify meaning" not in h and "check accuracy" not in h, h


def test_the_readme_says_the_same():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        text = f.read()
    assert "meaning check for your review" in text
    assert "verify meaning" not in text.lower()
