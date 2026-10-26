"""Markdown and MDX, read the way the server reads them: a copy of the parsing half of
backend/md_format.py, so `kaeris check` cuts a document into the same numbered segments the
translation was made from (`md.0001`, `md.0002`, ...) and the same protected runs (inline code,
URLs, tags) become `{0}`, `{1}`... placeholders. The writing half is not needed here.
tests/test_check_every_format.py compares the two on a corpus when KAERIS_BACKEND is set.
Stdlib only."""
import json
import re

# Front-matter keys that are configuration — slugs, dates, layout names, paths, taxonomy a
# build step matches on. Translating any of them breaks the site rather than localizing it.
# Every other value is prose a reader sees: until 0.2 only a short list of keys was offered,
# and a portfolio kept `role`, `services: [...]` and every `caption` in English (R10).
FRONT_MATTER_CONFIG = frozenset((
    "slug", "id", "uuid", "key", "date", "pubdate", "publishdate", "published", "updated",
    "updateddate", "lastmod", "created", "expirydate", "draft", "layout", "template", "type",
    "kind", "variant", "permalink", "url", "href", "link", "path", "src", "srcset", "image",
    "images", "img", "cover", "thumbnail", "icon", "avatar", "logo", "video", "audio", "file",
    "lang", "locale", "language", "translationkey", "order", "weight", "sort", "priority",
    "position", "index", "color", "colour", "theme", "class", "classname", "style", "css",
    "status", "canonical", "redirect", "redirect_from", "aliases", "tags", "categories",
    "author", "authors", "email", "twitter", "github", "width", "height", "format", "mime",
))
_CONFIG_SUFFIX_RE = re.compile(
    r"(?:^|[_-])(?:id|url|href|src|slug|date|path)$|[a-z](?:Id|Url|Href|Src|Slug|Date|Path)$")
# A value that is a bare identifier (`field`, `dark-mode`), a path, a URL, a date or a number
# is a setting, whatever its key is called.
_FM_SETTING_RE = re.compile(
    r"^(?:[a-z0-9][a-z0-9_-]*|[./~]\S*|[a-z][a-z0-9+.-]*://\S+|\d{4}-\d\d(?:-\d\d)?\S*"
    r"|[\w.-]+\.[A-Za-z0-9]{2,5}|[-+]?\d[\d_.,]*)$")
_FM_KEY_RE = re.compile(
    r"""^(\s*)((?:-\s+)?)([A-Za-z0-9_.$@-]+|"[^"]*"|'[^']*')(\s*:)(?=\s|$)(\s*)(.*)$""")
_FM_ITEM_RE = re.compile(r"^(\s*)(-)(\s+)(.*)$")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][-+]?\d?$")
_YAML_RESERVED_RE = re.compile(
    r"^(?:~|null|true|false|yes|no|on|off|y|n|[-+]?(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    r"|0x[0-9a-fA-F]+|0o[0-7]+|[-+]?\.inf|\.nan)$", re.IGNORECASE)

_FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
_HEADING_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*?)(\s*\{#[^}]*\}\s*)?$")
_SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_LIST_RE = re.compile(r"^(\s*(?:[-*+]|\d{1,9}[.)])\s+)(.*)$")
_QUOTE_RE = re.compile(r"^(\s{0,3}>+\s?)(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+\S+")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_MDX_STMT_RE = re.compile(r"^\s*(import|export)\s")
_HTML_OPEN_RE = re.compile(r"^\s{0,3}<[A-Za-z/!]")
_INDENTED_CODE_RE = re.compile(r"^(?: {4}|\t)")

# Runs inside a segment that must reach the output byte-identical. Order matters: the earlier
# alternatives win, so a URL inside a link is taken by the link rule, not by the bare-URL rule.
_PROTECT_RE = re.compile(
    r"(?P<code>``[^`]+``|`[^`\n]+`)"                       # inline code
    r"|(?P<autolink><https?://[^>\s]+>)"                   # <https://…>
    r"|(?P<linkurl>(?<=\])\([^)\s]*(?:\s+\"[^\"]*\")?\))"  # the (url "title") half of a link
    r"|(?P<reflink>(?<=\])\[[^\]]*\])"                     # the [ref] half of a reference link
    r"|(?P<tag></?[A-Za-z][A-Za-z0-9.-]*(?:\s[^<>]*)?/?>)"  # HTML / JSX tag
    r"|(?P<expr>\{[^{}\n]*\})"                             # MDX expression / existing placeholder
    r"|(?P<url>https?://[^\s)\]]+)"                        # bare URL
    r"|(?P<entity>&[a-zA-Z]+;|&#\d+;)"                     # HTML entity
)


def protect(text: str) -> tuple[str, list[str]]:
    """Swap must-survive runs for {0}, {1}, … Returns (masked_text, originals)."""
    originals: list[str] = []

    def _sub(m: "re.Match") -> str:
        originals.append(m.group(0))
        return "{%d}" % (len(originals) - 1)

    return _PROTECT_RE.sub(_sub, text), originals


def _is_prose(line: str) -> bool:
    """A line that carries text rather than structure."""
    s = line.strip()
    if not s:
        return False
    return not (_HR_RE.match(line) or _REF_DEF_RE.match(line) or _MDX_STMT_RE.match(line)
                or _HTML_OPEN_RE.match(line) or _INDENTED_CODE_RE.match(line)
                or _SETEXT_RE.match(line))


class _Builder:
    """Collects the skeleton: literal pieces interleaved with numbered segments."""

    def __init__(self):
        self.pieces: list = []          # ("lit", str) | ("seg", key)
        self.flat: dict[str, str] = {}
        self.masks: dict[str, list] = {}
        self.yaml: dict[str, dict] = {}
        self._n = 0

    def lit(self, text: str) -> None:
        if not text:
            return
        if self.pieces and self.pieces[-1][0] == "lit":
            self.pieces[-1] = ("lit", self.pieces[-1][1] + text)
        else:
            self.pieces.append(("lit", text))

    def seg(self, text: str, yaml: dict | None = None) -> None:
        """Add a translatable segment — or a literal, if there is nothing to translate in it."""
        masked, originals = protect(text)
        if not _has_words(masked):
            # Nothing but punctuation, tokens and digits: a lone `code` line, a bare URL,
            # a row of dashes. Sending it to a model can only damage it.
            self.lit(yaml["raw"] if yaml else text)
            return
        self._n += 1
        key = "md.%04d" % self._n
        self.flat[key] = masked
        self.masks[key] = originals
        self.pieces.append(("seg", key))
        if yaml is not None:
            self.yaml[key] = yaml


def _has_words(masked: str) -> bool:
    return bool(re.search(r"[^\W\d_]", masked.replace("{", " ").replace("}", " ")))


def _is_config_key(key: str) -> bool:
    key = key.strip("\"'")
    return key.startswith("_") or key.lower() in FRONT_MATTER_CONFIG or \
        bool(_CONFIG_SUFFIX_RE.search(key))


def _unquote(token: str) -> tuple[str, str]:
    """(quote, decoded value) of one YAML scalar token."""
    if len(token) >= 2 and token[0] == token[-1] == '"':
        try:
            return '"', json.loads(token)
        except ValueError:
            return '"', token[1:-1]
    if len(token) >= 2 and token[0] == token[-1] == "'":
        return "'", token[1:-1].replace("''", "'")
    return "", token


def _split_comment(value: str) -> tuple[str, str]:
    """A plain scalar ends where ` #` starts a comment; a quoted one ends at its quote."""
    if value[:1] == '"':
        m = re.match(r'"(?:[^"\\]|\\.)*"', value)
        return (value[:m.end()], value[m.end():]) if m else (value, "")
    if value[:1] == "'":
        m = re.match(r"'(?:[^']|'')*'", value)
        return (value[:m.end()], value[m.end():]) if m else (value, "")
    m = re.search(r"\s#", value)
    return (value[:m.start()], value[m.start():]) if m else (value, "")


def _scalar(b: "_Builder", token: str, skip: bool, flow: bool) -> None:
    quote, decoded = _unquote(token)
    if skip or not decoded.strip() or _FM_SETTING_RE.match(decoded):
        b.lit(token)
        return
    b.seg(decoded, {"raw": token, "src": decoded, "quote": quote, "flow": flow})


def _flow_items(inner: str) -> list[str] | None:
    """Top-level comma-separated parts of a one-line flow collection, or None if it nests."""
    parts, quote, cur = [], "", ""
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'" and not cur.strip():
            quote = ch
        elif ch in "[]{}":
            return None
        elif ch == ",":
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    if quote:
        return None
    parts.append(cur)
    return parts


def _flow(b: "_Builder", value: str, skip: bool, hard: bool) -> bool:
    """A one-line `[a, b]` or `{k: v, k2: v2}`. False if it is not one we can split safely."""
    is_map = value.startswith("{")
    items = _flow_items(value[1:-1])
    if items is None:
        return False
    b.lit(value[0])
    for n, item in enumerate(items):
        if n:
            b.lit(",")
        body = item.strip()
        b.lit(item[:len(item) - len(item.lstrip())])
        if is_map and body:
            m = re.match(r"^([^:]+?)(\s*:\s*)(.*)$", body)
            if not m:
                b.lit(body)
            else:
                b.lit(m.group(1) + m.group(2))
                if m.group(3):
                    _scalar(b, m.group(3), hard or _is_config_key(m.group(1)), True)
        elif body:
            _scalar(b, body, skip, True)
        if body:
            b.lit(item[len(item.rstrip()):])
    b.lit(value[-1])
    return True


def _value(b: "_Builder", value: str, skip: bool, hard: bool) -> None:
    token, comment = _split_comment(value)
    gap = token[len(token.rstrip()):]
    token = token.rstrip()
    closing = {"[": "]", "{": "}"}.get(token[:1])
    if closing and token.endswith(closing) and _flow(b, token, skip, hard):
        pass
    elif token[:1] in "[{&*!|>%@`":
        b.lit(token)
    else:
        _scalar(b, token, skip, False)
    b.lit(gap + comment)


def _front_matter(lines: list[str], b: _Builder, nl) -> int:
    """Consume YAML front matter if present. Returns the number of lines consumed.

    Line by line, the way people write front matter: `key: value`, nested mappings, block
    lists, one-line flow collections and block scalars. Anything else is kept verbatim. The
    quoting is decided when the document is written (`_yaml_scalar`), because only then is the
    text known — writing a translation back unquoted is what broke builds (R11)."""
    if not lines or lines[0].rstrip() not in ("---", "+++"):
        return 0
    delim = lines[0].rstrip()
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == delim:
            end = i
            break
    if end is None:
        return 0
    b.lit(lines[0] + nl(0))
    if delim == "+++":
        for j in range(1, end + 1):
            b.lit(lines[j] + nl(j))
        return end + 1
    # Open keys: (indent of their children, is configuration, is a `_` service section). Only a
    # `_` section silences everything under it; `image:` is a setting as `image: /a.jpg` but
    # its `caption:` child is prose.
    parents: list[tuple[int, bool, bool]] = []
    j = 1
    while j < end:
        raw = lines[j]
        line, cr = (raw[:-1], "\r") if raw.endswith("\r") else (raw, "")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            b.lit(raw + nl(j))
            j += 1
            continue
        indent = len(line) - len(line.lstrip())
        m = _FM_KEY_RE.match(line)
        item = None if m else _FM_ITEM_RE.match(line)
        if m:
            key_indent = indent + len(m.group(2))
            while parents and parents[-1][0] > (indent + 1 if m.group(2) else indent):
                parents.pop()
            hard = any(h for _, _, h in parents) or m.group(3).strip("\"'").startswith("_")
            skip = hard or _is_config_key(m.group(3))
            value = m.group(6)
            b.lit(m.group(1) + m.group(2) + m.group(3) + m.group(4) + m.group(5))
            if _BLOCK_SCALAR_RE.match(value.strip()):
                b.lit(value + cr + nl(j))
                j += 1
                start = j
                while j < end and (not lines[j].strip() or
                                   len(lines[j]) - len(lines[j].lstrip()) > key_indent):
                    j += 1
                while j > start and not lines[j - 1].strip():
                    j -= 1
                _block(b, lines[start:j], value.strip()[0], skip, nl, j)
                continue
            if value.strip():
                _value(b, value, skip, hard)
            else:
                parents.append((key_indent + 1, skip, hard))
            b.lit(cr + nl(j))
        elif item:
            while parents and parents[-1][0] > indent + 1:
                parents.pop()
            hard = any(h for _, _, h in parents)
            skip = hard or bool(parents and parents[-1][1])
            b.lit(item.group(1) + item.group(2) + item.group(3))
            _value(b, item.group(4), skip, hard)
            b.lit(cr + nl(j))
        else:
            b.lit(raw + nl(j))
        j += 1
    b.lit(lines[end] + nl(end))
    return end + 1


def _block(b: "_Builder", body: list[str], style: str, skip: bool, nl, after: int) -> None:
    """A `|` or `>` block scalar: one segment, indented again when it is written back."""
    if not body:
        return
    raw = "\n".join(body)
    if skip:
        b.lit(raw + nl(after - 1))
        return
    indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
    text = "\n".join(l[indent:].rstrip("\r") for l in body)
    if style == ">":
        text = re.sub(r"(?<=\S)\n(?=\S)", " ", text)
    b.seg(text, {"raw": raw, "src": text, "block": " " * indent})
    b.lit(nl(after - 1))


def parse_markdown(text: str, passthrough: dict | None = None) -> dict[str, str]:
    """Flatten a Markdown/MDX document into translatable segments."""
    lines = text.split("\n")
    b = _Builder()
    last = len(lines) - 1

    def nl(idx: int) -> str:
        """The newline that FOLLOWS line `idx` — empty for the final line, because a document
        without a trailing newline must come back without one."""
        return "\n" if idx < last else ""

    i = _front_matter(lines, b, nl)

    while i < len(lines):
        line = lines[i]

        fence = _FENCE_RE.match(line)
        if fence:                                   # fenced code — verbatim, including the info
            marker = fence.group(2)
            b.lit(line + nl(i))
            i += 1
            while i < len(lines):
                b.lit(lines[i] + nl(i))
                closing = _FENCE_RE.match(lines[i])
                i += 1
                if closing and closing.group(2)[0] == marker[0] and \
                        len(closing.group(2)) >= len(marker) and not closing.group(3).strip():
                    break
            continue

        if _MDX_STMT_RE.match(line) or _HTML_OPEN_RE.match(line):
            # MDX import/export and any block starting with a tag: kept whole, up to the next
            # blank line. Text inside a multi-line component is NOT translated — see the
            # module docstring; guessing JSX boundaries with regexes ships broken pages.
            while i < len(lines) and lines[i].strip():
                b.lit(lines[i] + nl(i))
                i += 1
            continue

        if not line.strip() or _HR_RE.match(line) or _REF_DEF_RE.match(line) \
                or _SETEXT_RE.match(line) or _INDENTED_CODE_RE.match(line):
            b.lit(line + nl(i))
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            b.lit(heading.group(1))
            b.seg(heading.group(2))
            b.lit((heading.group(3) or "") + nl(i))
            i += 1
            continue

        if "|" in line and line.strip().startswith("|"):
            if _TABLE_SEP_RE.match(line):
                b.lit(line + nl(i))
                i += 1
                continue
            parts = line.split("|")
            for idx, cell in enumerate(parts):
                if idx:
                    b.lit("|")
                if cell.strip():
                    lead = cell[:len(cell) - len(cell.lstrip())]
                    trail = cell[len(cell.rstrip()):]
                    b.lit(lead)
                    b.seg(cell.strip())
                    b.lit(trail)
                else:
                    b.lit(cell)
            b.lit(nl(i))
            i += 1
            continue

        quote = _QUOTE_RE.match(line)
        if quote and quote.group(2).strip():
            b.lit(quote.group(1))
            b.seg(quote.group(2))
            b.lit(nl(i))
            i += 1
            continue

        item = _LIST_RE.match(line)
        if item:
            b.lit(item.group(1))
            b.seg(item.group(2))
            b.lit(nl(i))
            i += 1
            continue

        # Paragraph: consecutive prose lines, translated as one unit so the model sees whole
        # sentences rather than fragments. Internal newlines are kept inside the segment.
        para: list[str] = []
        while i < len(lines) and _is_prose(lines[i]) and not _FENCE_RE.match(lines[i]) \
                and not _HEADING_RE.match(lines[i]) and not _LIST_RE.match(lines[i]) \
                and not _QUOTE_RE.match(lines[i]) and not lines[i].strip().startswith("|"):
            para.append(lines[i])
            i += 1
        if para:
            b.seg("\n".join(para))
            b.lit(nl(i - 1))
        else:
            b.lit(line + nl(i))
            i += 1

    if passthrough is not None:
        passthrough["__markdown__"] = {"pieces": b.pieces, "masks": b.masks}
        if b.yaml:
            passthrough["__markdown__"]["yaml"] = b.yaml
    return b.flat
