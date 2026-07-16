"""Offline tests for the incremental (delta) logic — no API calls."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import incremental as inc


def test_flatten_strings_only():
    src = {"a": "x", "n": 5, "b": {"c": "y"}, "ok": True}
    assert inc.flatten(src) == {"a": "x", "b.c": "y"}


def test_flatten_all_keeps_scalars():
    src = {"a": "x", "n": 5, "ok": True, "z": None}
    assert inc.flatten_all(src) == {"a": "x", "n": 5, "ok": True, "z": None}


def test_missing_keys():
    source = {"a": "1", "b": "2", "c": "3"}
    existing = {"a": "uno"}
    assert set(inc.missing_keys(source, existing)) == {"b", "c"}


def test_build_subset_nested():
    src = {"btn": {"save": "Save", "cancel": "Cancel"}, "hi": "Hi"}
    subset = inc.build_subset(src, {"btn.save"}, flat_style=False)
    assert subset == {"btn": {"save": "Save"}}


def test_build_subset_flat():
    src = {"btn.save": "Save", "btn.cancel": "Cancel"}
    subset = inc.build_subset(src, {"btn.cancel"}, flat_style=True)
    assert subset == {"btn.cancel": "Cancel"}


def test_merge_preserves_existing_and_numbers():
    existing = {"greeting": "Hola", "count": 5, "btn": {"save": "Guardar"}}
    translated = {"btn": {"cancel": "Cancelar"}, "farewell": "Adiós"}
    merged = inc.merge_translation(existing, translated)
    assert merged["greeting"] == "Hola"
    assert merged["count"] == 5
    assert merged["btn"]["save"] == "Guardar"
    assert merged["btn"]["cancel"] == "Cancelar"
    assert merged["farewell"] == "Adiós"


def test_flat_style_detection():
    assert inc.is_flat({"a.b": "x", "c": "y"}) is True
    assert inc.is_flat({"a": {"b": "x"}}) is False


def test_changed_or_missing_detects_edited_and_missing():
    source = {"a": "Hello", "b": "Bye", "c": "New"}
    existing = {"a": "Hola", "b": "Adios"}          # c missing; a/b present
    lock = {"a": inc.hash_value("Hello"), "b": inc.hash_value("Old text")}  # b's source was edited
    todo = inc.changed_or_missing_keys(source, existing, lock)
    assert set(todo) == {"b", "c"}                  # b edited (hash mismatch) + c missing
    assert "a" not in todo                          # unchanged source, still locked -> reproduced verbatim


def test_settings_signature_is_order_stable_and_normalized():
    a = inc.settings_signature(tone="formal", icu=True, keep=["B", " A "])
    b = inc.settings_signature(tone="formal", icu=True, keep=["A", "B"])
    assert a == b                                   # keep order/whitespace doesn't matter
    assert a != inc.settings_signature(tone="casual", icu=True, keep=["A", "B"])
    assert a != inc.settings_signature(tone="formal", icu=False, keep=["A", "B"])


def test_lock_v2_roundtrip_and_legacy_compat(tmp_path=None):
    import tempfile, os as _os
    keys = {"a": inc.hash_value("Hello")}
    settings = inc.settings_signature(tone="formal", icu=False, keep=["KAERIS"])
    with tempfile.TemporaryDirectory() as d:
        p = _os.path.join(d, "kaeris.lock")
        inc.dump_lock(inc.build_lock(keys, settings), p)
        loaded = inc.load_lock(p)
        assert inc.lock_keys(loaded) == keys        # keys survive
        assert inc.lock_settings(loaded) == settings
    # legacy flat lock: keys readable, settings unknown (None) so no forced re-translate on upgrade
    legacy = {"a": inc.hash_value("Hello"), "b": inc.hash_value("Bye")}
    assert inc.lock_keys(legacy) == legacy
    assert inc.lock_settings(legacy) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
