"""The rest of R02: `--keep Pro` and the case warning.

The drop check learned that a term is a whole word ("Pro" is not in "product"), the case check
did not: it collected every "pro" the source had inside other words, so "PRODUCT LINE" demanded a
"PRO" and «Produktlinie» got 'glossary case: PRO appears as "Pro"' — a warning about a brand the
string never mentioned.
"""
from kaeris import detectors as D


def test_a_word_that_merely_contains_the_term_has_no_case_to_keep():
    assert D._glossary_case_drift("PRODUCT LINE", "Produktlinie", ["Pro"]) == []
    assert D._glossary_case_drift("Project PROGRESS", "Projektfortschritt Pro", ["Pro"]) == []


def test_the_term_itself_is_still_held_to_its_case():
    assert D._glossary_case_drift("Upgrade to Pro", "Passer à PRO", ["Pro"]) == [
        'Pro appears as "PRO" — a kept term keeps its exact case']
    assert D._glossary_case_drift("Upgrade to Pro", "Proにアップグレード", ["Pro"]) == []
