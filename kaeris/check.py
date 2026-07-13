"""kaeris check — the "i18n firewall": a local, static comparison of a source
(base-language) locale file against each target locale file.

Pure stdlib, no network/API call. Designed to run in CI and fail the build
(non-zero exit) when a locale is untranslated or a translated string lost/
gained a placeholder — the kind of bug that silently breaks the app at
runtime (`Hello {name}` -> `Bonjour` drops the name; `{name}` -> `{nom}`
just crashes str.format()/ICU).

JSON only for now (matches --only-new's scope elsewhere in the CLI).
"""

import re

from .incremental import flatten_all

# Percent/dollar-style placeholders (order matters: positional and %@ must be
# tried before the bare %s/%d/... class since e.g. "%1$s" would otherwise
# partially match the bare-conversion alternative first).
_PERCENT_RE = re.compile(r"%\d+\$[@a-zA-Z]|%@|%[sdifxocegpnXOCEGP%]")
# ${name} template-literal placeholders. Scanned (and masked) BEFORE the brace
# scanner so a ${x} span isn't also miscounted as a bare {x} — they're distinct
# families: a source ${name} translated to {name} would break at runtime, and
# that mismatch must be caught. (Previously the brace scanner masked ${name}
# down to {name} before this ran, making the regex dead — M2.)
_DOLLAR_RE = re.compile(r"\$\{[\w\d]+\}")
# NOTE: a :word: (colon on both sides) "family" was removed here (M1). It never
# matched the real Rails/Laravel `:name` convention (leading colon only) and it
# false-positived on emoji shortcodes / prose like ":smile:" — failing CI on a
# non-placeholder. No format currently in the pipeline (i18next, next-intl,
# react-intl, vue-i18n, flutter-arb, JSON/percent) relies on a colon placeholder,
# so it's dropped rather than replaced with a leading-colon regex that would
# itself misfire on ordinary text (times "12:30", ratios "3:2").


def _brace_spans(text):
    """Balanced top-level {...} spans in `text`. Covers plain named
    placeholders ({name}), doubled/i18next-style ({{name}}), and ICU
    MessageFormat ({count, plural, one {# item} other {# items}}) as a
    single opaque span each — nested braces don't confuse it."""
    spans = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            if depth == 0:
                spans.append((i, j))
                i = j
                continue
        i += 1
    return spans


def _brace_identity(span_text):
    """Reduce a brace span to a comparable identity: strip doubled braces
    and any ICU plural/select clause, keeping just the argument name, so
    '{{name}}' -> '{name}' and '{count, plural, one {..} other {..}}' -> '{count}'.
    This intentionally ignores ICU sub-clause wording (which legitimately
    differs per language, e.g. Slavic plural categories) — only the bound
    variable name has to match between source and target."""
    inner = span_text.strip("{}")
    while inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    name = inner.split(",")[0].strip()
    return "{" + name + "}"


def find_placeholders(text):
    """Return the set of placeholder identities found in a string."""
    if not isinstance(text, str):
        return set()
    found = set()
    chars = list(text)
    # 1) ${name} template placeholders first — mask each span so the { } inside
    #    isn't also miscounted as a bare {name} brace placeholder (distinct family).
    for m in _DOLLAR_RE.finditer(text):
        found.add(m.group(0))
        for i in range(m.start(), m.end()):
            chars[i] = " "
    masked = "".join(chars)
    # 2) Balanced {...} brace spans (plain/doubled/ICU) on the ${}-masked text.
    spans = _brace_spans(masked)
    found.update(_brace_identity(masked[s:e]) for s, e in spans)
    # 3) Mask brace regions too so percent scanning can't double-count them.
    for s, e in spans:
        for i in range(s, e):
            chars[i] = " "
    remainder = "".join(chars)
    found.update(_PERCENT_RE.findall(remainder))
    return found


def diff_locale(source_flat, target_flat):
    """Compare one target locale's flattened keys against the flattened source.

    Returns (missing, extra, placeholder_issues):
      - missing: sorted dotted keys present in source but absent from target
      - extra: sorted dotted keys present in target but absent from source
      - placeholder_issues: list of {"key", "missing", "added"} dicts, where
        "missing" = placeholders present in source but lost in target, and
        "added" = placeholders present in target but not in source (renamed
        or hallucinated) — either one means the string will misbehave/crash
        at runtime.
    """
    missing = sorted(k for k in source_flat if k not in target_flat)
    extra = sorted(k for k in target_flat if k not in source_flat)

    issues = []
    for key, src_val in source_flat.items():
        if key not in target_flat:
            continue
        tgt_val = target_flat[key]
        if not isinstance(src_val, str) or not isinstance(tgt_val, str):
            continue
        src_ph = find_placeholders(src_val)
        tgt_ph = find_placeholders(tgt_val)
        if src_ph != tgt_ph:
            issues.append({
                "key": key,
                "missing": sorted(src_ph - tgt_ph),
                "added": sorted(tgt_ph - src_ph),
            })
    return missing, extra, issues


def check_locales(source_obj, langs, load_target):
    """Run the check across every target language.

    `load_target(lang)` -> parsed JSON object for that language, or None if
    the file doesn't exist (a whole-locale-missing lang is reported as every
    source key being missing).

    Returns the result dict (this is exactly what --json prints):
      {"ok": bool, "missing": {lang: [keys]}, "extra": {lang: [keys]},
       "placeholder_issues": [{"lang", "key", "missing", "added"}],
       "missing_files": [lang, ...]}
    `ok` is True only if there are no missing keys and no placeholder issues
    (extra/stale keys never affect `ok` — that's what --strict is for).
    """
    source_flat = flatten_all(source_obj)

    result = {
        "ok": True,
        "missing": {},
        "extra": {},
        "placeholder_issues": [],
        "missing_files": [],
    }

    for lang in langs:
        target_obj = load_target(lang)
        if target_obj is None:
            result["missing_files"].append(lang)
            result["missing"][lang] = sorted(source_flat)
            result["ok"] = False
            continue
        target_flat = flatten_all(target_obj)
        missing, extra, issues = diff_locale(source_flat, target_flat)
        if missing:
            result["missing"][lang] = missing
            result["ok"] = False
        if extra:
            result["extra"][lang] = extra
        for issue in issues:
            result["placeholder_issues"].append({"lang": lang, **issue})
        if issues:
            result["ok"] = False

    return result
