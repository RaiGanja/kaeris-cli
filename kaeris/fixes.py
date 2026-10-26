"""Apply what `--verify` found to the translated files: locally, with no network and no charge.

verify.json names every line to read and, where the meaning check proposed one, a corrected
translation. Before this module the person copied each fix by hand into each locale file. A fix
is written only if our own detectors find nothing in it that breaks the app (placeholders, tags,
ICU, line breaks, *emphasis*) and nothing new; a line with a lost line break or a torn emphasis
and no fix gets them put back the way the server does it (detectors._mend_structure). What
needs a new translation is not done here: that is a model call, and a model call is a paid run.
"""
from __future__ import annotations

import codecs
import json
import os
import re

from . import detectors as det
from . import incremental as inc
from .encoding import UnknownEncoding, _bomless_wide_encoding, decode_bytes

APPLIED, ALREADY, SKIPPED = "applied", "already", "skipped"


def _kinds(messages):
    return {re.sub(r"\d+", "#", m) for m in messages}


def _faults(src, text, lang, glossary):
    return [f["msg"] for f in det.string_faults(src, text, lang, glossary)]


def _passes(src, current, cand, lang, glossary):
    """("", True) when `cand` breaks nothing and adds no problem the current line lacks."""
    after = _faults(src, cand, lang, glossary)
    breaking = [m for m in after if det.warning_group(m) == "break"]
    if breaking:
        return breaking[0], False
    new = sorted(_kinds(after) - _kinds(_faults(src, current, lang, glossary)))
    if new:
        return f"it adds a new problem: {new[0]}", False
    return "", True


def choose(entry, lang, glossary=None):
    """(text, how, why): the line to write and where it came from, or (None, None, why not)."""
    src, cur = entry.get("source"), entry.get("translation")
    if not isinstance(src, str) or not isinstance(cur, str):
        return None, None, ("verify.json has no source text for this line (written by an older "
                            "kaeris) — run `kaeris translate --verify` again")
    rejected = ""
    fix = entry.get("fix")
    if isinstance(fix, str) and fix.strip() and fix != cur:
        why, ok = _passes(src, cur, fix, lang, glossary)
        if ok:
            return fix, "fix", ""
        rejected = f"the suggested fix was not applied: {why}"
    mended = det._mend_structure(src, cur)
    if mended != cur:
        why, ok = _passes(src, cur, mended, lang, glossary)
        if ok:
            return mended, "structure", ""
    if rejected:
        return None, None, rejected
    if fix == cur:
        return None, None, "the suggested fix is the current translation"
    return None, None, ("no fix to apply — it needs a new translation of this line (a model "
                        "call, charged to your quota like any translation)")


def _variants(text):
    """How file formats write a string: as is (Markdown, YAML block), JSON/PO-escaped, XML, and
    Android's strings.xml with its own backslashes."""
    def xml(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return [lambda t: t,
            lambda t: json.dumps(t, ensure_ascii=False)[1:-1],
            xml,
            lambda t: xml(t).replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")]


_NOT_FOUND = "the line is not in the file as verify saw it (edited since?) — apply it by hand"


def _forms(raw, cur, new):
    """(current, new) as the file writes them, line endings included."""
    out = [(cur, new)]
    if "\r\n" in raw and "\n" in cur:
        out.append((cur.replace("\n", "\r\n"), new.replace("\n", "\r\n")))
    return out


def _replace_once(raw, cur, new):
    """Text formats (Markdown, YAML, PO, XML…): `raw` with the one occurrence of `cur`, as the
    format writes it, replaced by `new`. (text, "") or (raw, why not)."""
    already = False
    for c, n in _forms(raw, cur, new):
        for esc in _variants(c):
            needle = esc(c)
            if needle and raw.count(needle) == 1:
                return raw.replace(needle, esc(n)), ""
            if needle and raw.count(needle) > 1:
                return raw, "the same text is in the file more than once — apply it by hand"
            already = already or bool(esc(n) and esc(n) in raw)
    return raw, (ALREADY if already else _NOT_FOUND)


def _replace_in_json(raw, key, cur, new):
    """JSON and ARB: the quoted value under its own key, so the file keeps its layout; through
    the parsed document when the text cannot be told apart (a key repeated at two depths)."""
    q = lambda t: json.dumps(t, ensure_ascii=False)
    name = q(key.rsplit(".", 1)[-1])
    for c, n in _forms(raw, cur, new):
        pat = re.compile(rf"({re.escape(name)}\s*:\s*){re.escape(q(c))}")
        hits = pat.findall(raw)
        if len(hits) == 1:
            return pat.sub(lambda m: m.group(1) + q(n), raw), ""
    try:
        obj = json.loads(raw)
    except ValueError:
        return raw, "the file is not valid JSON"
    for k, path, value in inc._leaves(obj):
        if k != key:
            continue
        if value == new:
            return raw, ALREADY
        if value != cur:
            return raw, "the line was edited since verify — left as it is"
        node = obj
        for seg in path[:-1]:
            node = node[seg]
        node[path[-1]] = new
        return json.dumps(obj, ensure_ascii=False, indent=2) + "\n", ""
    return raw, "the key is not in the file"


def apply_file(path, changes):
    """Write `changes` {key: (current, new)} into one translated file. Returns {key: status}."""
    try:
        with open(path, "rb") as f:
            content = f.read()
        raw = decode_bytes(content, path)
    except (OSError, UnknownEncoding) as e:
        return {k: f"cannot read {path}: {getattr(e, 'strerror', None) or e}" for k in changes}
    json_like = path.lower().endswith((".json", ".arb"))
    result, text = {}, raw
    for key, (cur, new) in changes.items():
        if json_like:
            text, why = _replace_in_json(text, key, cur, new)
        else:
            text, why = _replace_once(text, cur, new)
        result[key] = why or APPLIED
    if text != raw:
        _write(path, text, _encoding_of(content, path))
    return result


def _encoding_of(content, path):
    """The file's own encoding, so a fix does not turn a UTF-16 .strings or a BOM'd .resx
    into something else (the same rules the reader uses, kaeris/encoding.py)."""
    if content.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    wide = _bomless_wide_encoding(content)
    if wide:
        return wide
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "latin-1"                   # .properties, as decode_bytes reads it
    return "utf-8"


def _write(path, text, encoding):
    from .cli import write_atomic          # one writer for every file the CLI touches
    # Bytes, so the line endings stay the file's own: text mode on Windows made every \r\n
    # a \r\r\n.
    write_atomic(path, text.encode(encoding), binary=True)


def plan(review, files, base_dir, glossary=None):
    """For one source file's verify.json section: [(lang, key, target path, current, new, how,
    why)] for every line to review. `files` maps a language to its translated file."""
    rows = []
    for lang, lines in sorted((review or {}).items()):
        target = (files or {}).get(lang)
        path = os.path.normpath(os.path.join(base_dir, target)) if target else None
        for key, entry in lines.items():
            new, how, why = choose(entry, lang, glossary)
            if new is not None and not path:
                new, how, why = None, None, "verify.json does not say which file holds this language"
            rows.append((lang, key, path, entry.get("translation"), new, how, why))
    return rows


def apply(rows, dry_run=False):
    """Write every row that has a new line. Returns rows with their final status."""
    by_file = {}
    for lang, key, path, cur, new, how, why in rows:
        if new is not None:
            by_file.setdefault(path, {})[key] = (cur, new)
    status = {}
    for path, changes in by_file.items():
        res = ({k: APPLIED for k in changes} if dry_run else apply_file(path, changes))
        for k, s in res.items():
            status[(path, k)] = s
    out = []
    for lang, key, path, cur, new, how, why in rows:
        if new is None:
            out.append((lang, key, path, how, SKIPPED, why))
            continue
        s = status.get((path, key), APPLIED)
        if s in (APPLIED, ALREADY):
            out.append((lang, key, path, how, s, ""))
        else:
            out.append((lang, key, path, how, SKIPPED, s))
    return out


def sections(body):
    """verify.json holds one source file's results, or {source path: results} for a run over
    several. Yields (source name or "", section)."""
    if not isinstance(body, dict):
        return
    if "_review" in body or "_files" in body:
        yield "", body
        return
    for name, sec in body.items():
        if isinstance(sec, dict) and ("_review" in sec or "_files" in sec):
            yield name, sec
