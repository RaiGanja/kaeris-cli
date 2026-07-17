"""Offline, deterministic i18n quality detectors — a byte-faithful copy of the pure
detector layer in backend/translator.py. NO network, NO model, NO file IO here.
Parity with the backend is proven by cli/tests/test_detector_parity.py.
Stdlib only: re, collections."""

import re
import collections

# Placeholders, longest/most-specific forms FIRST so e.g. {{name}} isn't split into {name}
# and %{name} isn't split into % + {name}:
#   {{ name }}  Vue/Angular/Handlebars/Mustache      %{name}   Rails i18n
#   %(name)s  Python/Django dict printf     {name} {0} {0:C}  brace + .NET composite
#   %1$s / %2$@  positional (Android/iOS)   %@  iOS   %s/%d printf
#   ${name}  JS template     :name:  Rails-ish
_PH_RE = re.compile(
    r"\{\{\s*[\w\d_.]+\s*\}\}"
    r"|%\{[\w\d_]+\}"
    r"|%\([\w\d_]+\)[-+0#]*+\d*+(?:\.\d+)?[a-zA-Z]"   # Python dict, incl. %(name).2f width/precision; possessive *+ blocks ReDoS
    r"|\{[\w\d_]+(?:,-?\d+)?(?::[^}]*)?\}"   # brace + .NET composite incl. alignment {0,-10:N2} {1,5}
    r"|%\d+\$[-+0# ]*+\d*+(?:\.\d+)?[@a-zA-Z]"   # positional printf incl. flags/width/precision %1$02d %2$-5s; possessive *+ blocks ReDoS
    r"|%@"
    r"|%[-+0#]*+\d*+(?:\.\d+)?(?:hh|ll|[hlLzjt])?[sSdiouxXeEfFgGaAcCpn%]"  # printf incl. %.2f %02d %-10s %u %ld; possessive *+ blocks ReDoS
    r"|\$\{[\w\d_]+\}"
    r"|:[A-Za-z]\w*:"   # Rails-ish :name: — must START with a letter so time (10:30:45) and ratios (3:4:5) aren't mis-read as placeholders
)

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
    src = collections.Counter(_PH_RE.findall(original))
    tr = collections.Counter(_PH_RE.findall(translated))
    faults: list[str] = []
    for ph in sorted(set(src) | set(tr)):
        if ph == "%%":                       # literal percent, not an argument
            continue
        s, t = src[ph], tr[ph]
        if t > s:
            faults.append(f"invented placeholder {ph} (not in source)" if s == 0
                          else f"placeholder {ph} appears {t}× vs {s}× in source (duplicated)")
        elif s > t and s > 1:                # single drops are _lost_placeholders' job
            faults.append(f"placeholder {ph} appears {s}× in source but {t}× in translation")
    return faults

# Digit runs incl. non-ASCII digits (Arabic-Indic, Persian, Devanagari) and locale
# grouping/decimal separators, so "1,000" / "1.000" / "1 000" and "١٢٣" are all seen.
_NUM_RUN_RE = re.compile(
    r"[0-9٠-٩۰-۹०-९]"
    r"[0-9٠-٩۰-۹०-९.,'   ]*")
_DIGIT_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹०१२३४५६७८९", "0123456789" * 3)

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

def _numeric_faults(original: str, translated: str) -> list[str]:
    """Deterministic number-drift check. Catches the dangerous silent case a meaning-judge can
    miss because the sentence still reads fine: 'Delete 5 files' -> 'Delete 50 files', a wrong
    price, a mangled version. No mainstream TMS diffs the numeric CONTENT of a translation.
    Guarded against two false positives: locale grouping (1,000 vs 1.000 normalize equal) and
    a fully spelled-out translation ('5' -> 'fünf' with no digits left) — the latter is skipped
    because a translation with NO digits is a legitimate spell-out, not a dropped value."""
    src, src_disp = _extract_numbers(original)
    tr, tr_disp = _extract_numbers(translated)
    if src == tr:
        return []
    faults: list[str] = []
    for num in sorted(tr - src):
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
        if typ in ("plural", "selectordinal"):
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
