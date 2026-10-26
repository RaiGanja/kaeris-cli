"""A customer re-run (26.10.2026), recreated with neutral strings of the same shape, on the copy
of the detectors `kaeris check` runs. The backend holds the same cases for its own copy, and
tests/test_detector_parity.py keeps the two answering alike."""
import pytest

from kaeris import detectors as D


@pytest.mark.parametrize("src,tr", [
    # One emphasis became two.
    ("Notes from\nthe *harbor*.", "*港*からの\n*メモ*。"),
    ("users remember *when* they saved a file, not only where.",
     "使用者記住的是他們*何時*儲存檔案，而不僅僅是*何處*。"),
])
def test_an_added_emphasis_is_reported(src, tr):
    assert any("emphasis" in f for f in D._line_break_faults(src, tr, soft_breaks=True))


@pytest.mark.parametrize("src,tr", [
    ("Ninety seconds\nof *setup*.", "90秒の\n*セットアップ*。"),
    ("Two hundred testers from the forum try the first release.",
     "フォーラムから選ばれた200人のテスターが最初のリリースを試す。"),
    ("a tester spent forty minutes on one screen", "テスターが40分間一つの画面に費やした"),
    ("a score from zero to one hundred", "0から100までのスコア"),
])
def test_a_number_the_source_spells_out_is_not_invented(src, tr):
    assert D._numeric_faults(src, tr, "ja") == []


def test_a_spelled_number_still_cannot_hide_a_wrong_one():
    assert D._numeric_faults("Ninety seconds", "96秒", "ja")


def test_a_label_that_gained_a_few_characters_is_not_an_overflow():
    assert D._compute_overflow({"k": "Page not found"}, {"k": "Pahina ay hindi natagpuan"}, "tl") == []


def test_a_button_that_doubled_is_still_reported():
    assert D._compute_overflow({"k": "Add to favorites"},
                               {"k": "Afegeix a la llista de preferits"}, "ca")


def test_prose_reflows():
    assert D._compute_overflow({"k": "Still testing"}, {"k": "अभी भी परीक्षण कर रहे हैं"}, "hi",
                               prose=True) == []
