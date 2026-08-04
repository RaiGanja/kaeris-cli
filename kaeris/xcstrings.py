"""Read an Xcode String Catalog (.xcstrings) for `kaeris check`.

Unlike every other format this CLI checks, a catalog is ONE file holding the source language
and every translation. So there is nothing to open per language: the source and all the targets
come out of the same document.

The key encoding and the ICU rendering of plurals mirror the server's parser byte for byte. If
they drift, `kaeris check` and the API disagree about the same file — one says a string is
missing while the other has just translated it — and the developer has no way to tell which is
lying. tests/test_xcstrings_parity.py compares the two on a shared corpus.

Zero dependencies, like the rest of this package.
"""
import json
import re

_XC_OPEN, _XC_CLOSE = "⟦", "⟧"          # ⟦ ⟧ — marks a synthetic sub-key

# printf-family specifier: %lld, %@, %d, %1$lld, %.2f — what Xcode substitutes the count into.
_SPEC_RE = re.compile(r"%(?:\d+\$)?[-+ #0]*[\d.*]*(?:hh|h|ll|l|q|L|z|t|j)?[@dioupxXeEfgGcsSaAn%]")


def _key(base, *parts):
    return base + "".join(f"{_XC_OPEN}{p}{_XC_CLOSE}" for p in parts)


def _count_spec(arm_texts):
    """The specifier standing for the COUNT: the one printf token every arm shares."""
    common = None
    for text in arm_texts:
        found = set(_SPEC_RE.findall(text))
        common = found if common is None else (common & found)
    return next(iter(common)) if common and len(common) == 1 else None


def _icu_from_plural(plural):
    """{'one': {...}, 'other': {...}} -> "{count, plural, one {# …} other {# …}}".

    The count specifier is swapped for `#`, ICU's own marker for the number — the same thing
    the server does, so the ICU and CLDR detectors see the shape they were written for."""
    texts = [((n or {}).get("stringUnit") or {}).get("value")
             for n in plural.values()]
    texts = [t for t in texts if isinstance(t, str)]
    if not texts:
        return None
    spec = _count_spec(texts)
    arms = []
    for cat, node in plural.items():
        val = ((node or {}).get("stringUnit") or {}).get("value")
        if isinstance(val, str):
            arms.append(f"{cat} {{{val.replace(spec, '#') if spec else val}}}")
    return "{count, plural, " + " ".join(arms) + "}" if arms else None


def _flatten_localization(base, loc, out, prefix=()):
    unit = (loc.get("stringUnit") or {}).get("value")
    if isinstance(unit, str) and unit.strip():
        out[_key(base, *prefix)] = unit
    variations = loc.get("variations") or {}
    if isinstance(variations.get("plural"), dict):
        icu = _icu_from_plural(variations["plural"])
        if icu:
            out[_key(base, *prefix, "plural")] = icu
    for dev, node in (variations.get("device") or {}).items():
        if isinstance(node, dict):
            _flatten_localization(base, node, out, prefix + (f"device:{dev}",))
    for name, node in (loc.get("substitutions") or {}).items():
        if isinstance(node, dict):
            _flatten_localization(base, node, out, prefix + (f"sub:{name}",))


def is_catalog(path):
    return path.lower().endswith(".xcstrings")


def load(path):
    """(source_lang, {lang: {key: text}}) for every language present in the catalog.

    The source language always appears, even for keys with no localization at all: Xcode's
    convention is that such a key IS its own source string, and treating those as absent would
    report a freshly-extracted project as entirely untranslated."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("strings"), dict):
        raise ValueError("not a String Catalog: no 'strings' object")
    src_lang = doc.get("sourceLanguage") or "en"

    langs = {src_lang}
    for entry in doc["strings"].values():
        if isinstance(entry, dict):
            langs.update((entry.get("localizations") or {}).keys())

    by_lang = {lang: {} for lang in langs}
    for key, entry in doc["strings"].items():
        if not isinstance(entry, dict) or entry.get("shouldTranslate") is False:
            continue
        locs = entry.get("localizations") or {}
        for lang in langs:
            loc = locs.get(lang)
            if isinstance(loc, dict):
                _flatten_localization(key, loc, by_lang[lang])
            elif lang == src_lang and not locs.get(src_lang):
                by_lang[lang][key] = key
    return src_lang, by_lang
