"""`--digits latin`: the result's digits in 0-9, whatever script the language writes them in.

The model writes Persian ۲۰۲۷, Bengali ২০২৭, Hindi २०२७ — correct for those scripts, wrong for
a site that shows years, prices and error codes in 0-9 everywhere (client report, 0.2.31).
Every Unicode decimal digit outside ASCII (category Nd) becomes its ASCII value. Placeholders
are the developer's text, not the model's, so they are copied through untouched.
"""
import re
import unicodedata

MODES = ("native", "latin")

# What a placeholder looks like across the formats we write: {name} / {0} / {{name}},
# %s / %1$s / %(name)s / %@, ${name}, $t(key), and markup tags. [0-9], not \d: \d matches the
# very digits this module rewrites.
_PROTECTED = re.compile(
    r"\{\{[^{}]*\}\}"
    r"|\{[^{}\s,]*\}"
    r"|%(?:[0-9]+\$)?[-+ #0]*[0-9]*(?:\.[0-9]+)?(?:\([^)]*\))?[sdifuxXeEgGcp@]"
    r"|\$\{[^}]*\}"
    r"|\$t\([^)]*\)"
    r"|<[^<>]+>"
)


def _latin(chunk):
    out = []
    for ch in chunk:
        if ch > "\x7f" and unicodedata.category(ch) == "Nd":
            out.append(str(unicodedata.decimal(ch)))
        else:
            out.append(ch)
    return "".join(out)


def _has_native(text):
    return any(ch > "\x7f" and unicodedata.category(ch) == "Nd" for ch in text)


def to_latin(text):
    """`text` with every non-ASCII decimal digit outside a placeholder written as 0-9."""
    if not _has_native(text):
        return text
    parts, last = [], 0
    for m in _PROTECTED.finditer(text):
        parts.append(_latin(text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_latin(text[last:]))
    return "".join(parts)


def in_values(obj):
    """to_latin over every string in a parsed JSON value (keys are left alone)."""
    if isinstance(obj, str):
        return to_latin(obj)
    if isinstance(obj, dict):
        return {k: in_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [in_values(v) for v in obj]
    return obj


def in_file(data):
    """to_latin over a whole result file as the server sent it (bytes, UTF-8). Anything that is
    not UTF-8 text is returned unchanged."""
    try:
        text = data.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return data
    fixed = to_latin(text)
    return data if fixed is text else fixed.encode("utf-8")
