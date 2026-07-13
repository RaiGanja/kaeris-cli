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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lock, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


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
