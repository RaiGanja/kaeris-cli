"""Translating one language marked the edit as done for all of them.

kaeris.lock held a single map: "this source hash has reached every target language". Inside a
run that held, because a language that failed kept its keys stale-locked. Across runs it did
not — and translating languages in separate runs is the normal case, whether that is a CI
matrix or simply doing German today and French tomorrow.

Reproduced before the fix:
    day 1  source "Hello"      → de and fr translated, lock records hash("Hello")
    day 2  source → "Hi there" → run --langs de only; de retranslated, lock advances
    day 3  run --langs fr      → nothing to do. French keeps the translation of "Hello".

Silently, permanently, on the feature sold as incremental and reproducible. The lock now keeps
a baseline per language, and falls back to the old global map for a language it has not
recorded yet — so upgrading does not trigger a surprise (billable) full retranslate.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import incremental as inc


SOURCE_V1 = {"greet": "Hello"}
SOURCE_V2 = {"greet": "Hi there"}


class TestPerLanguageLock(unittest.TestCase):
    def test_an_edit_translated_in_one_language_is_still_pending_in_another(self):
        flat_v1 = inc.flatten(SOURCE_V1)
        lock = inc.build_lock(inc.hash_flat(flat_v1), inc.settings_signature(),
                              {"de": inc.hash_flat(flat_v1), "fr": inc.hash_flat(flat_v1)})
        de, fr = {"greet": "Hallo"}, {"greet": "Bonjour"}

        flat_v2 = inc.flatten(SOURCE_V2)
        todo_de = inc.changed_or_missing_keys(flat_v2, inc.flatten(de), inc.lock_keys(lock, "de"))
        self.assertEqual(list(todo_de), ["greet"])

        # German run finishes; only German's baseline advances.
        langs = inc.lock_langs(lock)
        langs["de"] = inc.hash_flat(flat_v2)
        lock = inc.build_lock({}, inc.settings_signature(), langs)

        todo_fr = inc.changed_or_missing_keys(flat_v2, inc.flatten(fr), inc.lock_keys(lock, "fr"))
        self.assertEqual(list(todo_fr), ["greet"],
                         "French must still see the edited source as pending")

    def test_a_language_that_is_current_is_not_retranslated(self):
        flat = inc.flatten(SOURCE_V1)
        lock = inc.build_lock(inc.hash_flat(flat), inc.settings_signature(),
                              {"de": inc.hash_flat(flat)})
        todo = inc.changed_or_missing_keys(flat, inc.flatten({"greet": "Hallo"}),
                                           inc.lock_keys(lock, "de"))
        self.assertEqual(todo, {}, "an up-to-date language must cost nothing")

    def test_an_old_lock_without_per_language_data_behaves_exactly_as_before(self):
        """Upgrading must not bill anyone for a full retranslate."""
        flat = inc.flatten(SOURCE_V1)
        old_lock = {"version": 3, "settings": inc.settings_signature(),
                    "keys": inc.hash_flat(flat)}
        for lang in ("de", "fr", "ja"):
            baseline = inc.lock_keys(old_lock, lang)
            self.assertEqual(baseline, inc.hash_flat(flat),
                             "an unseen language falls back to the global baseline")
            todo = inc.changed_or_missing_keys(flat, inc.flatten({"greet": "x"}), baseline)
            self.assertEqual(todo, {})

    def test_the_legacy_flat_lock_still_loads(self):
        legacy = {"greet": inc.hash_value("Hello")}
        self.assertEqual(inc.lock_keys(legacy), legacy)
        self.assertEqual(inc.lock_keys(legacy, "de"), legacy)

    def test_a_new_language_translates_everything_the_first_time(self):
        flat = inc.flatten(SOURCE_V1)
        lock = inc.build_lock(inc.hash_flat(flat), inc.settings_signature(),
                              {"de": inc.hash_flat(flat)})
        # Spanish has no file and no baseline yet.
        todo = inc.changed_or_missing_keys(flat, {}, inc.lock_keys(lock, "es"))
        self.assertEqual(list(todo), ["greet"])


class TestLockShape(unittest.TestCase):
    def test_per_language_data_is_written_and_read_back(self):
        flat = inc.flatten(SOURCE_V1)
        doc = inc.build_lock(inc.hash_flat(flat), inc.settings_signature(),
                             {"de": inc.hash_flat(flat)})
        self.assertEqual(doc["version"], inc.LOCK_VERSION)
        self.assertIn("langs", doc)
        self.assertEqual(inc.lock_langs(doc)["de"], inc.hash_flat(flat))
        # The global map is still there, so an older client reading this lock is unaffected.
        self.assertEqual(doc["keys"], inc.hash_flat(flat))

    def test_no_per_language_data_means_no_langs_key(self):
        doc = inc.build_lock({"a": "h"}, inc.settings_signature())
        self.assertNotIn("langs", doc)


if __name__ == "__main__":
    unittest.main()
