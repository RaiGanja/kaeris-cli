"""Offline, deterministic i18n quality detectors — a byte-faithful copy of the pure
detector layer in backend/translator.py. NO network, NO model, NO file IO here.
Parity with the backend is proven by cli/tests/test_detector_parity.py.
Stdlib only: re, collections."""

from __future__ import annotations   # keeps `list[str] | None` hints from being EVALUATED at
                                     # def time, so this module imports on Python 3.8/3.9 too —
                                     # the package advertises >=3.8 and `kaeris check` must run
                                     # wherever pip let it install.
import re
import collections

# Placeholders, longest/most-specific forms FIRST so e.g. {{name}} isn't split into {name}
# and %{name} isn't split into % + {name}:
# Widths/flags use BOUNDED repeats ({0,6}/{0,8}), not possessive `*+`: bounded repetition
# denies ReDoS the same way (no unbounded ambiguous overlap — '0' lives in both the flag class
# and \d), and unlike `*+` it parses on Python < 3.11. With possessive quantifiers this module
# raised `re.error: multiple repeat` at import on 3.8-3.10, so `kaeris check` — the free
# offline firewall, and the Action's check mode — died on every runner older than 3.11.
#   {{ name }}  Vue/Angular/Handlebars/Mustache      %{name}   Rails i18n
#   %(name)s  Python/Django dict printf     {name} {0} {0:C}  brace + .NET composite
#   %1$s / %2$@  positional (Android/iOS)   %@  iOS   %s/%d printf
#   ${name}  JS template     :name:  Rails-ish
_PH_RE = re.compile(
    r"\{\{\s*[\w\d_.]+\s*\}\}"
    r"|%\{[\w\d_]+\}"
    r"|%\([\w\d_]+\)[-+0#]{0,6}\d{0,8}(?:\.\d{1,8})?[a-zA-Z]"   # Python dict, incl. %(name).2f width/precision
    r"|\{[\w\d_]+(?:,-?\d+)?(?::[^}]*)?\}"   # brace + .NET composite incl. alignment {0,-10:N2} {1,5}
    r"|%\d+\$[-+0# ]{0,6}\d{0,8}(?:\.\d{1,8})?[@a-zA-Z]"   # positional printf incl. flags/width/precision %1$02d %2$-5s
    r"|%@"
    r"|%[-+0#]{0,6}\d{0,8}(?:\.\d{1,8})?(?:hh|ll|[hlLzjt])?[sSdiouxXeEfFgGaAcCpn%]"  # printf incl. %.2f %02d %-10s %u %ld
    r"|\$\{[\w\d_]+\}"
    r"|:[A-Za-z]\w*:"   # Rails-ish :name: — must START with a letter so time (10:30:45) and ratios (3:4:5) aren't mis-read as placeholders
)

# A simple {var} that became an ICU head {var, plural|select|selectordinal, …}. Captures the
# variable name — the ARGUMENT of the block, which is the real placeholder.
_ICU_SELECTOR_VAR_RE = re.compile(r"\{\s*([\w\d_]+)\s*,\s*(?:plural|select|selectordinal)\s*,")

def _placeholder_list(text: str) -> list[str]:
    """Placeholders as ICU semantics define them, not as a flat regex sees them.

    An ICU block is STRUCTURE, not text: in {count, plural, one {# file} other {# files}}
    the argument is `count` (its value renders through #), while `{# file}` and `{He}` are
    ARMS — human text that merely happens to sit in braces. A flat scan reads those arms as
    placeholders, so a CORRECT translation reports the source arms as lost and the target
    arms as invented — e.g. a three-case select yields five phantom faults, in exactly the
    --icu mode that asked the model to produce the construct. \\w is Unicode-aware, so this
    bites hardest in non-Latin locales ({خطأان} read as an invented placeholder).

    Arms are RECURSED into, so a genuine placeholder nested in an arm (`other {Hi {name}}`)
    still counts and its loss is still reported. Anything that isn't an ICU block — {0:C},
    {{name}}, %{name} — is left to the flat scan and behaves exactly as before.

    Returns a list (not a set) so callers can count occurrences for arity checks.
    """
    out: list[str] = []
    rest: list[str] = []        # everything outside ICU blocks, scanned flat
    pos = 0
    for bs, be in _brace_spans(text):
        seg = text[bs:be]
        head = _ICU_HEAD_RE.match(seg)
        if not head:
            continue            # not ICU — leave it in `rest` for the flat scan
        rest.append(text[pos:bs])
        pos = be
        var = _ICU_SELECTOR_VAR_RE.match(seg)
        if var:
            out.append("{" + var.group(1) + "}")     # the argument IS the placeholder
        body = seg[head.end():-1]                    # arms only, after "{name, type,"
        for as_, ae in _brace_spans(body):
            out.extend(_placeholder_list(body[as_ + 1:ae - 1]))
    rest.append(text[pos:])
    # Join with a space so removing a block can't fuse two neighbours into a false match.
    out.extend(_PH_RE.findall(" ".join(rest)))
    return out

def _icu_visible_text(text: str) -> str:
    """The text a user actually SEES: ICU markup replaced by the arm that renders longest
    (the worst case for layout), nested blocks resolved recursively. `#` is left in place —
    it stands for the number, which is 1-3 characters in practice. Non-ICU text is returned
    untouched, so callers that never meet a plural behave exactly as before."""
    out: list[str] = []
    pos = 0
    for bs, be in _brace_spans(text):
        seg = text[bs:be]
        head = _ICU_HEAD_RE.match(seg)
        if not head:
            continue
        out.append(text[pos:bs])
        pos = be
        body = seg[head.end():-1]
        arms = [_icu_visible_text(body[s + 1:e - 1]) for s, e in _brace_spans(body)]
        out.append(max(arms, key=len) if arms else "")
    out.append(text[pos:])
    return "".join(out)

def _find_placeholders(text: str) -> set[str]:
    return set(_placeholder_list(text))

def _lost_placeholders(original: str, translated: str) -> list[str]:
    return sorted(_find_placeholders(original) - _find_placeholders(translated))

def _placeholder_arity(text: str) -> tuple[collections.Counter, collections.Counter]:
    """How many times a placeholder can appear in the RENDERED string — (most, fewest).

    ICU arms are alternatives: exactly one is chosen at runtime, so a placeholder used once
    in every arm renders once, no matter how many arms there are. Counting the flat list
    instead makes correct output look broken the moment plural rules differ from English —
    and they differ almost everywhere: Arabic needs six arms, Russian four, Japanese one.
    A source with `{count, plural, one {Hi {name}…} other {Hi {name}…}}` lists {name} twice;
    its Arabic translation lists it six times, its Japanese once. Compared flatly that is a
    "duplicated" fault and a "dropped" fault on two perfectly correct translations — in
    exactly the --icu mode that asked for those arms.

    So: count outside the ICU blocks normally, and inside take the max across arms (for
    duplication) and the min (for a drop inside SOME arm, which is a real bug — that arm
    renders without the value). Nested blocks recurse.
    """
    outside: list[str] = []
    arm_max: collections.Counter = collections.Counter()
    arm_min: collections.Counter = collections.Counter()
    pos = 0
    for bs, be in _brace_spans(text):
        seg = text[bs:be]
        head = _ICU_HEAD_RE.match(seg)
        if not head:
            continue
        outside.append(text[pos:bs])
        pos = be
        var = _ICU_SELECTOR_VAR_RE.match(seg)
        if var:
            outside.append("{" + var.group(1) + "}")   # the argument renders once
        body = seg[head.end():-1]
        per_arm = []
        for as_, ae in _brace_spans(body):
            mx, mn = _placeholder_arity(body[as_ + 1:ae - 1])
            per_arm.append((mx, mn))
        if per_arm:
            keys = set().union(*[set(mx) for mx, _ in per_arm])
            for k in keys:
                arm_max[k] += max(mx.get(k, 0) for mx, _ in per_arm)
                arm_min[k] += min(mn.get(k, 0) for _, mn in per_arm)
    outside.append(text[pos:])
    flat = collections.Counter(_PH_RE.findall(" ".join(outside)))
    return flat + arm_max, flat + arm_min


def _placeholder_type_faults(original: str, translated: str) -> list[str]:
    """Deterministic placeholder faults a SET-based check (_lost_placeholders) cannot express:
      1. a placeholder the model INVENTED — present in the translation, absent from the
         source. A hallucinated arg that crashes or misformats at runtime. A `%s` that became
         `%d` surfaces here as an invented `%d` (with _lost_placeholders reporting the missing
         `%s`) — together they flag the runtime-crashing TYPE swap.
      2. ARITY drift — a placeholder used N times in the source appearing a different number
         of times in the translation. Set difference collapses the count and misses this.
    Single drops (N→0 for a once-used placeholder) are left to _lost_placeholders so the two
    checks don't double-report."""
    src_max, src_min = _placeholder_arity(original)
    tr_max, tr_min = _placeholder_arity(translated)
    faults: list[str] = []
    for ph in sorted(set(src_max) | set(tr_max)):
        if ph == "%%":                       # literal percent, not an argument
            continue
        if tr_max[ph] > src_max[ph]:
            faults.append(f"invented placeholder {ph} (not in source)" if src_max[ph] == 0
                          else f"placeholder {ph} appears {tr_max[ph]}× vs {src_max[ph]}× in source (duplicated)")
        elif src_min[ph] > tr_min[ph]:
            # Present in every source arm, missing from at least one translated arm — that arm
            # renders without the value. A placeholder that vanished ENTIRELY is left to
            # _lost_placeholders (tr_max == 0) so the two checks never double-report the same
            # fault; this branch is specifically the per-arm drop a set difference cannot see.
            if tr_max[ph] == 0:
                continue
            faults.append(
                f"placeholder {ph} is missing from some plural/select arms "
                f"({tr_min[ph]}× in the thinnest arm vs {src_min[ph]}× in the source)"
                if tr_max[ph] != tr_min[ph] or src_max[ph] != src_min[ph]
                else f"placeholder {ph} appears {src_min[ph]}× in source but {tr_min[ph]}× in translation")
    return faults

# Digit runs incl. non-ASCII digits (Arabic-Indic, Persian, Devanagari) and locale
# grouping/decimal separators, so "1,000" / "1.000" / "1 000" and "١٢٣" are all seen.
_NUM_RUN_RE = re.compile(
    r"[0-9٠-٩۰-۹०-९০-৯๐-๙]"
    r"[0-9٠-٩۰-۹०-९০-৯๐-๙.,'   ]*")
_DIGIT_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹०१२३४५६७८९০১২৩৪৫৬৭৮৯๐๑๒๓๔๕๖๗๘๙", "0123456789" * 5)

# Genuine decimal separators (comma/dot/Swiss apostrophe). Spaces/NBSP are grouping-only, so
# they never need to be considered when deciding "decimal vs thousands".
_NUM_SEP = ",.'"

def _num_key(run: str) -> str:
    """Normalized numeric key. Tolerates locale grouping (1,000 == 1.000 == 1 000 000) yet still
    distinguishes a decimal VALUE (9.99 != 999, 1.5 != 15). Heuristic: the digit group after the
    LAST separator is a decimal fraction UNLESS it is exactly 3 digits long, in which case that
    separator is grouping and the run is a plain integer. Ambiguous 3-digit tails (e.g. '1.000')
    are treated as grouping on purpose — we'd rather miss the rare true-3-decimal case than flag
    1,000 vs 1.000 as a change and break the zero-false-positive record."""
    t = run.translate(_DIGIT_TRANS)
    last = max(t.rfind(c) for c in _NUM_SEP)
    if last != -1:
        head = re.sub(r"[^0-9]", "", t[:last])
        tail = re.sub(r"[^0-9]", "", t[last + 1:])
        if head and tail and len(tail) != 3:      # decimal fraction, not a thousands group
            return head + "." + tail
    return re.sub(r"[^0-9]", "", t)

def _extract_numbers(text: str) -> tuple[collections.Counter, dict[str, str]]:
    """(counts, display) for the numeric VALUES in `text`, separator- and script-normalized so
    locale formatting isn't mistaken for a value change: placeholders are stripped first (so a
    `%2$d`/`{0}` index isn't counted as content), non-ASCII digits are transliterated, and
    grouping separators are dropped for the KEY while a genuine decimal fraction is kept (see
    _num_key) — '1,000'/'1.000' both key to '1000' but '9.99' keys to '9.99', not '999'.
    `display` maps each normalized key back to the original run (e.g. '9.99' -> '$9.99') so
    warnings quote what the human actually sees."""
    stripped = _PH_RE.sub(" ", text)
    counts: collections.Counter = collections.Counter()
    display: dict[str, str] = {}
    for run in _NUM_RUN_RE.findall(stripped):
        run = run.strip(" ,.'  ")
        key = _num_key(run)
        if key:
            counts[key] += 1
            display.setdefault(key, run)
    return counts, display

# Small numbers written as words. English spells them out, CJK and most locales write digits,
# and neither is a change of value: "at least one language" → "少なくとも1つの言語" drifted
# nothing. Without this, translating an English UI into Japanese produced a stream of invented
# -number errors that were not errors — and warnings nobody believes are warnings nobody reads.
# Only the English side is listed: it is the source language in essentially every job, and a
# table per target language would be a large surface for the sake of a rare case.
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    # Ordinals too — "a third attempt" becomes "3回目" in Japanese just the same.
    # "second" is deliberately ABSENT: in English it is also a unit of time, and "wait a
    # second" is far more common in a UI than "a second pass". Including it would trade a
    # rare miss for a frequent false alarm, which is the wrong way round for a detector
    # people have to keep trusting.
    "first": 1, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
# Whole words only: "someone", "atonement" and "stone" must never read as "one".
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)


# An ICU arm label — `one {`, `two {`, `=0 {` — is syntax, not prose. Matched only where a
# label can legally stand: after the construct's comma or after a previous arm's closing brace.
_ICU_ARM_LABEL_RE = re.compile(r"(?<=[,}])(\s*)(=\d+|\w+)(\s*)(?=\{)")


def _spelled_numbers(text: str) -> set:
    """Numbers the ENGLISH source spells out, as their values.

    Used only to FORGIVE, never to accuse — see _numeric_faults. Running this table over a
    translation instead was a net loss when measured: it cleared four false alarms in Japanese
    and created thirteen elsewhere, because English number words are ordinary words in other
    languages. Portuguese "começa do zero" ("starts from scratch") was read as the number nought.

    The CLDR labels of a plural are stripped first: `one` in `one {# item}` is ICU syntax, and
    reading it as the word "one" made every plural source forgive a literal 1 in its
    translation. Russian `one` covers 1, 21 and 31, so `one {1 уведомление}` shows "1" to a
    user who has 21 — a wrong number, silently allowed. Prose is untouched, so a source that
    really says "at least one language" still forgives.
    """
    return {_num_key(str(_NUMBER_WORDS[m.group(1).lower()]))
            for m in _NUMBER_WORD_RE.finditer(_ICU_ARM_LABEL_RE.sub(r"\1\3", text or ""))}

# Languages whose counter system spells singularity as a numeral: English "an hour" is
# Japanese "1時間". The article carries the meaning in one and a digit carries it in the other,
# so the digit is grammar rather than an invented value. Regional tags share the writing
# system, so the base subtag is what matters (zh-Hant, ko-KR, ja-JP).
_COUNTER_LANGS = {"ja", "zh", "ko", "yue", "wuu"}
# The English indefinite article as a WORD — not the "a" inside "analytics".
_INDEFINITE_ARTICLE_RE = re.compile(r"\b(a|an)\b", re.I)


def _numeric_faults(original: str, translated: str, lang: str = "") -> list[str]:
    """Deterministic number-drift check. Catches the dangerous silent case a meaning-judge can
    miss because the sentence still reads fine: 'Delete 5 files' -> 'Delete 50 files', a wrong
    price, a mangled version. No mainstream TMS diffs the numeric CONTENT of a translation.
    Guarded against two false positives: locale grouping (1,000 vs 1.000 normalize equal) and
    a fully spelled-out translation ('5' -> 'fünf' with no digits left) — the latter is skipped
    because a translation with NO digits is a legitimate spell-out, not a dropped value.

    `lang` adds a third, narrow one: a CJK counter turns the English article into a numeral,
    so "an hour" -> "1時間" is a correct translation carrying a digit the source never had.
    Forgiven only for the value 1, only when the source really has an article, and only for
    those languages — Russian writes "через час", so a 1 there is still worth reporting.
    Omitting `lang` keeps the original strict behaviour for every existing caller."""
    src, src_disp = _extract_numbers(original)
    tr, tr_disp = _extract_numbers(translated)
    if src == tr:
        return []
    faults: list[str] = []
    spelled = _spelled_numbers(original)
    base = (lang or "").replace("_", "-").split("-")[0].lower()
    if base in _COUNTER_LANGS and _INDEFINITE_ARTICLE_RE.search(original):
        spelled = spelled | {_num_key("1")}
    for num in sorted(set(tr - src) - spelled):
        faults.append(f"number {tr_disp[num]} appears in the translation but not the source")
    if tr:                                   # skip when the translation spelled every number out
        for num in sorted(src - tr):
            faults.append(f"number {src_disp[num]} from the source is missing or changed in the translation")
    return faults

# A `&`-entity whose leading `&` was itself re-escaped: &amp;amp; / &amp;lt; / &amp;#39; …
# This is the classic MT round-trip corruption — the model saw `&amp;`, "translated" the
# text, and re-encoded the ampersand, so what ships renders the literal "&amp;" to the user.
_DOUBLE_ENC_RE = re.compile(r"&amp;(?:amp|lt|gt|quot|apos|nbsp|#\d{1,7}|#x[0-9a-fA-F]{1,6});")
# `\u` not followed by exactly 4 hex digits — a mangled JSON/JS unicode escape.
_BROKEN_UNICODE_ESC_RE = re.compile(r"\\u(?![0-9a-fA-F]{4})")

def _entity_faults(original: str, translated: str) -> list[str]:
    """Deterministic escape/entity corruption a meaning-judge and a placeholder check both
    miss: an HTML entity double-encoded during the round-trip (&amp; → &amp;amp;, so the user
    literally sees "&amp;"), and a \\uXXXX escape mangled into non-hex. Only NEW corruption is
    flagged — a double-encoding already present in the source is left alone."""
    faults: list[str] = []
    src = collections.Counter(_DOUBLE_ENC_RE.findall(original))
    tr = collections.Counter(_DOUBLE_ENC_RE.findall(translated))
    for ent in sorted(tr - src):
        faults.append(f"double-encoded entity {ent} — the & was re-escaped (renders literally)")
    if _BROKEN_UNICODE_ESC_RE.search(translated) and not _BROKEN_UNICODE_ESC_RE.search(original):
        faults.append("broken \\u escape — a unicode escape was mangled to non-hex")
    return faults

# Inline markup that must survive translation: HTML/XML tags — <b>, </b>, <a href="…">,
# <br/>, <x id="1"/> — plus i18next <Trans> numeric tags <0>, </1>. Requires a letter or
# digit right after '<' (or '</') immediately followed by tag chars then '>', so "a < b" and
# "I <3 you" are NOT read as tags (no closing '>'). We compare the MULTISET of normalized tag
# shapes (name + open/close/self-close, attributes stripped) so a dropped/duplicated tag is
# caught while attribute-order or inner-text changes are not false positives.
_TAG_RE = re.compile(r"</?[a-zA-Z0-9][\w:-]*(?:\s[^<>]*?)?/?>")

def _find_tags(text: str) -> list[str]:
    out: list[str] = []
    for m in _TAG_RE.finditer(text):
        raw = m.group(0)
        name = re.match(r"</?\s*([a-zA-Z0-9][\w:-]*)", raw).group(1)
        if raw.startswith("</"):
            out.append(f"</{name}>")
        elif raw.rstrip().endswith("/>"):
            out.append(f"<{name}/>")
        else:
            out.append(f"<{name}>")
    return out

def _lost_tags(original: str, translated: str) -> list[str]:
    """Tags present in `original` but missing (or fewer) in `translated`."""
    o: dict[str, int] = {}
    for tg in _find_tags(original):
        o[tg] = o.get(tg, 0) + 1
    for tg in _find_tags(translated):
        if tg in o:
            o[tg] -= 1
    lost: list[str] = []
    for tg, n in o.items():
        if n > 0:
            lost.extend([tg] * n)
    return sorted(lost)

def _brace_spans(text: str) -> list[tuple[int, int]]:
    """Balanced top-level {...} spans. Covers both simple named placeholders ({name})
    and nested ICU MessageFormat ({count, plural, one {# item} other {# items}}) as ONE
    opaque unit each — partially transforming ICU plural/select syntax would corrupt it,
    so the whole construct is preserved verbatim rather than parsed.

    Single pass, O(n): a stack of unmatched '{' positions; a '}' that empties the stack
    closes a top-level span. (The old nested-scan was O(n^2) on unbalanced input like
    "{"*N — each '{' rescanned to end-of-string — a CPU-DoS via /api/pseudo.)"""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, ch in enumerate(text):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            if not stack:               # closed a top-level brace
                spans.append((start, i + 1))
    return spans

# ICU MessageFormat plural/select validation. CLDR cardinal categories a target language
# REQUIRES beyond 'other' (always required, checked separately). Conservative on purpose —
# only the unambiguous "extra" forms (Slavic few/many, Arabic) are listed, so a correct
# one/other collapse (en/de/ja/…) never false-positives, and CLDR-version-sensitive additions
# (Romance 'many') are deliberately omitted.
_CLDR_REQUIRED: dict[str, set[str]] = {
    "ru": {"one", "few", "many"}, "uk": {"one", "few", "many"}, "pl": {"one", "few", "many"},
    "cs": {"one", "few"}, "sk": {"one", "few"}, "hr": {"one", "few"}, "sr": {"one", "few"},
    "lt": {"one", "few"}, "ro": {"one", "few"},
    # Arabic uses all six: 0 and 2 have their own forms, not just few/many.
    "ar": {"zero", "one", "two", "few", "many"},
    "lv": {"zero", "one"}, "sl": {"one", "two", "few"},
    # Hebrew has a dual — 2 takes its own form.
    "he": {"one", "two"},
}
_ICU_HEAD_RE = re.compile(r"\{\s*[\w\d_]+\s*,\s*(plural|selectordinal|select)\s*,")

def _icu_blocks(text: str) -> list[tuple[str, str]]:
    """Top-level ICU plural/select/selectordinal blocks as (type, full_segment). A block that
    became brace-unbalanced (broken by translation) is simply not returned by _brace_spans —
    so a drop in block count between source and translation flags a broken/lost construct."""
    out = []
    for s, e in _brace_spans(text):
        seg = text[s:e]
        m = _ICU_HEAD_RE.match(seg)
        if m:
            out.append((m.group(1), seg))
    return out

def _icu_arms(seg: str) -> tuple[str, set, set] | None:
    """(type, category_keywords, exact_matches) for one ICU block. Categories are CLDR
    keywords (one/few/many/other/…) or select cases; exacts are literal =N branches."""
    m = _ICU_HEAD_RE.match(seg)
    if not m:
        return None
    body = seg[m.end():-1]                # arms only: after "{name, type," up to the final "}"
    keywords, exacts = set(), set()
    for s, e in _brace_spans(body):
        km = re.search(r"(=\d+|[\w]+)\s*$", body[:s])   # token right before this arm's {…}
        if km:
            tok = km.group(1)
            (exacts if tok.startswith("=") else keywords).add(tok)
    return m.group(1), keywords, exacts

def _icu_faults(original: str, translated: str, lang: str) -> list[str]:
    """Deterministic ICU MessageFormat validation — a class Crowdin/Lokalise/Phrase only
    enforce inside their editor when the string is registered as a plural. Flags: a plural/
    select construct dropped or brace-broken by the translation; a missing required 'other'
    arm (ICU won't compile); a dropped literal =N exact-match branch (e.g. the =0 'empty'
    case); and a plural missing a CLDR form the TARGET language requires (Russian few/many)."""
    src = _icu_blocks(original)
    tr = _icu_blocks(translated)
    faults: list[str] = []
    if len(src) > len(tr):
        faults.append(f"ICU plural/select construct dropped or broken ({len(src)} in source, {len(tr)} in translation)")
    base = (lang or "").replace("_", "-").split("-")[0].lower()
    for typ, seg in tr:
        arms = _icu_arms(seg)
        if not arms:
            continue
        _, kws, _exacts = arms
        if "other" not in kws:
            faults.append(f"ICU {typ} is missing the required 'other' branch")
        # Only cardinal `plural`. _CLDR_REQUIRED holds CARDINAL categories; ordinal ones
        # differ, so applying them to `selectordinal` false-flags a correct ordinal (Russian
        # ordinals need only `other`). We have no ordinal table, so skip completeness for
        # selectordinal rather than warn wrongly (the 'other'-branch check above still applies).
        if typ == "plural":
            missing = _CLDR_REQUIRED.get(base, set()) - kws
            if missing:
                faults.append(f"ICU plural is missing the {'/'.join(sorted(missing))} form(s) {base} requires")
    # dropped literal =N branches (pair blocks positionally when counts match; else union)
    def _exacts(blocks):
        return [a[2] for a in (_icu_arms(s) for _, s in blocks) if a]
    se, te = _exacts(src), _exacts(tr)
    src_ex = set().union(*se) if se else set()
    tr_ex = set().union(*te) if te else set()
    for ex in sorted(src_ex - tr_ex):
        faults.append(f"ICU exact-match branch {ex} {{…}} was dropped in the translation")
    return faults

# Formal/informal (T–V) register markers per base language. Informal is matched
# case-insensitively; the German FORMAL "Sie/Ihnen" is case-SENSITIVE on purpose (lowercase
# "sie" means she/they). Strong, unambiguous markers only — no mainstream TMS audits register
# CONSISTENCY across a whole file with zero configuration.
_REGISTER_MARKERS: dict[str, tuple[re.Pattern, re.Pattern]] = {
    "de": (re.compile(r"\b(?:du|dich|dir|dein\w*)\b", re.I), re.compile(r"\bSie\b|\bIhnen\b")),
    "fr": (re.compile(r"\b(?:tu|toi|ton|tes)\b", re.I), re.compile(r"\bvous\b|\bvotre\b|\bvos\b", re.I)),
    "ru": (re.compile(r"\b(?:ты|тебя|тебе|тобой|твой|тво[яеёи]\w*)\b", re.I), re.compile(r"\b(?:вы|вас|вам|вами|ваш\w*)\b", re.I)),
    "es": (re.compile(r"\b(?:tú|ti|tus)\b", re.I), re.compile(r"\b(?:usted|ustedes)\b", re.I)),
    "it": (re.compile(r"\b(?:tu|tuo|tua|tuoi|tue)\b", re.I), re.compile(r"\bLei\b|\bLei,")),
}

def _register_faults(translated_flat: dict[str, str], lang: str) -> list[str]:
    """FILE-LEVEL: a T–V language app should pick ONE register. Flags a file that mixes
    informal (du/tu/ты) with formal (Sie/vous/вы) address. Deterministic; no TMS does this
    without a per-project config. Conservative: fires only when BOTH registers appear >= 2
    times, so a single quoted line or stray marker doesn't trip it."""
    base = (lang or "").replace("_", "-").split("-")[0].lower()
    markers = _REGISTER_MARKERS.get(base)
    if not markers:
        return []
    inf_re, form_re = markers
    text = "\n".join(translated_flat.values())
    inf = len(inf_re.findall(text))
    formal = len(form_re.findall(text))
    if inf >= 2 and formal >= 2:
        return [f"file mixes informal and formal address ({inf} informal vs {formal} formal markers) — pick one register"]
    return []

# Typical text expansion vs an English source, by base language (character-count proxy;
# well-documented localization figures). Used to CALIBRATE the overflow heuristic per language
# so a German label that grew the expected ~35% isn't flagged like an anomaly, while a language
# that usually stays compact is caught when it unexpectedly runs long. Compact scripts (CJK) are
# deliberately left to the client-side PIXEL measurement — character count under-represents their
# rendered width, so the char heuristic here only tightens, never loosens, for them.
_LANG_EXPANSION: dict[str, float] = {
    "de": 1.35, "nl": 1.35, "fi": 1.5, "sv": 1.3, "da": 1.3, "no": 1.3, "hu": 1.35,
    "fr": 1.25, "es": 1.25, "pt": 1.25, "it": 1.2, "ro": 1.3, "el": 1.3, "tr": 1.25,
    "pl": 1.3, "cs": 1.3, "sk": 1.3, "ru": 1.2, "uk": 1.2, "vi": 1.3, "id": 1.2,
    "zh": 0.4, "ja": 0.55, "ko": 0.7, "th": 0.9,
}


def _compute_overflow(source_flat: dict[str, str], translated_flat: dict[str, str],
                      lang: str = "") -> list[dict]:
    """Flag translations likely to overflow UI (short labels that grew a lot). Length is a strong,
    free proxy for layout breakage. Language-aware: the trigger is scaled to the target language's
    TYPICAL expansion, so an expected German/Finnish stretch doesn't cry wolf while a genuine
    outlier (or a normally-compact language that ran long) still trips it."""
    base = (lang or "").replace("_", "-").split("-")[0].lower()
    expected = _LANG_EXPANSION.get(base, 1.2)
    # Overflow is a LAYOUT question, so measure what RENDERS, not what's authored: an ICU
    # block is markup the user never sees ("{count, plural, one {# Fehler} other {# Fehler}}
    # gefunden" displays as "5 Fehler gefunden" — shorter than the source, yet raw length
    # reads +185%). Applied to both sides, so the comparison stays honest either way.
    _vis = _icu_visible_text
    # Trigger 18% beyond the language's norm, but never below the original absolute 1.4 floor —
    # a >40% stretch is a real risk for a tight UI in any language.
    threshold = max(1.4, expected * 1.18)
    typical_pct = round((expected - 1) * 100)
    out = []
    for k, src in source_flat.items():
        tr = translated_flat.get(k, "")
        if not isinstance(tr, str) or not src:
            continue
        slen, tlen = len(_vis(src)), len(_vis(tr))
        if slen == 0:
            continue
        ratio = tlen / slen
        if slen <= 60 and tlen >= 8 and ratio >= threshold:
            out.append({"key": k, "src": src, "tr": tr,
                        "pct": round((ratio - 1) * 100),
                        "typical": typical_pct})
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out[:50]

# Wrong-language guard: for languages written in a distinctive script, a correct
# translation must actually contain that script. If it doesn't, the model translated into
# the wrong language (or not at all). Latin-script languages (de/fr/es…) share the alphabet
# with English and can't be checked this way, so they are deliberately absent here.
_LANG_SCRIPT_RE = {
    "ru": re.compile(r"[Ѐ-ӿ]"), "uk": re.compile(r"[Ѐ-ӿ]"),
    "bg": re.compile(r"[Ѐ-ӿ]"),
    "el": re.compile(r"[Ͱ-Ͽ]"),
    "ar": re.compile(r"[؀-ۿݐ-ݿ]"),
    "fa": re.compile(r"[؀-ۿݐ-ݿ]"),
    "he": re.compile(r"[֐-׿]"),
    "zh": re.compile(r"[㐀-鿿]"),
    "zh-Hant": re.compile(r"[㐀-鿿]"),
    "ja": re.compile(r"[぀-ヿ㐀-鿿]"),
    "ko": re.compile(r"[가-힣]"),
    "th": re.compile(r"[฀-๿]"),
    "hi": re.compile(r"[ऀ-ॿ]"),
    "bn": re.compile(r"[ঀ-৿]"),
}

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
# A lowercase-initial Latin word of >= 4 letters — the signal that stripped text is natural
# language ("save changes") rather than a Title-Case brand phrase ("GitHub Actions").
_LOWERCASE_WORD_RE = re.compile(r"\b[a-zß-öø-ÿ][A-Za-zÀ-ÿ]{3,}\b")

def _untranslated_string(original: str, translated: str, lang: str,
                         glossary: list[str] | None = None) -> list[str]:
    """Per-string leftover-source detector: an individual value the model left in the source
    language. The file-level echo/script ratios (_translation_quality) miss this when only a
    handful of strings slip. Non-Latin-script targets only (Latin-from-Latin can't be told
    apart this way). Fires when the translation carries NO target-script character yet still
    contains a real lowercase word (>= 4 letters) after placeholders/tags/URLs/numbers/glossary
    are stripped — the lowercase word separates an untranslated SENTENCE ('save changes') from
    a Title-Case brand phrase ('GitHub Actions') that legitimately stays in Latin."""
    base = (lang or "").replace("_", "-").split("-")[0].lower()
    if base == "en":
        return []                       # an English source going to English is identical by definition
    rx = _LANG_SCRIPT_RE.get(lang) or _LANG_SCRIPT_RE.get(base)

    # Two ways for a string to give itself away, because the script test only works when the
    # target has its own alphabet — that is 14 of our 46 languages. The other 32 are Latin,
    # and English sitting inside German is Latin inside Latin.
    #
    # Found the hard way: 199,962 characters into German on the paid model returned 40 strings
    # in English out of 3,174. Nothing noticed. The file-level echo ratio needs 70% before it
    # speaks and this was 1.3%; the script test cannot fire at all. The CLI reported QA clean
    # and exited 0, and a customer would have shipped English to German users.
    #
    # For a same-script pair the signal is identity: the "translation" is the source, byte for
    # byte. On its own that proves nothing — OK, Status, Email, Menu genuinely survive
    # translation — so it only counts for something sentence-shaped, which the word count and
    # the lowercase-word rule below decide.
    same_as_source = translated.strip() == original.strip()
    if not same_as_source and (not rx or rx.search(translated)):
        return []

    s = _PH_RE.sub(" ", translated)
    s = _TAG_RE.sub(" ", s)
    s = _URL_RE.sub(" ", s)
    s = re.sub(r"\d+", " ", s)
    for g in (glossary or []):
        if g:
            s = re.sub(re.escape(g), " ", s, flags=re.I)
    if not _LOWERCASE_WORD_RE.search(s):
        # A name, not a sentence — "GitHub Actions Runner" legitimately stays Latin. But the
        # rule needs a lowercase word of 4+ letters, and a short question has none: "How do I
        # get my API key?" survived into Russian byte-for-byte, with the model reporting 98%
        # confidence, because how/do/get/my/key are all shorter than four letters.
        #
        # For a target with its OWN script the script test alone is already decisive — no
        # Cyrillic anywhere means nothing was translated. What has to stay excluded is the
        # brand phrase, and those are Title Case ("GitHub Actions"), never mixed case with
        # function words. So: several words, at least one of them lowercase, and a target
        # script that is entirely absent.
        words = re.findall(r"[^\W\d_]+", s, flags=re.UNICODE)
        looks_like_a_sentence = (
            len(words) >= 3 and any(w[:1].islower() for w in words)
        )
        if not (rx and not rx.search(translated) and looks_like_a_sentence):
            return []

    if rx and not rx.search(translated):
        return ["may be untranslated — no target-language script, still reads as source text"]
    # Same-script target: demand a real sentence before saying anything, so a one-word label
    # that legitimately survives translation never trips it.
    if same_as_source and len(re.findall(r"[^\W\d_]{2,}", s, flags=re.UNICODE)) >= 3:
        return ["may be untranslated — identical to the source text"]
    return []

def _compute_consistency(source_flat: dict[str, str], translated_flat: dict[str, str]) -> list[dict]:
    """Flag source terms that were translated DIFFERENTLY across keys that share the exact
    same source text — usually a sign the same UI term (e.g. "Cancel") drifted into two
    different translations ("Отмена" vs "Отменить") depending on context. Pure post-
    processing over already-translated results — no extra LLM call, just string comparison.
    Normalizes case/whitespace before comparing so trivial formatting differences (a trailing
    space, a capital letter) don't create false positives. Limited to short, reusable
    label-like strings (≤60 chars) — long sentences legitimately vary with context."""
    # norm_source -> norm_translation -> [keys]
    by_term: dict[str, dict[str, list[str]]] = {}
    # norm_source -> norm_translation -> original-cased example (for display)
    display: dict[str, dict[str, str]] = {}
    src_display: dict[str, str] = {}
    for k, src in source_flat.items():
        if not isinstance(src, str):
            continue
        s = src.strip()
        if not s or len(s) > 60:
            continue
        tr = translated_flat.get(k)
        if not isinstance(tr, str) or not tr.strip():
            continue
        norm_s = " ".join(s.lower().split())
        norm_t = " ".join(tr.strip().lower().split())
        by_term.setdefault(norm_s, {}).setdefault(norm_t, []).append(k)
        display.setdefault(norm_s, {}).setdefault(norm_t, tr.strip())
        src_display.setdefault(norm_s, s)

    out = []
    for norm_s, variants in by_term.items():
        if len(variants) < 2:
            continue  # translated consistently everywhere it appears
        keys: list[str] = []
        variant_list: list[str] = []
        for norm_t, ks in variants.items():
            keys.extend(ks)
            variant_list.append(display[norm_s][norm_t])
        out.append({
            "term": src_display[norm_s],
            "variants": variant_list,
            "keys": sorted(keys),
        })
    out.sort(key=lambda x: len(x["keys"]), reverse=True)
    return out[:30]

# Currency tokens, longest first so "R$" is never read as "$". The value is the currency they
# mean, so a symbol and its ISO code compare equal: "$79" and "79 USD" are the same price.
_CURRENCY_TOKENS = [
    ("R$", "BRL"), ("US$", "USD"), ("CA$", "CAD"), ("NZ$", "NZD"), ("HK$", "HKD"),
    ("A$", "AUD"), ("S$", "SGD"), ("zł", "PLN"), ("Kč", "CZK"), ("kr", "KR"),
    ("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("¥", "CJY"), ("₽", "RUB"), ("₴", "UAH"),
    ("₹", "INR"), ("₩", "KRW"), ("₺", "TRY"), ("₫", "VND"), ("₪", "ILS"),
    ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("RUB", "RUB"), ("UAH", "UAH"),
    ("BRL", "BRL"), ("JPY", "CJY"), ("CNY", "CJY"), ("PLN", "PLN"), ("CZK", "CZK"),
    ("SEK", "KR"), ("NOK", "KR"), ("DKK", "KR"), ("CHF", "CHF"), ("INR", "INR"),
    ("KRW", "KRW"), ("TRY", "TRY"), ("CAD", "CAD"), ("AUD", "AUD"),
]
# A number, then optional space/nbsp, then the token — or the token then the number. Only
# money is interesting here: "50 kr" is a price, "Kredit" and "krona" are words, and a rule
# that fired on those would be muted within a day.
_CURRENCY_NEAR_NUMBER = [
    (re.compile(r"(?:(?<=\d)|(?<=\d[  \u00a0\u202f]))" + re.escape(tok) + r"(?![A-Za-z])"), cur)
    for tok, cur in _CURRENCY_TOKENS
] + [
    (re.compile(r"(?<![A-Za-z0-9])" + re.escape(tok) + r"[  \u00a0\u202f]?(?=\d)"), cur)
    for tok, cur in _CURRENCY_TOKENS
]


def _currencies_in(text: str) -> set:
    """Which currencies this string quotes a price in. Longest token wins, so R$ never
    registers as $."""
    found, seen = set(), []
    for rx, cur in _CURRENCY_NEAR_NUMBER:
        for m in rx.finditer(text or ""):
            span = (m.start(), m.end())
            # a longer token already claimed this position (R$ before $)
            if any(a <= span[0] < b for a, b in seen):
                continue
            seen.append(span)
            found.add(cur)
    return found


def _currency_faults(original: str, translated: str) -> list[str]:
    """The translation quotes a different currency than the source did.

    Not a wording choice — a price. Our homepage line "Lifetime, $79 once" came back from
    Portuguese as "R$79", dollars turned into reais: about thirteen dollars instead of
    seventy-nine. The numeric-drift detector was satisfied, because 79 never moved; what
    moved was the symbol in front of it.

    Deliberately blind to POSITION: "79 $" is correct French typography for "$79", and a
    detector that flagged it would be wrong more often than right. Symbol and ISO code
    compare equal for the same reason.
    """
    src = _currencies_in(original)
    tgt = _currencies_in(translated)
    if not src and not tgt:
        return []
    if src == tgt:
        return []
    def _show(cs):
        return ", ".join(sorted(cs)) if cs else "none"
    if src and not tgt:
        return [f"currency dropped — the source priced this in {_show(src)}, the translation has no currency"]
    if tgt and not src:
        return [f"currency invented — the translation quotes {_show(tgt)}, the source quoted none"]
    return [f"currency changed: {_show(src)} → {_show(tgt)} — this is a different price, not a translation"]


def _lost_glossary(original: str, translated: str, glossary: list[str] | None) -> list[str]:
    """Glossary/brand terms present in the source that did NOT survive (case-insensitive)
    in the translation. A deterministic FACT — the model altered or dropped a term the user
    locked to stay verbatim across languages. Biased to UNDER-flag (only reports a term whose
    text is genuinely absent from the output), so it never fabricates a violation."""
    if not glossary:
        return []
    src = original.lower()
    tr = translated.lower()
    out = []
    for term in glossary:
        t = (term or "").strip()
        if t and t.lower() in src and t.lower() not in tr:
            out.append(t)
    return sorted(set(out))


ERROR = "error"
WARN = "warn"


def _glossary_collapse(original: str, translated: str, glossary: list[str] | None) -> list[str]:
    """A sentence whose translation is nothing but the glossary term(s).

    Asking the model to keep a term verbatim can make it answer with the term and nothing
    else. Seen in production: three UI questions with glossary=KAERIS came back from German
    as {"q1": "KAERIS", "q2": "KAERIS", "q3": "KAERIS"} — every string replaced by the word
    it was told to preserve, and the whole batch went, not only the string that contained
    the term. Reproduced; it never happens without a glossary.

    Every existing guard is blind to it, which is why this one exists:
      - _lost_glossary sees the term present and is satisfied;
      - _untranslated_string strips glossary terms before looking for a real word, so
        "KAERIS" strips to "" and passes as a name rather than a sentence — the exemption
        that protects "GitHub Actions" is exactly what hides this.

    Deliberately narrow, so it never fires on a legitimate brand-only label: the SOURCE must
    be sentence-shaped (3+ words) and the TRANSLATION must retain nothing but glossary terms
    and punctuation.
    """
    if not glossary or not isinstance(original, str) or not isinstance(translated, str):
        return []
    terms = [t.strip() for t in glossary if t and t.strip()]
    if not terms:
        return []

    src_words = re.findall(r"[^\W\d_]{2,}", original, flags=re.UNICODE)
    if len(src_words) < 3:
        return []                       # "KAERIS Pro" losing a word is not this defect

    rest = translated
    for t in terms:
        rest = re.sub(re.escape(t), " ", rest, flags=re.I)
    rest = _PH_RE.sub(" ", rest)
    rest = _TAG_RE.sub(" ", rest)
    if re.search(r"[^\W\d_]", rest, flags=re.UNICODE):
        return []                       # something other than the term survived — a real translation

    # Nothing but the term(s) left, and the source was a sentence: the content is gone.
    kept = [t for t in terms if t.lower() in translated.lower()]
    if not kept:
        return []
    return [f'translation collapsed to the glossary term "{kept[0]}" — the sentence is gone']


def _answered_instead_of_translating(original: str, translated: str) -> bool:
    """The model treated the string as a question to answer rather than text to translate.

    Live on 01.08: "How do I get my API key?" (24 chars) came back from French as 250
    characters of English advice — "You can get your API key by signing up for an account on
    the service provider's website. After registration…". Nothing about it is a translation.

    The overflow check does see the size (+1017%), but it reports a LAYOUT risk, which sends
    the customer to inspect their UI for a string whose content is simply gone. Worth its own
    verdict, and worth repairing rather than warning about.

    Short sources only, and a multiple no honest expansion reaches: German, the longest of the
    languages we serve, averages +35% and tops out well under double.
    """
    if not isinstance(original, str) or not isinstance(translated, str):
        return False
    src, tr = original.strip(), translated.strip()
    if not src or not tr:
        return False
    if len(src) > 80:                   # a paragraph legitimately varies in length
        return False
    return len(tr) >= max(4 * len(src), 120)


def string_faults(src, tgt, lang, glossary=None):
    """All per-string faults between one source value and one target value,
    each tagged error (breaks build) or warn (advisory). Pure/offline."""
    if not isinstance(src, str) or not isinstance(tgt, str):
        return []
    out = []
    for ph in _lost_placeholders(src, tgt):
        out.append({"severity": ERROR, "msg": f"lost placeholder {ph}"})
    for msg in _placeholder_type_faults(src, tgt):
        out.append({"severity": ERROR, "msg": msg})
    for msg in _numeric_faults(src, tgt, lang):
        out.append({"severity": ERROR, "msg": msg})
    for msg in _entity_faults(src, tgt):
        out.append({"severity": ERROR, "msg": msg})
    for msg in _currency_faults(src, tgt):
        out.append({"severity": ERROR, "msg": msg})
    for msg in _icu_faults(src, tgt, lang):
        out.append({"severity": ERROR, "msg": msg})
    for tag in _lost_tags(src, tgt):
        out.append({"severity": ERROR, "msg": f"lost inline tag {tag}"})
    if glossary:
        for term in _lost_glossary(src, tgt, glossary):
            out.append({"severity": ERROR, "msg": f"glossary term dropped: {term}"})
        for msg in _glossary_collapse(src, tgt, glossary):
            out.append({"severity": ERROR, "msg": msg})
    if _answered_instead_of_translating(src, tgt):
        out.append({"severity": ERROR, "msg": "not a translation — the model answered the string instead of translating it"})
    for msg in _untranslated_string(src, tgt, lang, glossary):
        out.append({"severity": WARN, "msg": msg})
    return out


def file_faults(source_flat, translated_flat, lang):
    """File-level faults over one target locale (register, consistency, overflow).
    All advisory (warn). Pure/offline."""
    out = []
    for msg in _register_faults(translated_flat, lang):
        out.append({"severity": WARN, "msg": msg})
    for item in _compute_consistency(source_flat, translated_flat):
        out.append({"severity": WARN,
                    "msg": f"inconsistent translation of \"{item['term']}\" across "
                           f"{len(item['keys'])} keys: {', '.join(item['variants'])}"})
    for item in _compute_overflow(source_flat, translated_flat, lang):
        out.append({"severity": WARN,
                    "msg": f"{item['key']}: translation +{item['pct']}% vs source "
                           f"(typical +{item['typical']}%) — may overflow UI"})
    return out
