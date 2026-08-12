"""SARIF 2.1.0 output for `kaeris check` — findings that show up inside the pull request.

`--json` gives a report someone has to open. SARIF is what GitHub reads: upload it in a
workflow and every finding becomes an annotation on the exact line of the locale file, in
the diff, beside the human reviewer's comments. Same checks, but the developer meets them
where the work already is.

Two details decide whether that actually happens:

  * THE LINE. check_locales speaks in keys ("menu.file.save"); an annotation without a line
    number is dropped on the floor. Every finding is resolved back to the line where its key
    sits in the file it belongs to.
  * THE PATH. GitHub matches annotations to the diff by REPO-RELATIVE path. An absolute path
    is silently accepted and annotates nothing, which looks exactly like "no problems found".

Zero dependencies, like the rest of the CLI.
"""
import json
import os
import re

from .encoding import read_text

SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

# Each finding carries a ruleId so GitHub can group them and a developer can mute a class of
# them. Text is what appears in the Security tab's rule list.
RULES = [
    ("missing-key",       "Key missing from a locale",
     "A key present in the source file has no translation in this locale."),
    ("placeholder",       "Placeholder lost or altered",
     "A placeholder ({name}, %s, %d, <b>…) present in the source is missing or changed type "
     "in the translation — this breaks formatting at runtime."),
    ("translation-fault", "Translation fault",
     "A deterministic defect in the translation: dropped glossary term, plural form missing "
     "for the language's CLDR rules, number or currency drift, broken entity, ICU syntax."),
    ("translation-warning", "Translation warning",
     "A soft signal worth a look: text left in the source language, register drift, "
     "cross-key inconsistency, or a translation long enough to overflow the UI."),
    ("extra-key",         "Key not in the source",
     "A key exists in this locale but not in the source file — usually a leftover after a "
     "rename or deletion."),
]


def line_of_key(path: str, key: str) -> int:
    """1-based line where `key` is defined in a JSON/ARB file, or 1 if it cannot be found.

    Deliberately textual rather than a parse: it must work on the file as the developer sees
    it (comments, ordering, formatting all intact), and a wrong line is worse than the top of
    the file only in theory — GitHub still shows the annotation either way.

    Nested keys are matched on their LAST segment ("menu.file.save" → "save"), because that
    is the only part that appears as a JSON key. The regex requires the quoted name followed
    by a colon, so a key that also occurs as a VALUE elsewhere is not mistaken for it.
    """
    if not key:
        return 1
    leaf = key.split(".")[-1]
    pattern = re.compile(r'^\s*"' + re.escape(leaf) + r'"\s*:')
    try:
        for n, line in enumerate(read_text(path).splitlines(), 1):
            if pattern.match(line):
                return n
    except (OSError, ValueError):
        # Номер строки — украшение отчёта: файл в неизвестной кодировке уже отвергнут выше,
        # а здесь молчаливая единица лучше падения на пути к SARIF.
        return 1
    return 1


def _rel(path: str, root: str) -> str:
    """Repo-relative, forward slashes — the only shape GitHub can match to a diff."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:                       # different drives on Windows
        rel = path
    return rel.replace(os.sep, "/")


def _result(rule_id: str, level: str, text: str, uri: str, line: int) -> dict:
    return {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": text},
        "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": uri},
                "region": {"startLine": max(1, int(line))},
            }
        }],
    }


def build(result: dict, source: str, target_for, root: str = ".") -> dict:
    """Turn a check_locales result into a SARIF document.

    target_for(lang) -> path of that language's file, so a finding lands on the locale that
    has the problem rather than on the source everyone shares.
    """
    results = []

    def add(lang, key, rule, level, text):
        path = target_for(lang) if lang else source
        results.append(_result(rule, level, text, _rel(path, root),
                               line_of_key(path, key) if key else 1))

    for lang, keys in (result.get("missing") or {}).items():
        for key in keys:
            add(lang, None, "missing-key", "error",
                f"[{lang}] missing translation for key \"{key}\"")

    for lang, keys in (result.get("extra") or {}).items():
        for key in keys:
            add(lang, key, "extra-key", "note",
                f"[{lang}] key \"{key}\" is not in the source file")

    for item in (result.get("placeholder_issues") or []):
        lang, key = item.get("lang"), item.get("key")
        add(lang, key, "placeholder", "error",
            f"[{lang}] {key}: {item.get('msg') or 'placeholder mismatch'}")

    for item in (result.get("faults") or []):
        lang, key = item.get("lang"), item.get("key")
        level = "error" if item.get("severity", "error") == "error" else "warning"
        add(lang, key, "translation-fault", level, f"[{lang}] {key}: {item.get('msg', '')}")

    for item in (result.get("warnings") or []):
        lang, key = item.get("lang"), item.get("key")
        add(lang, key, "translation-warning", "warning",
            f"[{lang}] {key}: {item.get('msg', '')}")

    for lang in (result.get("missing_files") or []):
        results.append(_result("missing-key", "error",
                               f"[{lang}] no locale file found for this language",
                               _rel(source, root), 1))

    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "KAERIS",
                "informationUri": "https://kaeris.dev",
                "rules": [{"id": rid, "name": name,
                           "shortDescription": {"text": name},
                           "fullDescription": {"text": desc},
                           "helpUri": "https://kaeris.dev/developer.html"}
                          for rid, name, desc in RULES],
            }},
            "results": results,
        }],
    }


def write(path: str, doc: dict) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
