"""Project translation memory.

`kaeris.lock` remembers a KEY and the hash of the English it was translated from. That is
enough to notice an edited string, and not enough for anything else: rename `nav.save` to
`toolbar.save` and the identical sentence is translated and charged again; the same "Save
changes" in `common.json` and in `checkout.json` is translated twice; delete a key and re-add
it next week and you pay for it twice.

This remembers the TEXT instead. One file, `kaeris-tm.json`, committed with the project:

    source string + the settings it was translated under  ->  { de: …, fr: … }

so reuse survives renames, moves between files, and a colleague who has only just cloned the
repository. The map is handed to the API as its `reuse` parameter — a path that already
exists, is already tested, and already does not charge for what it did not translate.

The settings are part of the key on purpose. A string translated with `--tone formal` is not
the same answer as one translated casually, and a glossary or model change means the old text
was produced under rules that no longer apply. Same rule the lock follows.

Zero dependencies, like the rest of this package.
"""
import hashlib
import json
import os

VERSION = 1
FILENAME = "kaeris-tm.json"


def default_path(source_path, explicit=None):
    """Where the memory lives: next to the source file, unless told otherwise."""
    if explicit:
        return explicit
    return os.path.join(os.path.dirname(os.path.abspath(source_path)), FILENAME)


def signature_key(signature):
    """A stable string for the settings map incremental.settings_signature() returns.

    It is a dict, and a dict has no defined text form — json.dumps with sorted keys gives one
    that does not shift when Python's iteration order does. Getting this wrong would not fail
    loudly: the memory would simply stop matching itself between runs."""
    if isinstance(signature, str):
        return signature
    return json.dumps(signature or {}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def entry_id(text, signature):
    """The unit of memory: this exact string, translated under these exact settings."""
    payload = signature_key(signature) + "\x00" + text
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def load(path):
    """Read the memory, or an empty one. A damaged file is treated as empty rather than fatal:
    a translation run must not be blocked by a cache."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": VERSION, "entries": {}}


def save(path, memory, write):
    """Persist the memory. `write` is the caller's atomic writer, so a crash mid-save cannot
    leave a half-written file where a valid one was."""
    memory["version"] = VERSION
    body = json.dumps(memory, ensure_ascii=False, indent=2, sort_keys=True)
    write(path, body)


def lookup(memory, source_flat, langs, signature):
    """{lang: {key: previous translation}} for every source string already known.

    Keyed by text, so it finds a string that moved to a different key or a different file."""
    entries = memory.get("entries") or {}
    out = {}
    for lang in langs:
        hits = {}
        for key, text in source_flat.items():
            if not isinstance(text, str):
                continue
            got = (entries.get(entry_id(text, signature)) or {}).get(lang)
            if isinstance(got, str) and got:
                hits[key] = got
        if hits:
            out[lang] = hits
    return out


def record(memory, source_flat, translations, signature):
    """Remember what came back. Returns how many string/language pairs are new.

    A translation identical to its source is NOT remembered: that is what a language which
    fell back to the original looks like, and storing it would teach the memory to serve the
    untranslated text forever — the same trap the browser memory fell into on 02.08."""
    entries = memory.setdefault("entries", {})
    added = 0
    for lang, kv in (translations or {}).items():
        if not isinstance(kv, dict):
            continue
        for key, translated in kv.items():
            text = source_flat.get(key)
            if not isinstance(text, str) or not isinstance(translated, str):
                continue
            if not translated.strip() or translated == text:
                continue
            slot = entries.setdefault(entry_id(text, signature), {"source": text})
            if slot.get(lang) != translated:
                added += 1
            slot[lang] = translated
    return added


def count(memory):
    entries = memory.get("entries") or {}
    strings = len(entries)
    pairs = sum(len([k for k in e if k != "source"]) for e in entries.values()
                if isinstance(e, dict))
    return strings, pairs
