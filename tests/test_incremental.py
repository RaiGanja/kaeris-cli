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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
