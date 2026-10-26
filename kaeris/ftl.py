"""Mozilla Fluent (.ftl) read without Mozilla's parser.

The server cuts a .ftl into strings with `fluent.syntax`; this package has no dependencies, so
`kaeris check` reads the part of Fluent that translation files use and builds the SAME keys:
`greeting`, `login.label` (attribute), `-brand` (term), and for a selector one key per variant
(`emails$0.one`, `emails$0.other`; text beside a selector is `emails~0`). Each pattern is one
string with its placeables kept as `{ $name }`.

A plural selector (every variant key a CLDR category) is folded into one ICU string,
`{count, plural, one {...} other {...}}`, like Android plurals are, so the plural-form checks
see it: Russian with only `one` and `other` is incomplete, Japanese with only `other` is not.

tests/test_check_every_format.py compares the keys with fluent.syntax when KAERIS_BACKEND is set.
What this reader does not do: validate Fluent. A construct it cannot place makes the entry
unreadable and that is said, never skipped silently.
"""
import re

ORDER = ("zero", "one", "two", "few", "many", "other")
COUNTING = {"zero", "one", "two", "few", "many"}
FOLD_MARK = "⟦plural⟧"

_ENTRY_RE = re.compile(r"^(-?[A-Za-z][A-Za-z0-9_-]*)[ \t]*=(.*)$")
_ATTR_RE = re.compile(r"^[ \t]+\.([A-Za-z][A-Za-z0-9_-]*)[ \t]*=(.*)$")
_VARIANT_RE = re.compile(r"^[ \t]*(\*?)\[[ \t]*([^\]\s][^\]]*?)[ \t]*\][ \t]*(.*)$")
_SIMPLE_VAR_RE = re.compile(r"\{ (\$[A-Za-z][\w-]*) \}")


class FtlError(ValueError):
    pass


def _scan(text):
    """Depth of `{` nesting at the start of every line, string literals ("...") ignored."""
    depths, depth, in_str, k = [0], 0, False, 0
    while k < len(text):
        c = text[k]
        if in_str:
            if c == "\\":
                k += 1
            elif c == '"':
                in_str = False
        elif c == '"' and depth:
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth = max(depth - 1, 0)
        elif c == "\n":
            depths.append(depth)
        k += 1
    return depths


def _dedent(lines, depths):
    """Strip the indentation shared by every non-blank line that starts outside a placeable."""
    common = None
    for line, depth in zip(lines, depths):
        if depth == 0 and line.strip():
            indent = len(line) - len(line.lstrip(" \t"))
            common = indent if common is None else min(common, indent)
    common = common or 0
    return [ln[common:] if d == 0 and ln.strip() else ln if d else "" for ln, d in zip(lines, depths)]


def _pattern_text(first, rest):
    """One pattern's text from the line after `=` and the indented lines that follow it."""
    raw = "\n".join([first] + rest)
    depths = _scan(raw)
    lines = raw.split("\n")
    head = lines[0].lstrip(" \t")
    body = _dedent(lines[1:], depths[1:])
    out = ([head] if head.strip() else []) + body
    while out and not out[0].strip() and not head.strip():
        out.pop(0)
    return "\n".join(out).rstrip(" \t\n")


def _split(text):
    """[("text", s) | ("ph", inner)] — top-level `{ ... }` placeables and the text between."""
    out, buf, k = [], [], 0
    while k < len(text):
        c = text[k]
        if c != "{":
            buf.append(c)
            k += 1
            continue
        depth, j, in_str = 1, k + 1, False
        while j < len(text) and depth:
            d = text[j]
            if in_str:
                if d == "\\":
                    j += 1
                elif d == '"':
                    in_str = False
            elif d == '"':
                in_str = True
            elif d == "{":
                depth += 1
            elif d == "}":
                depth -= 1
            j += 1
        if depth:
            raise FtlError("a { that never closes")
        if buf:
            out.append(("text", "".join(buf)))
            buf = []
        out.append(("ph", text[k + 1:j - 1]))
        k = j
    if buf:
        out.append(("text", "".join(buf)))
    return out


def _top_level_arrow(inner):
    depth, in_str, k = 0, False, 0
    while k < len(inner) - 1:
        c = inner[k]
        if in_str:
            if c == "\\":
                k += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and c == "-" and inner[k + 1] == ">":
            return k
        k += 1
    return -1


def _norm_expr(expr):
    """How the placeable is written once Fluent's own serializer has had it."""
    out, in_str, k = [], False, 0
    expr = expr.strip()
    while k < len(expr):
        c = expr[k]
        if in_str:
            out.append(c)
            if c == "\\" and k + 1 < len(expr):
                out.append(expr[k + 1])
                k += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c.isspace():
            if out and out[-1] != " " and out[-1] not in "(":
                out.append(" ")
        elif c == ",":
            while out and out[-1] == " ":
                out.pop()
            out.append(", ")
            while k + 1 < len(expr) and expr[k + 1].isspace():
                k += 1
        elif c == ":":
            while out and out[-1] == " ":
                out.pop()
            out.append(": ")
            while k + 1 < len(expr) and expr[k + 1].isspace():
                k += 1
        elif c == ")":
            while out and out[-1] == " ":
                out.pop()
            out.append(c)
        else:
            out.append(c)
        k += 1
    return "".join(out).strip()


def _variants(body):
    """[(key, is_default, pattern text)] from what follows `->` inside a placeable."""
    lines = body.split("\n")
    marks = [(n, _VARIANT_RE.match(ln)) for n, ln in enumerate(lines)]
    marks = [(n, m) for n, m in marks if m]
    if not marks:
        raise FtlError("a selector with no [variant]")
    out = []
    for idx, (n, m) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        cont = lines[n + 1:end]
        text = _pattern_text(m.group(3), cont)
        out.append((m.group(2), bool(m.group(1)), text))
    return out


def _elements(text):
    """The pattern as Fluent's parser lists it: text runs and placeables, in order."""
    els = []
    for kind, value in _split(text):
        if kind == "text":
            els.append(("text", value))
        elif _top_level_arrow(value) >= 0:
            arrow = _top_level_arrow(value)
            els.append(("select", [(k, d, _elements(t)) for k, d, t in _variants(value[arrow + 2:])]))
        else:
            els.append(("ph", _norm_expr(value)))
    return els


def _inline(els):
    return "".join(v if k == "text" else "{ " + v + " }" for k, v in els)


def _walk(els, path, out):
    if not any(k == "select" for k, _ in els):
        text = _inline(els)
        if text.strip():
            out[path] = text
        return
    for i, (kind, value) in enumerate(els):
        if kind == "select":
            for key, _default, sub in value:
                _walk(sub, f"{path}${i}.{key}", out)
        elif kind == "text" and value.strip():
            out[f"{path}~{i}"] = value


def _plural_prefixes(entries):
    """Paths ('emails$0') of selectors whose variants are plain text and whose keys are the
    CLDR plural categories."""
    found = set()

    def walk(els, path):
        for i, (kind, value) in enumerate(els):
            if kind != "select":
                continue
            keys = {k for k, _d, _s in value}
            named = {k for k in keys if re.fullmatch(r"[A-Za-z][\w-]*", k)}
            simple = not any(sk == "select" for _k, _d, sub in value for sk, _v in sub)
            if keys == named and named <= set(ORDER) and named & COUNTING and simple:
                found.add(f"{path}${i}")
            for k, _d, sub in value:
                walk(sub, f"{path}${i}.{k}")

    for path, els in entries:
        walk(els, path)
    return found


def _balanced(text):
    depth = 0
    for ch in text:
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            return False
    return depth == 0


def _fold(flat, prefixes):
    members = {}
    for key in flat:
        head, _, cat = key.rpartition(".")
        if head in prefixes and cat in ORDER:
            members.setdefault(head, {})[cat] = key
    groups = {}
    for head, cats in members.items():
        texts = {c: _SIMPLE_VAR_RE.sub(lambda m: "{" + m.group(1)[1:] + "}", flat[k])
                 for c, k in cats.items()}
        if "other" not in cats or not all(_balanced(t) for t in texts.values()):
            continue
        arms = " ".join(f"{c} {{{texts[c]}}}" for c in ORDER if c in texts)
        groups[head + FOLD_MARK] = (set(cats.values()), f"{{count, plural, {arms}}}",
                                    next(iter(cats.values())))
    if not groups:
        return flat
    first = {first_key: name for name, (_m, _t, first_key) in groups.items()}
    gone = set().union(*(m for m, _t, _f in groups.values()))
    out = {}
    for key, value in flat.items():
        if key in first:
            out[first[key]] = groups[first[key]][1]
        elif key not in gone:
            out[key] = value
    return out


def parse(text, fold=True):
    """({key: text}, [problems]). `fold=False` is the server's own per-variant keys."""
    flat, entries, problems = {}, [], []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _ENTRY_RE.match(lines[i])
        if not m:
            if lines[i].strip() and not lines[i].startswith("#") and not lines[i][:1].isspace():
                problems.append(f"line {i + 1}: not a Fluent message ({lines[i][:40]!r})")
            i += 1
            continue
        name, first = m.group(1), m.group(2)
        start = i
        i += 1
        rest = []
        while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
            rest.append(lines[i])
            i += 1
        while rest and not rest[-1].strip():
            rest.pop()
        depths = _scan("\n".join([first] + rest))
        blocks, cur = [("", first, [])], None
        for line, depth in zip(rest, depths[1:]):
            a = _ATTR_RE.match(line) if depth == 0 else None
            if a:
                blocks.append((a.group(1), a.group(2), []))
            else:
                blocks[-1][2].append(line)
        for attr, head, body in blocks:
            path = f"{name}.{attr}" if attr else name
            try:
                els = _elements(_pattern_text(head, body))
            except FtlError as e:
                problems.append(f"line {start + 1}: {name}: {e}")
                continue
            if els:
                entries.append((path, els))
                _walk(els, path, flat)
    if fold:
        flat = _fold(flat, _plural_prefixes(entries))
    return flat, problems
