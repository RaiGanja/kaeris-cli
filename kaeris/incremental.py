"""Incremental (delta) translation helpers for JSON files.

Only new/changed keys are sent to the API; existing translations are preserved.
This mirrors the server's flatten/unflatten so round-trips stay faithful.

A `kaeris.lock` file (JSON: dotted source key -> sha256 of the source string)
tracks what each key's source value looked like the last time it was
successfully translated. That's what lets incremental mode notice when an
EXISTING key's English text was edited (not just when a key is missing from
the target) — see changed_or_missing_keys().
"""

import hashlib
import json
import os


def flatten(obj, prefix=""):
    """Flatten nested dict to dotted keys; only string leaves are returned
    (these are the translatable keys used for delta detection)."""
    out = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        elif isinstance(v, str):
            out[key] = v
    return out


def flatten_all(obj, prefix=""):
    """Flatten keeping every scalar leaf (strings, numbers, booleans, null) —
    used for merging so non-string values survive round-trips."""
    out = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten_all(v, key))
        else:
            out[key] = v
    return out


def unflatten(flat):
    result = {}
    for key, value in flat.items():
        parts = key.split(".")
        d = result
        for p in parts[:-1]:
            if not isinstance(d.get(p), dict):
                d[p] = {}
            d = d[p]
        d[parts[-1]] = value
    return result


def is_flat(obj):
    """True if the object has no nested dict values (dotted-key style)."""
    return not any(isinstance(v, dict) for v in obj.values())


def missing_keys(source_flat, existing_flat):
    """Keys present in source but not in an existing translation."""
    return {k: v for k, v in source_flat.items() if k not in existing_flat}


def hash_value(value):
    """SHA-256 hex digest of a source string — the unit tracked in kaeris.lock."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_flat(flat):
    """{dotted key: sha256 hex} for every string leaf of a flattened source object."""
    return {k: hash_value(v) for k, v in flat.items()}


def changed_or_missing_keys(source_flat, existing_flat, lock):
    """Keys that need (re)translation this run: missing from the target, OR
    present in the target but whose source value's hash no longer matches
    `lock` (i.e. the English string was edited since the last successful
    translate run — this is the bug --only-new used to miss entirely).

    Keys with no prior lock entry are only flagged via the "missing" branch,
    so first-run behavior (no lock yet) is identical to plain missing_keys().
    """
    todo = {}
    for k, v in source_flat.items():
        if k not in existing_flat:
            todo[k] = v
            continue
        old_hash = lock.get(k)
        if old_hash is not None and old_hash != hash_value(v):
            todo[k] = v
    return todo


def unverified_keys(source_flat, existing_flat, lock):
    """Keys that already exist in the target but have no baseline in the lock.

    We cannot tell whether they match the current source. They are not translated — the whole
    point of --only-new is not to spend money on a guess — but they must not be recorded as
    current either. That was the damage: `kaeris translate` with no flags writes locale files
    and no lock, the source moves on, and the first incremental run (the GitHub Action passes
    only-new: true) skipped them and then wrote a lock stamped with the CURRENT hashes. From
    that moment the stale translations looked current to every later run, and the edits made in
    between could never be found again.
    """
    return {k for k in source_flat if k in existing_flat and k not in lock}


LOCK_VERSION = 4


def settings_signature(tone="", icu=False, keep=None, model="", app_context=""):
    """Stable signature of everything that changes the translation: tone, ICU handling, the
    glossary, the app context, and the model that produced it. A change to any of these means
    the WHOLE locale should be retranslated so it stays internally consistent (e.g. switching neutral→formal must
    not leave a mix of both) — that's the reproducibility contract, beyond just tracking edited
    source strings.

    The model belongs here for the same reason and more strongly: it is the single biggest
    influence on the output. It also changes on its own, without the user touching a flag —
    the tier picks it (MODEL_FREE vs MODEL_PREMIUM), so upgrading Free→Pro silently switched
    models and left a locale that is half DeepSeek and half GPT-4o-mini while the lock still
    reported everything up to date. Recording it means the upgrade retranslates instead.

    The app context belongs here for the same reason as the tone: it is fed to the model as
    part of the instruction, so "a bank app" and "a game for kids" give different wordings for
    the same string. Changing it and keeping half the locale from the previous context is the
    exact inconsistency this signature exists to prevent.
    """
    keep_norm = sorted(t.strip() for t in (keep or []) if t.strip())
    return {"tone": tone or "", "icu": bool(icu), "keep": keep_norm, "model": model or "",
            "app_context": (app_context or "").strip()}


def settings_changed(locked, current):
    """True if the lock's recorded settings differ from the current run's in a way that
    requires retranslating the whole locale.

    Absent/legacy settings (None) mean "unknown", never "changed" — a v1 lock has no
    signature at all, so upgrading must not force a full retranslate.

    Fields the lock never recorded are treated the same way. A v2 lock predates model
    tracking, and a client that could not reach /api/key/info reports "" — in neither case
    can we honestly claim the model changed, and forcing a full (billable) retranslate on our
    own uncertainty would be worse than the drift it guards against. Once both sides know the
    model, a real switch is caught.
    """
    if not isinstance(locked, dict):
        return False
    for field in ("tone", "icu", "keep", "app_context"):
        if field in locked and locked[field] != current.get(field):
            return True
    locked_model, current_model = locked.get("model", ""), current.get("model", "")
    return bool(locked_model and current_model and locked_model != current_model)


def lock_keys(lock, lang=None):
    """The {source key: source-hash} baseline to detect against.

    With `lang`, the PER-LANGUAGE baseline — which is the only correct one when languages are
    translated in separate runs, and they usually are (a CI matrix, or simply doing German
    today and French tomorrow). The lock used to hold a single global map meaning "this hash
    reached every language". Advancing it after a run that touched only German marked the edit
    as done for French too, so French kept the translation of the OLD English string forever,
    silently. Reproduced before this fix.

    Falls back to the global map when this language has no per-language record yet — so a lock
    written by an older version keeps behaving exactly as it did, and nobody is billed for a
    surprise full retranslate on upgrade.

    Also tolerates the legacy flat {key: hash} layout.
    """
    if isinstance(lock, dict) and "version" in lock:
        per_lang = lock.get("langs")
        if lang and isinstance(per_lang, dict) and isinstance(per_lang.get(lang), dict):
            return dict(per_lang[lang])
        return dict(lock["keys"]) if isinstance(lock.get("keys"), dict) else {}
    return {k: v for k, v in (lock or {}).items() if isinstance(v, str)}


def lock_langs(lock):
    """Per-language baselines recorded in the lock ({} for older locks)."""
    if isinstance(lock, dict) and isinstance(lock.get("langs"), dict):
        return {k: dict(v) for k, v in lock["langs"].items() if isinstance(v, dict)}
    return {}


def lock_settings(lock):
    """The settings signature recorded in the lock, or None for a legacy/absent lock. None means
    'unknown' — callers must NOT force a re-translate on it, so upgrading a v1 lock is seamless."""
    if isinstance(lock, dict) and "version" in lock:
        return lock.get("settings")
    return None


def build_lock(keys, settings, langs=None):
    """Assemble a lock document: settings, the global baseline, and per-language baselines.

    `keys` stays the "reached every language" map so an older client reading this lock behaves
    as it always did; `langs` is what a current client detects against."""
    doc = {"version": LOCK_VERSION, "settings": settings, "keys": dict(keys)}
    if langs:
        doc["langs"] = {lang: dict(m) for lang, m in langs.items() if m}
    return doc


def default_lock_path(source_path):
    """kaeris.lock lives next to the source file unless overridden."""
    return os.path.join(os.path.dirname(os.path.abspath(source_path)) or ".", "kaeris.lock")


def load_lock(path):
    """Load kaeris.lock (dotted key -> sha256 hex). Missing/invalid file -> {}."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def dump_lock(lock, path):
    """Atomic on purpose: the lock is what makes a re-run incremental. A half-written lock
    either re-translates everything (the user pays twice) or is unreadable and silently
    ignored — both from a Ctrl+C at the wrong moment."""
    from .cli import write_atomic
    write_atomic(path, json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def build_subset(source_obj, keys, flat_style):
    """Build a JSON document containing only `keys` (dotted), matching the source style."""
    subset_flat = {k: v for k, v in flatten(source_obj).items() if k in keys}
    if flat_style:
        return subset_flat
    return unflatten(subset_flat)


def merge_translation(existing_obj, translated_obj):
    """Merge freshly translated keys into an existing target document (new keys win).
    Preserves all existing leaves, including non-string scalars."""
    ex = flatten_all(existing_obj) if existing_obj else {}
    tr = flatten_all(translated_obj)
    ex.update(tr)
    return ex if is_flat_dict(existing_obj, translated_obj) else unflatten(ex)


def is_flat_dict(existing_obj, translated_obj):
    ref = existing_obj if existing_obj else translated_obj
    return is_flat(ref) if ref else True


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
