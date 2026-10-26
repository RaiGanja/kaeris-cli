"""Locale files of every format `kaeris translate` writes, read into {key: text} for `kaeris check`.

The readers mirror the server's parsers (backend/translator.py, md_format.py): the same keys and
the same text per key, because if the two read one file differently, `check` and the API disagree
about it and nobody can tell which is right. They are copies, not imports: this package ships
alone and has no dependencies. tests/test_formats_match_the_server.py compares them on a corpus
(set KAERIS_BACKEND to the backend folder); where a reader is deliberately NOT the server's, the
difference is named in its docstring.

Two deliberate differences, both because `check` compares a translation with its source:
  * a plural group is read as one ICU string (Android <plurals>, Fluent selectors) or kept per
    msgstr[n] (.po), so "Japanese has one form, Russian has three" is judged by the plural rules
    of the TARGET language and not reported as missing or extra keys;
  * in a TARGET file, an entry that is empty where the format means "not translated yet"
    (.po msgstr "", an XLIFF <target> that is empty or still state="new", a blank CSV cell) is
    absent, so it is reported as a missing key.

Keys that are not the developer's own text are written between ⟦ ⟧, which `kaeris check` prints
as `files (plural)`.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET

from . import ftl as _ftl
from . import mdformat as _md
from . import miniyaml as _yaml
from . import xcstrings as _xc
from .encoding import read_text

OPEN, CLOSE = "⟦", "⟧"


class FormatError(ValueError):
    """The file cannot be read as the format its name says. The text is for the person."""


class Flat(dict):
    """{key: text} plus what the reader found out about the file as a whole.

    `notes` are findings that belong to no single key ({"key", "severity", "msg"});
    `skip_pairwise` says the strings cannot be paired with the source's by key;
    `count_optional` names the keys whose translation may leave a lone count out."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.notes = []
        self.skip_pairwise = False
        self.count_optional = set()   # keys whose text may leave the number out (.po singular)


EXTENSIONS = {
    ".json": "json", ".arb": "arb", ".xcstrings": "xcstrings",
    ".yml": "yaml", ".yaml": "yaml", ".strings": "strings", ".po": "po", ".xml": "android",
    ".csv": "csv", ".xliff": "xliff", ".xlf": "xliff", ".properties": "properties",
    ".resx": "resx", ".ftl": "ftl", ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown",
}
# One file holds the source and every language: nothing to look up per language on disk.
MERGED = ("xcstrings", "csv")

SUPPORTED_TEXT = ("JSON, ARB, YAML, .strings, .xcstrings, .po, Android XML, CSV, XLIFF, "
                  ".properties, .resx, Fluent and Markdown")


def kind_of(path):
    name = path.lower()
    for ext, kind in EXTENSIONS.items():
        if name.endswith(ext):
            return kind
    return None


def key(base, *parts):
    return base + "".join(f"{OPEN}{p}{CLOSE}" for p in parts)


def find_lang(by_lang, lang):
    """The entry for `lang` in a file that holds several, spelled however the file spells it
    (`pt-BR`, `pt_br`)."""
    if lang in by_lang:
        return by_lang[lang]
    want = lang.replace("_", "-").lower()
    for name, value in by_lang.items():
        if str(name).replace("_", "-").lower() == want:
            return value
    return None


# ── .strings ──────────────────────────────────────────────────────────────────

_STRINGS_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;')


def _unescape(s, mapping):
    """One left-to-right pass, so an escaped backslash is consumed as one unit."""
    if "\\" not in s:
        return s
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in mapping:
                out.append(mapping[nxt])
            else:
                out.append("\\")
                out.append(nxt)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_STRINGS_UNESCAPE = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}
_PO_UNESCAPE = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}


def read_strings(text):
    return Flat({_unescape(m.group(1), _STRINGS_UNESCAPE): _unescape(m.group(2), _STRINGS_UNESCAPE)
                 for m in _STRINGS_RE.finditer(text)})


# ── .po ───────────────────────────────────────────────────────────────────────

def _po_unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _po_records(text):
    records, cur, field, idx = [], None, None, 0

    def start():
        nonlocal cur
        cur = {"ctx": None, "id": None, "id_plural": None, "strs": {}}
        records.append(cur)

    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("msgctxt "):
            start()
            field = "ctx"
            cur["ctx"] = _unescape(_po_unquote(s[8:]), _PO_UNESCAPE)
        elif s.startswith("msgid_plural "):
            field = "id_plural"
            if cur is not None:
                cur["id_plural"] = _unescape(_po_unquote(s[13:]), _PO_UNESCAPE)
        elif s.startswith("msgid "):
            if cur is None or field != "ctx" or cur["id"] is not None:
                start()
            field = "id"
            cur["id"] = _unescape(_po_unquote(s[6:]), _PO_UNESCAPE)
        elif s.startswith("msgstr["):
            close = s.find("]")
            idx = int(s[7:close]) if close > 7 and s[7:close].isdigit() else 0
            field = "str"
            if cur is not None:
                cur["strs"][idx] = _unescape(_po_unquote(s[close + 1:]), _PO_UNESCAPE)
        elif s.startswith("msgstr "):
            field, idx = "str", 0
            if cur is not None:
                cur["strs"][0] = _unescape(_po_unquote(s[7:]), _PO_UNESCAPE)
        elif s.startswith('"') and s.endswith('"') and len(s) >= 2 and cur is not None and field:
            chunk = _unescape(s[1:-1], _PO_UNESCAPE)
            if field == "ctx":
                cur["ctx"] = (cur["ctx"] or "") + chunk
            elif field == "id":
                cur["id"] = (cur["id"] or "") + chunk
            elif field == "id_plural":
                cur["id_plural"] = (cur["id_plural"] or "") + chunk
            else:
                cur["strs"][idx] = cur["strs"].get(idx, "") + chunk
    return records


# The number of forms gettext needs per language, from the server's table (backend/translator.py,
# _PLURAL_RULES). Only languages with three or more are enforced: those are where a header
# copied from an English file ("nplurals=2") is certainly wrong.
_NPLURALS = {"ru": 3, "uk": 3, "hr": 3, "sr": 3, "pl": 3, "cs": 3, "sk": 3, "ro": 3, "lt": 3,
             "lv": 3, "sl": 4, "ar": 6}


def read_po(text, role="source", lang=""):
    """Keys are the msgid (with ⟦ctx:…⟧ for a msgctxt, ⟦plural⟧ for the plural form, ⟦dup:n⟧ for
    a repeat). As a target, an empty msgstr is absent, and msgstr[2..n] are kept as
    ⟦plural:2⟧… with a note for a plural that has fewer forms than its header declares."""
    records = _po_records(text)
    out, seen, header = Flat(), {}, None
    out.count_optional = set()

    def uniq(base):
        if base not in seen:
            seen[base] = 0
            return base
        seen[base] += 1
        return key(base, f"dup:{seen[base]}")

    for r in records:
        mid = r["id"]
        if mid == "" and header is None:
            header = r["strs"].get(0) or ""
            continue
        if mid is None or mid == "":
            continue
        base = key(mid, f"ctx:{r['ctx']}") if r["ctx"] else mid
        s0, s1 = r["strs"].get(0), r["strs"].get(1)
        target = role == "target"
        if r["id_plural"] is None:
            k = uniq(base)
            if s0 or not target:
                out[k] = s0 if s0 else mid
            continue
        k0 = uniq(base)
        k1 = key(k0, "plural")
        out.count_optional.add(k0)
        if s0 or not target:
            out[k0] = s0 if s0 else mid
        if s1 or not target:
            out[k1] = s1 if s1 else r["id_plural"]
        if target:
            for n in sorted(i for i in r["strs"] if i >= 2):
                if r["strs"][n]:
                    out[key(k0, f"plural:{n}")] = r["strs"][n]
    if role == "target":
        _po_plural_notes(out, records, header, lang)
    return out


def _po_plural_notes(out, records, header, lang):
    declared = None
    m = re.search(r"nplurals\s*=\s*(\d+)", header or "")
    if m:
        declared = int(m.group(1))
    base_lang = (lang or "").replace("_", "-").split("-")[0].lower()
    need = _NPLURALS.get(base_lang)
    if declared is not None and need and declared < need:
        out.notes.append({"key": "", "severity": "error",
                          "msg": f"Plural-Forms declares nplurals={declared}, but {base_lang} needs "
                                 f"{need} plural forms — the extra forms can never be selected"})
    if not declared or declared <= 2:
        return
    for r in records:
        if r["id_plural"] is None or not r["id"]:
            continue
        absent = [i for i in range(2, declared) if not r["strs"].get(i)]
        if absent and r["strs"].get(0) and r["strs"].get(1):
            shown = r["id"] if len(r["id"]) <= 50 else r["id"][:47] + "..."
            forms = ", ".join(f"msgstr[{i}]" for i in absent)
            out.notes.append({"key": shown, "severity": "error",
                              "msg": f"the file declares {declared} plural forms, this entry has "
                                     f"no {forms}"})


# ── XML (Android, XLIFF, RESX) ────────────────────────────────────────────────

_XML_DTD_RE = re.compile(r"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[a-zA-Z0-9][\w:-]*(?:\s[^<>]*?)?/?>")


def _expat_views(text):
    raw = text.encode("utf-8", errors="ignore")
    return [text] + [raw.decode(enc, errors="ignore") for enc in ("utf-16-le", "utf-16-be")]


def _xml_root(text, label):
    for view in _expat_views(text):
        if _XML_DTD_RE.search(view):
            raise FormatError(f"{label} must not contain DOCTYPE or ENTITY declarations")
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        raise FormatError(f"Invalid {label} XML: {e}") from None


def _local_tag(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else tag


def _strip_ns(root):
    for el in root.iter():
        el.tag = _local_tag(el.tag)
        for k in list(el.attrib):
            if isinstance(k, str) and "}" in k:
                el.attrib[k.split("}", 1)[1]] = el.attrib.pop(k)


_ANDROID_UNESCAPE_RE = re.compile(r"\\(.)")
_ANDROID_KEPT_ENTITY_RE = re.compile(r"&(?:lt|gt|amp);")


def _android_unescape(s):
    return _ANDROID_UNESCAPE_RE.sub(lambda m: "\n" if m.group(1) == "n" else m.group(1), s)


def _android_text_protect(seg):
    seg = re.sub(r"&(?=(?:lt|gt|amp);)", "&amp;", seg)
    return _TAG_RE.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), seg)


def _android_inner(el):
    parts = [_android_text_protect(_android_unescape(el.text or ""))]
    for child in el:
        tail = child.tail
        child.tail = None
        try:
            parts.append(ET.tostring(child, encoding="unicode"))
        finally:
            child.tail = tail
        parts.append(_android_text_protect(_android_unescape(tail or "")))
    return "".join(parts).strip()


CLDR_ORDER = ("zero", "one", "two", "few", "many", "other")

# printf-family specifier, as in kaeris/xcstrings.py
_SPEC_RE = re.compile(r"%(?:\d+\$)?[-+ #0]*[\d.*]*(?:hh|h|ll|l|q|L|z|t|j)?[@dioupxXeEfgGcsSaAn%]")
_INT_CONV = set("diuoxX")


def lone_count_to_hash(text):
    """An arm whose only printf specifier is an integer ("%d files") is written with ICU's own
    `#` for the number. Many correct translations leave the number out of the singular
    ("Eine Datei"), and the number is not a placeholder that can crash anything; every other
    specifier (`%1$s`, a second `%d`) stays and is compared."""
    found = [m for m in _SPEC_RE.finditer(text) if m.group(0) != "%%"]
    if len(found) == 1 and found[0].group(0)[-1] in _INT_CONV:
        m = found[0]
        return text[:m.start()] + "#" + text[m.end():]
    return text


def _balanced(text):
    depth = 0
    for ch in text:
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            return False
    return depth == 0


def read_android(text, fold=True):
    """<string>, <plurals> and <string-array>. With `fold`, each <plurals> is ONE ICU string
    (as the server folds it for the model, but with a lone `%d` written `#`: see
    lone_count_to_hash); `fold=False` is the server's own per-quantity keys."""
    root = _xml_root(text, "Android XML")
    if root.tag != "resources":
        raise FormatError("Android XML root element must be <resources>")
    out = Flat()
    for el in root:
        if el.get("translatable", "true").lower() == "false":
            continue
        name = el.get("name", "").strip()
        if not name:
            continue
        if el.tag == "string":
            out[name] = _android_inner(el)
        elif el.tag == "plurals":
            items = {}
            for item in el:
                if item.tag != "item":
                    continue
                qty = item.get("quantity", "").strip()
                value = _android_inner(item)
                if qty and value:
                    items[qty] = value
            texts = {c: items[c] for c in CLDR_ORDER if c in items}
            if fold and "other" in texts and all(_balanced(t) for t in texts.values()) \
                    and len(texts) == len(items):
                arms = " ".join(f"{c} {{{lone_count_to_hash(texts[c])}}}" for c in CLDR_ORDER if c in texts)
                out[key(name, "plural")] = f"{{count, plural, {arms}}}"
            else:
                for qty, value in items.items():
                    out[key(name, f"plural:{qty}")] = value
        elif el.tag == "string-array":
            for i, item in enumerate(it for it in el if it.tag == "item"):
                out[key(name, f"array:{i}")] = _android_inner(item)
    return out


def _inner_xml(el):
    parts = [el.text or ""]
    for child in el:
        parts.append(ET.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


_UNTRANSLATED_STATES = {"new", "needs-translation", "needs-adaptation", "needs-l10n"}


def read_xliff(text, role="source"):
    """XLIFF 1.2: {trans-unit id: <source>}; as a target, {id: <target>} for the units that have
    one (empty, or still state="new"/"needs-translation", is not translated)."""
    root = _xml_root(text, "XLIFF")
    _strip_ns(root)
    if _local_tag(root.tag) != "xliff":
        raise FormatError("XLIFF root element must be <xliff>")
    if (root.get("version") or "").strip().startswith("2"):
        raise FormatError("XLIFF 2.0 is not supported yet — export XLIFF 1.2 "
                          "(<trans-unit> with <source>/<target>)")
    out = Flat()
    for fel in root:
        if _local_tag(fel.tag) != "file":
            continue
        for tu in fel.iter():
            if _local_tag(tu.tag) != "trans-unit":
                continue
            uid = tu.get("id")
            if uid is None:
                continue
            src_el = tgt_el = None
            for child in tu:
                lt = _local_tag(child.tag)
                if lt == "source":
                    src_el = child
                elif lt == "target":
                    tgt_el = child
            if src_el is None:
                continue
            if role == "source":
                value = _inner_xml(src_el)
            else:
                value = _inner_xml(tgt_el) if tgt_el is not None else ""
                if tgt_el is not None and (tgt_el.get("state") or "").lower() in _UNTRANSLATED_STATES:
                    value = ""
            if value:
                out[uid] = value
    return out


def read_resx(text):
    root = _xml_root(text, "RESX")
    _strip_ns(root)
    if _local_tag(root.tag) != "root":
        raise FormatError("RESX root element must be <root>")
    out = Flat()
    for el in root:
        if _local_tag(el.tag) != "data":
            continue
        name = el.get("name")
        if not name or el.get("type") or el.get("mimetype"):
            continue
        value_el = next((c for c in el if _local_tag(c.tag) == "value"), None)
        out[name] = (value_el.text if value_el is not None else el.text) or ""
    return out


# ── .properties ───────────────────────────────────────────────────────────────

_PROP_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f"}


def _properties_unescape(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "u" and i + 6 <= n:
                try:
                    code = int(s[i + 2:i + 6], 16)
                except ValueError:
                    code = None
                if code is not None:
                    if 0xd800 <= code <= 0xdbff and s[i + 6:i + 8].lower() == "\\u":
                        try:
                            low = int(s[i + 8:i + 12], 16)
                        except ValueError:
                            low = None
                        if low is not None and 0xdc00 <= low <= 0xdfff:
                            out.append(chr(0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00)))
                            i += 12
                            continue
                    out.append(chr(code))
                    i += 6
                    continue
            out.append(_PROP_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _prop_is_continuation(s):
    cnt, j = 0, len(s) - 1
    while j >= 0 and s[j] == "\\":
        cnt += 1
        j -= 1
    return cnt % 2 == 1


def _prop_split_kv(s):
    i, n, key_chars = 0, len(s), []
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            key_chars.append(c)
            key_chars.append(s[i + 1])
            i += 2
            continue
        if c in " \t\f=:":
            break
        key_chars.append(c)
        i += 1
    raw_key = "".join(key_chars)
    while i < n and s[i] in " \t\f":
        i += 1
    if i < n and s[i] in "=:":
        i += 1
        while i < n and s[i] in " \t\f":
            i += 1
    return raw_key, s[i:]


def read_properties(text):
    out = Flat()
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip("\r")
        stripped = line.lstrip(" \t\f")
        if stripped == "" or stripped[0] in "#!":
            i += 1
            continue
        logical = stripped
        while _prop_is_continuation(logical) and i + 1 < n:
            i += 1
            logical = logical[:-1] + lines[i].rstrip("\r").lstrip(" \t\f")
        i += 1
        raw_key, raw_val = _prop_split_kv(logical)
        out[_properties_unescape(raw_key)] = _properties_unescape(raw_val)
    return out


# ── CSV (one file, one column per language) ───────────────────────────────────

_CSV_FORMULA_RE = re.compile(r"^'*[=+\-@\t\r]")


def _csv_unguard(value):
    v = value or ""
    return v[1:] if v.startswith("'") and _CSV_FORMULA_RE.match(v[1:]) else v


def read_csv(text):
    """(source column name, {column name: Flat}). The first column is the key, the second the
    source text, every later column a language — the layout the server writes. A blank cell is
    absent: in a spreadsheet blank is how "not translated yet" looks."""
    rows = [r for r in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        raise FormatError("a CSV needs a header row and a key column + a source-text column")
    header = [(h or "").strip() for h in rows[0]]
    if len(header) < 2 or not header[1]:
        raise FormatError("the CSV header must name the source-text column (second column)")
    by_col = {h: Flat() for h in header[1:] if h}
    for r in rows[1:]:
        if not r or not (r[0] or "").strip():
            continue
        k = _csv_unguard(r[0].strip())
        for col, name in enumerate(header[1:], 1):
            if name and col < len(r) and (r[col] or "").strip():
                by_col[name][k] = _csv_unguard(r[col])
    return header[1], by_col


# ── YAML, Fluent, Markdown, ICU keys ──────────────────────────────────────────

_LOCALE_ROOT_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,4})*$")


def read_yaml(text):
    """The document without its locale root (`en:` / `de:` / `pt-BR:`), leaves flattened."""
    from . import incremental as inc
    try:
        data = _yaml.load(text)
    except _yaml.YamlUnsupported as e:
        raise FormatError(f"{e} (kaeris check reads the YAML that locale files use; "
                          f"it will not guess at the rest)") from None
    except _yaml.YamlError as e:
        raise FormatError(f"Invalid YAML: {e}") from None
    if len(data) == 1 and _LOCALE_ROOT_RE.match(next(iter(data))) and isinstance(next(iter(data.values())), dict):
        data = next(iter(data.values()))
    return Flat(inc.flatten_all(data))


def read_fluent(text):
    flat, problems = _ftl.parse(text)
    out = Flat(flat)
    out.notes = [{"key": "", "severity": "warn", "msg": p} for p in problems]
    return out


def read_markdown(text):
    return Flat(_md.parse_markdown(text.replace("\r\n", "\n")))


# ── the door ──────────────────────────────────────────────────────────────────

def read_one(path, role="source", lang=""):
    """One file, one language, as {key: text}. `role` is "target" for a translation."""
    kind = kind_of(path)
    try:
        text = read_text(path)
    except OSError:
        raise
    except ValueError as e:
        raise FormatError(f"cannot read {path}: {e}") from None
    try:
        if kind == "strings":
            return read_strings(text)
        if kind == "po":
            return read_po(text, role, lang)
        if kind == "yaml":
            return read_yaml(text)
        if kind == "android":
            return read_android(text)
        if kind == "xliff":
            return read_xliff(text, role)
        if kind == "properties":
            return read_properties(text)
        if kind == "resx":
            return read_resx(text)
        if kind == "ftl":
            return read_fluent(text)
        if kind == "markdown":
            return read_markdown(text)
    except RecursionError:
        raise FormatError("the file is nested deeper than the readers handle") from None
    raise FormatError(f"{path}: no reader for this format")


def read_merged(path):
    """(source language, {language: Flat}) for a file that holds all of them."""
    kind = kind_of(path)
    try:
        text = read_text(path)
    except OSError:
        raise
    except ValueError as e:
        raise FormatError(f"cannot read {path}: {e}") from None
    if kind == "csv":
        return read_csv(text)
    return _xc.load(path)
