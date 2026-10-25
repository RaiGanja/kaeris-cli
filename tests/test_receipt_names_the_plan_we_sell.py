"""The "Run:" line names the plan the way the pricing page sells it.

The server's receipt carries the tier id it keys limits by: `premium`, `teamapi`, `byok`,
`team`, `free`. Printed as is, a Pro customer reads "premium" — an old plan name nobody sells
— and a Lifetime customer reads "byok". The README sample showed exactly that line, next to a
model the product had stopped using.
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kaeris import cli as K   # noqa: E402


def _run_line(plan):
    rec = {"model": "google/gemini-2.5-flash-lite", "plan": plan, "strings": 1, "seconds": 0.1,
           "languages": {"requested": ["de"], "delivered": ["de"], "failed": []}}
    lines, _ = K._fmt_receipt(rec)
    return lines[0]


@pytest.mark.parametrize("tier,name", [("premium", "Pro"), ("teamapi", "Team/API"),
                                       ("byok", "Lifetime"), ("team", "Scale"),
                                       ("free", "Free key"), ("anonymous", "Free")])
def test_the_run_line_says_the_plan_name(tier, name):
    line = _run_line(tier)
    assert f" · {name} · " in line, line


def test_an_unknown_tier_is_shown_as_is():
    assert " · enterprise · " in _run_line("enterprise")


def test_a_hidden_plan_still_says_so():
    assert "plan hidden" in _run_line(None)


def test_readme_sample_matches_what_the_cli_prints():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    m = re.search(r"→ Run: (\S+) · (\S+) · ", readme)
    assert m, "README lost its sample Run: line"
    assert m.group(2) in ("Pro", "Team/API", "Lifetime", "Free", "Free key"), m.group(0)
    assert "gpt-4o-mini" not in m.group(1), "the sample shows a model the product no longer translates with"


def test_readme_does_not_call_lifetime_unlimited():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    row = next(l for l in readme.splitlines() if l.startswith("| **Lifetime"))
    assert "unlimited" not in row.lower() and "1,000,000" in row, row


def test_readme_tier_table_has_the_free_key():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert re.search(r"^\| \*\*Free API key\*\*[^\n]*50,000", readme, re.M)
