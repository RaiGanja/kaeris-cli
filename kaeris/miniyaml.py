"""A reader for the YAML that locale files are written in, with no dependencies.

`kaeris translate` reads YAML with PyYAML on the server; this package ships with none, so
`kaeris check` reads the part of YAML that i18n files use: nested mappings, block and inline
sequences, plain / 'single' / "double" quoted scalars (also across lines), `|` and `>` block
scalars, one-line or multi-line `[a, b]` / `{a: b}`, comments. Scalars are typed the way
PyYAML types them (YAML 1.1: `yes` is a boolean, `~` is null), mapping keys stay text.

What it does not read it refuses by name instead of guessing: anchors and aliases (`&a`, `*a`),
merge keys (`<<`), tags (`!!str`), several documents in one file, complex keys (`? `), tabs as
indentation. A guess would be a wrong "all clear" in someone's CI.
"""
import datetime
import re


class YamlUnsupported(ValueError):
    """Valid-looking YAML that this reader will not interpret. The message names the construct
    and the line."""


class YamlError(ValueError):
    """The file is not YAML we can make sense of."""


_BLOCK_RE = re.compile(r"^([|>])([-+]?)(\d?)([-+]?)\s*(?:#.*)?$")
_INT_RE = re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$")
_HEX_RE = re.compile(r"^[-+]?0x[0-9a-fA-F_]+$")
_OCT_RE = re.compile(r"^[-+]?0[0-7_]+$")
_BIN_RE = re.compile(r"^[-+]?0b[01_]+$")
_FLOAT_RE = re.compile(
    r"^[-+]?(?:[0-9][0-9_]*)?\.[0-9_]+(?:[eE][-+]?[0-9]+)?$"
    r"|^[-+]?[0-9][0-9_]*(?:\.[0-9_]*)?[eE][-+]?[0-9]+$"
    r"|^[-+]?[0-9][0-9_]*\.$")
_INF_RE = re.compile(r"^[-+]?\.(?:inf|Inf|INF)$|^\.(?:nan|NaN|NAN)$")
_DATE_RE = re.compile(r"^\d{4}-\d\d-\d\d$")
_SEXAGESIMAL_RE = re.compile(r"^[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+$")
_NULLS = {"~", "null", "Null", "NULL", ""}
_TRUE = {"yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON"}
_FALSE = {"no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF"}
_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
            "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
            "N": "\x85", "_": "\xa0", "L": " ", "P": " "}


def resolve(token):
    """The Python value of a plain scalar, typed like PyYAML's default resolver."""
    if token in _NULLS:
        return None
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    try:
        if _INT_RE.match(token):
            return int(token.replace("_", ""))
        if _OCT_RE.match(token):
            return int(token.replace("_", ""), 8)
        if _HEX_RE.match(token):
            return int(token.replace("_", ""), 16)
        if _BIN_RE.match(token):
            return int(token.replace("_", ""), 2)
        if _SEXAGESIMAL_RE.match(token):
            total = 0
            for part in token.replace("_", "").lstrip("+-").split(":"):
                total = total * 60 + int(part)
            return -total if token.startswith("-") else total
        if _FLOAT_RE.match(token):
            return float(token.replace("_", ""))
        if _INF_RE.match(token):
            return float("nan") if "nan" in token.lower() else float(token.lower().replace(".inf", "inf"))
        if _DATE_RE.match(token):
            return datetime.date.fromisoformat(token)
    except ValueError:
        pass
    return token


class _Reader:
    def __init__(self, text):
        raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines = []                       # [indent or None for blank, content, lineno, raw line]
        started = False
        for n, line in enumerate(raw, 1):
            if line.startswith("%"):
                raise YamlUnsupported(f"line {n}: YAML directives (%...) are not read")
            if line.rstrip() == "---" or line.startswith("--- "):
                if started:
                    raise YamlUnsupported(f"line {n}: several YAML documents in one file are not read")
                started = True
                rest = line[3:].strip()
                if not rest or rest.startswith("#"):
                    continue
                line = rest
            elif line.rstrip() == "...":
                break
            started = started or bool(line.strip() and not line.lstrip().startswith("#"))
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                self.lines.append([None, "", n, line])
                continue
            indent = len(line) - len(line.lstrip(" "))
            if line[indent:indent + 1] == "\t":
                raise YamlError(f"line {n}: a tab used as indentation")
            self.lines.append([indent, line[indent:].rstrip(), n, line])

    def skip_blank(self, i):
        while i < len(self.lines) and self.lines[i][0] is None:
            i += 1
        return i

    def block(self, i, depth=0):
        if depth > 200:
            raise YamlError("nested too deeply")
        i = self.skip_blank(i)
        if i >= len(self.lines):
            return None, i
        indent, content, _n, _raw = self.lines[i]
        if content == "-" or content.startswith("- "):
            return self.sequence(i, indent, depth)
        return self.mapping(i, indent, depth)

    def sequence(self, i, indent, depth):
        out = []
        while True:
            i = self.skip_blank(i)
            if i >= len(self.lines):
                break
            ind, content, n, _raw = self.lines[i]
            if ind != indent or not (content == "-" or content.startswith("- ")):
                if ind > indent:
                    raise YamlError(f"line {n}: unexpected indentation")
                break
            rest = content[1:]
            gap = len(rest) - len(rest.lstrip(" "))
            rest = rest.lstrip(" ")
            if not rest or rest.startswith("#"):
                j = self.skip_blank(i + 1)
                if j < len(self.lines) and self.lines[j][0] > indent:
                    value, i = self.block(j, depth + 1)
                else:
                    value, i = None, i + 1
                out.append(value)
                continue
            self.lines[i] = [indent + 1 + gap, rest, n, self.lines[i][3]]
            if self._split_key(rest, n) is not None or rest == "-" or rest.startswith("- "):
                value, i = self.block(i, depth + 1)
            else:
                value, i = self.scalar_value(i, rest, indent)
            out.append(value)
        return out, i

    def _split_key(self, content, n):
        """(key, rest) when `content` is `key: rest` or `key:`, else None."""
        if content.startswith("? "):
            raise YamlUnsupported(f"line {n}: complex mapping keys (? ...) are not read")
        if content[:1] in "\"'":
            end = self._end(content, content[0])
            if end == -1:
                return None
            m = re.match(r"^\s*:(?:\s+(.*))?$", content[end:])
            if not m:
                return None
            return self.quoted(content[:end], n), (m.group(1) or "")
        if content[:1] in "[{":
            return None
        m = re.match(r"^(.*?[^\s])\s*:(?:\s+(.*))?$", content)
        if not m or re.search(r"\s#", m.group(1)):
            return None
        return m.group(1), (m.group(2) or "")

    def mapping(self, i, indent, depth):
        out = {}
        while True:
            i = self.skip_blank(i)
            if i >= len(self.lines):
                break
            ind, content, n, _raw = self.lines[i]
            if ind < indent:
                break
            if ind > indent:
                raise YamlError(f"line {n}: unexpected indentation")
            if content == "-" or content.startswith("- "):
                break
            pair = self._split_key(content, n)
            if pair is None:
                raise YamlError(f"line {n}: expected 'key: value', found {content[:40]!r}")
            key, rest = pair
            if key == "<<":
                raise YamlUnsupported(f"line {n}: YAML merge keys (<<) are not read")
            if content[:1] in "&*!":
                raise YamlUnsupported(f"line {n}: anchors, aliases and tags on a key are not read")
            rest = rest.strip()
            if not rest or rest.startswith("#"):
                j = self.skip_blank(i + 1)
                if j < len(self.lines) and self.lines[j][0] > indent:
                    value, i = self.block(j, depth + 1)
                elif j < len(self.lines) and self.lines[j][0] == indent and (
                        self.lines[j][1] == "-" or self.lines[j][1].startswith("- ")):
                    value, i = self.sequence(j, indent, depth + 1)
                else:
                    value, i = None, i + 1
            else:
                value, i = self.scalar_value(i, rest, indent)
            out[key] = value
        return out, i

    def scalar_value(self, i, rest, indent):
        """The value that starts on line `i` after `key:` or `- `; returns (value, next line)."""
        n = self.lines[i][2]
        if rest[0] in "&*":
            raise YamlUnsupported(f"line {n}: YAML anchors and aliases (&x, *x) are not read")
        if rest[0] == "!":
            raise YamlUnsupported(f"line {n}: YAML tags (!!type) are not read")
        if _BLOCK_RE.match(rest):
            return self.block_scalar(i, rest, indent)
        if rest[0] in "[{":
            return self.flow(i, rest)
        if rest[0] in "\"'":
            return self.quoted_value(i, rest)
        text = re.split(r"\s#", rest, maxsplit=1)[0].rstrip()
        i += 1
        while True:                             # a plain scalar continued on more-indented lines
            j = i
            while j < len(self.lines) and self.lines[j][0] is None:
                j += 1
            if j < len(self.lines) and self.lines[j][0] > indent \
                    and self._split_key(self.lines[j][1], self.lines[j][2]) is None:
                text += " " + re.split(r"\s#", self.lines[j][1], maxsplit=1)[0].strip()
                i = j + 1
                continue
            break
        return resolve(text), i

    def block_scalar(self, i, header, indent):
        m = _BLOCK_RE.match(header)
        style, chomp = m.group(1), (m.group(2) or m.group(4))
        explicit = int(m.group(3)) if m.group(3) else 0
        i += 1
        body = []
        while i < len(self.lines):
            ind, _content, _n, raw_line = self.lines[i]
            if ind is not None and ind <= indent:
                break
            body.append(raw_line)
            i += 1
        blanks = 0
        while body and not body[-1].strip():
            body.pop()
            blanks += 1
        lead = (indent + explicit) if explicit else min(
            (len(l) - len(l.lstrip(" ")) for l in body if l.strip()), default=0)
        rows = [l[lead:] if l.strip() else "" for l in body]
        if style == "|":
            text = "\n".join(rows)
        else:
            text, prev = "", None
            for row in rows:
                if prev is None:
                    text = row
                elif row == "":
                    text += "\n"
                elif prev == "":
                    text += row
                elif row.startswith(" ") or prev.startswith(" "):
                    text += "\n" + row
                else:
                    text += " " + row
                prev = row
        if chomp == "+":
            text += "\n" * (blanks + 1)
        elif chomp != "-" and rows:
            text += "\n"
        return text, i

    def quoted(self, token, n):
        q = token[0]
        inner = token[1:-1]
        if q == "'":
            return inner.replace("''", "'")
        out, k = [], 0
        while k < len(inner):
            c = inner[k]
            if c != "\\":
                out.append(c)
                k += 1
                continue
            k += 1
            e = inner[k:k + 1]
            if e in ("x", "u", "U"):
                width = {"x": 2, "u": 4, "U": 8}[e]
                digits = inner[k + 1:k + 1 + width]
                try:
                    out.append(chr(int(digits, 16)))
                except ValueError:
                    raise YamlError(f"line {n}: bad escape \\{e}{digits}") from None
                k += 1 + width
            elif e in _ESCAPES:
                out.append(_ESCAPES[e])
                k += 1
            else:
                raise YamlError(f"line {n}: unknown escape \\{e}")
        return "".join(out)

    def quoted_value(self, i, rest):
        n = self.lines[i][2]
        q = rest[0]
        text, j = rest, i
        while self._end(text, q) == -1:
            j += 1
            if j >= len(self.lines):
                raise YamlError(f"line {n}: a quoted string that never closes")
            text += "\n" + self.lines[j][3].strip()
        end = self._end(text, q)
        token, tail = text[:end], text[end:].strip()
        if tail and not tail.startswith("#"):
            raise YamlError(f"line {n}: unexpected text after the closing quote: {tail[:30]!r}")
        if "\n" in token:
            token = q + self._fold(token[1:-1], q) + q
        return self.quoted(token, n), j + 1

    @staticmethod
    def _fold(inner, q):
        """A quoted scalar written over several lines: a line break is a space, a blank line
        is a line break, and a trailing backslash joins lines without either."""
        if q == '"':
            inner = re.sub(r"\\\n[ \t]*", "", inner)
        parts = inner.split("\n")
        out, pending = parts[0].rstrip(" \t"), 0
        for part in parts[1:]:
            part = part.strip(" \t")
            if not part:
                pending += 1
                continue
            out += ("\n" * pending) if pending else " "
            out += part
            pending = 0
        return out

    @staticmethod
    def _end(text, q):
        """Index just past the quote that closes the string opening `text`, or -1."""
        j = 1
        while j < len(text):
            if text[j] == "\\" and q == '"':
                j += 2
                continue
            if text[j] == q:
                if q == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                    continue
                return j + 1
            j += 1
        return -1

    def flow(self, i, rest):
        n = self.lines[i][2]
        text, j = rest, i
        while not self._balanced(text):
            j += 1
            if j >= len(self.lines):
                raise YamlError(f"line {n}: a [ or {{ that never closes")
            text += " " + self.lines[j][1]
        value, pos = self._flow_value(text, 0, n)
        tail = text[pos:].strip()
        if tail and not tail.startswith("#"):
            raise YamlError(f"line {n}: unexpected text after the closing bracket: {tail[:30]!r}")
        return value, j + 1

    @staticmethod
    def _balanced(text):
        depth, q, k = 0, "", 0
        while k < len(text):
            c = text[k]
            if q:
                if c == "\\" and q == '"':
                    k += 1
                elif c == q:
                    q = ""
            elif c in "\"'":
                q = c
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
            k += 1
        return depth <= 0

    @staticmethod
    def _ws(s, p):
        while p < len(s) and s[p] in " \t":
            p += 1
        return p

    def _flow_value(self, s, p, n):
        p = self._ws(s, p)
        if p >= len(s):
            raise YamlError(f"line {n}: a flow collection ends too early")
        if s[p] == "[":
            out, p = [], p + 1
            while True:
                p = self._ws(s, p)
                if s[p:p + 1] == "]":
                    return out, p + 1
                value, p = self._flow_value(s, p, n)
                out.append(value)
                p = self._ws(s, p)
                if s[p:p + 1] == ",":
                    p += 1
                elif s[p:p + 1] != "]":
                    raise YamlError(f"line {n}: expected , or ] in a flow list")
        if s[p] == "{":
            out, p = {}, p + 1
            while True:
                p = self._ws(s, p)
                if s[p:p + 1] == "}":
                    return out, p + 1
                key, p = self._flow_scalar(s, p, n, ":,}", typed=False)
                p = self._ws(s, p)
                value = None
                if s[p:p + 1] == ":":
                    p = self._ws(s, p + 1)
                    if s[p:p + 1] not in (",", "}"):
                        value, p = self._flow_value(s, p, n)
                out[key] = value
                p = self._ws(s, p)
                if s[p:p + 1] == ",":
                    p += 1
                elif s[p:p + 1] != "}":
                    raise YamlError(f"line {n}: expected , or }} in a flow mapping")
        return self._flow_scalar(s, p, n, ",]}", typed=True)

    def _flow_scalar(self, s, p, n, stops, typed):
        if s[p:p + 1] in ("&", "*", "!"):
            raise YamlUnsupported(f"line {n}: anchors, aliases and tags are not read")
        if s[p:p + 1] in ("\"", "'"):
            end = self._end(s[p:], s[p])
            if end == -1:
                raise YamlError(f"line {n}: a quoted string that never closes")
            return self.quoted(s[p:p + end], n), p + end
        k = p
        while k < len(s) and s[k] not in stops and not (s[k] == ":" and s[k + 1:k + 2] in ("", " ")):
            k += 1
        token = s[p:k].strip()
        return (resolve(token) if typed else token), k


def load(text):
    """The document as Python values. The root must be a mapping."""
    reader = _Reader(text)
    try:
        value, i = reader.block(0)
    except RecursionError:
        raise YamlError("nested too deeply") from None
    i = reader.skip_blank(i)
    if i < len(reader.lines):
        raise YamlError(f"line {reader.lines[i][2]}: content this reader could not place")
    if not isinstance(value, dict):
        raise YamlError("the YAML root must be a mapping")
    return value
