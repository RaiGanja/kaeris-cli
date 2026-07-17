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
