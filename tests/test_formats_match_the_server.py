"""The readers behind `kaeris check` cut a file into the same keys and the same text as the
server that translated it (backend/translator.py, md_format.py).

Dev-only: set KAERIS_BACKEND to the backend folder (and run with the backend's dependencies:
PyYAML, fluent.syntax). Without it the file is skipped, like the other parity tests; the
package itself ships with none of this.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import formats as fm   # noqa: E402
from kaeris import ftl             # noqa: E402
from kaeris import mdformat        # noqa: E402
from kaeris import miniyaml        # noqa: E402


def _backend():
    folder = os.environ.get("KAERIS_BACKEND", "")
    if not folder or not os.path.isfile(os.path.join(folder, "translator.py")):
        return None
    if folder not in sys.path:
        sys.path.insert(0, folder)
    try:
        import translator
        import md_format
        return translator, md_format
    except Exception:
        return None


BK = _backend()
pytestmark = pytest.mark.skipif(BK is None, reason="KAERIS_BACKEND is not set (dev-only parity)")

PO = '''msgid ""
msgstr ""
"Language: en\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

# a comment
#: src/a.py:1
msgid "Hello, %(name)s"
msgstr ""

msgctxt "menu"
msgid "Open"
msgstr ""

msgctxt "button"
msgid "Open"
msgstr "Offnen"

msgid "%d file"
msgid_plural "%d files"
msgstr[0] ""
msgstr[1] ""

msgid "A long "
"line in two parts"
msgstr ""

msgid "Say \\"hi\\"\\n"
msgstr "Sag \\"hallo\\"\\n"
'''

STRINGS = '''/* greeting */
"greeting" = "Hello, %@";
"quote" = "He said \\"hi\\"\\nnow";
"path" = "C:\\\\dir\\\\new";
'''

ANDROID = '''<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">KAERIS</string>
    <string name="hi">Hello, %1$s &amp; welcome</string>
    <string name="styled">Click <b>here</b> now</string>
    <string name="quoted">It\\'s \\"fine\\"\\nnext</string>
    <string name="skip" translatable="false">DEBUG</string>
    <string name="empty"></string>
    <plurals name="files">
        <item quantity="one">%d file</item>
        <item quantity="other">%d files</item>
    </plurals>
    <string-array name="days">
        <item>Mon</item>
        <item>Tue</item>
    </string-array>
</resources>
'''

XLIFF = '''<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">
  <file original="a" source-language="en" datatype="plaintext">
    <body>
      <trans-unit id="a"><source>Hello, {name}</source></trans-unit>
      <trans-unit id="b"><source>Click <g id="1">here</g> &amp; go</source><target>x</target></trans-unit>
      <group><trans-unit id="c"><source>Nested</source></trans-unit></group>
      <trans-unit id="d"><source></source></trans-unit>
    </body>
  </file>
</xliff>
'''

RESX = '''<?xml version="1.0" encoding="utf-8"?>
<root>
  <resheader name="resmimetype"><value>text/microsoft-resx</value></resheader>
  <data name="Greeting" xml:space="preserve"><value>Hello, {0}</value></data>
  <data name="Multi" xml:space="preserve"><value>Line one
Line two</value></data>
  <data name="Icon" type="System.Drawing.Bitmap, System.Drawing" mimetype="application/x-microsoft.net.object.bytearray.base64"><value>AAAA</value></data>
</root>
'''

PROPERTIES = '''# comment
! another
greeting=Hello, {0}
path = C:\\\\dir
multi = first \\
        second
uni=Gr\\u00fc\\u00dfe \\ud83d\\ude00
colon: value
key\\ with\\ space = v
empty=
'''

CSV = '''key,en,de
greeting,"Hello, {name}",
multi,"line one
line two",x
guarded,'=1+1,
blank,,
'''

YAML = '''en:
  greeting: "Hello, %{name}"   # trailing
  save: Save changes
  cart:
    items: "You have {count} items"
    list:
      - one
      - "two, still"
  block: |
    first
    second
  folded: >-
    some folded
    text
  flag: true
  number: 3
  nothing:
'''

FTL = '''# comment
## group
-brand = KAERIS
greeting = Hello, { $name }!
about = About { -brand }
multi =
    Line one
    Line two { $x }
login =
    .label = Log in
    .title = Sign in to { -brand }
emails = { $n ->
    [one] { $n } email
   *[other] { $n } emails
  }
status = You have { NUMBER($n, minimumFractionDigits: 2) } points
platform = { $os ->
    [windows] Press Start
   *[other] Press the key
  }
beside = You have { $n ->
    [one] one item
   *[other] many items
  } here
'''

MD = '''---
title: Welcome to the docs
slug: welcome
tags: [a, b]
---

# Welcome

Read the [docs](https://example.com/docs) and run `kaeris check`.

- Fast
- Free [link][ref]

> A quote

| Name | Value |
|------|-------|
| a    | Hello |

```bash
npm install
```

[ref]: https://example.com
'''


def _po_key(k):
    dup = re.search("\x04#(\\d+)$", k)
    if dup:
        k = k[:dup.start()]
    plural = k.endswith("\x01")
    if plural:
        k = k[:-1]
    ctx = None
    if "\x04" in k:
        ctx, k = k.split("\x04", 1)
    out = fm.key(k, *([f"ctx:{ctx}"] if ctx else []))
    if dup:
        out = fm.key(out, f"dup:{dup.group(1)}")
    return fm.key(out, "plural") if plural else out


def _android_key(k):
    if "\x1eplural\x1e" in k:
        name, qty = k.split("\x1eplural\x1e", 1)
        return fm.key(name, f"plural:{qty}")
    if "\x1earray\x1e" in k:
        name, i = k.split("\x1earray\x1e", 1)
        return fm.key(name, f"array:{i}")
    return k


def test_strings():
    t, _ = BK
    assert fm.read_strings(STRINGS) == t._parse_strings(STRINGS)


def test_po_as_a_source():
    t, _ = BK
    theirs = t._parse_po(PO, {})
    assert dict(fm.read_po(PO, "source")) == {_po_key(k): v for k, v in theirs.items()}


def test_po_repeated_entries():
    t, _ = BK
    text = 'msgid "a"\nmsgstr ""\n\nmsgid "a"\nmsgstr "b"\n\nmsgid "%d x"\nmsgid_plural "%d xs"\nmsgstr[0] ""\nmsgstr[1] ""\n\nmsgid "%d x"\nmsgid_plural "%d xs"\nmsgstr[0] ""\nmsgstr[1] ""\n'
    theirs = t._parse_po(text, {})
    assert dict(fm.read_po(text, "source")) == {_po_key(k): v for k, v in theirs.items()}


def test_android_per_quantity_keys():
    t, _ = BK
    theirs = t._parse_android_xml(ANDROID)
    mine = fm.read_android(ANDROID, fold=False)
    assert dict(mine) == {_android_key(k): v for k, v in theirs.items()}


def test_android_folded_plurals_are_where_the_server_folds_them():
    t, _ = BK
    flat = t._parse_android_xml(ANDROID)
    folded, groups = t._fold_plurals(flat, "android-xml", {})
    mine = fm.read_android(ANDROID)
    assert [k for k in mine if "plural" in k] == [fm.key(g.split("\x1efold\x1e")[0], "plural")
                                                 for g in groups]
    assert len(mine) == len(folded)


def test_xliff_source():
    t, _ = BK
    assert dict(fm.read_xliff(XLIFF, "source")) == t._parse_xliff(XLIFF)[0]


def test_resx():
    t, _ = BK
    assert dict(fm.read_resx(RESX)) == t._parse_resx(RESX)[0]


def test_properties():
    t, _ = BK
    assert dict(fm.read_properties(PROPERTIES)) == t._parse_properties(PROPERTIES)[0]


def test_csv_source_column():
    t, _ = BK
    _, by_col = fm.read_csv(CSV)
    assert dict(by_col["en"]) == t._parse_csv(CSV)[0]


def test_yaml():
    t, _ = BK
    theirs, rooted = t._parse_yaml(YAML, {})
    assert rooted
    mine = fm.read_yaml(YAML)
    assert {k: v for k, v in mine.items() if isinstance(v, str)} == theirs


def test_yaml_without_a_locale_root():
    t, _ = BK
    text = "greeting: Hi\nnav:\n  home: Home\nlist:\n  - a\n  - b\n"
    theirs, rooted = t._parse_yaml(text, {})
    assert not rooted
    assert {k: v for k, v in fm.read_yaml(text).items() if isinstance(v, str)} == theirs


def test_fluent_keys_and_text():
    t, _ = BK
    theirs = t._parse_ftl(FTL)
    mine, problems = ftl.parse(FTL, fold=False)
    assert problems == []
    assert mine == theirs


def test_fluent_folds_the_plurals_the_server_folds():
    t, _ = BK
    flat = t._parse_ftl(FTL)
    _folded, groups = t._fold_plurals(flat, "ftl", {"__ftl__": FTL})
    mine, _ = ftl.parse(FTL)
    assert sorted(k for k in mine if k.endswith(ftl.FOLD_MARK)) == sorted(
        g.split("\x1efold\x1e")[0] + ftl.FOLD_MARK for g in groups)


def test_markdown():
    _, md = BK
    assert mdformat.parse_markdown(MD) == md.parse_markdown(MD, {})


def test_markdown_front_matter_blocks_and_crlf_free():
    _, md = BK
    text = "# A\n\ntext one\ntext two\n\n1. x\n2. y\n\nSetext\n======\n\n<div>\nhtml\n</div>\n"
    assert mdformat.parse_markdown(text) == md.parse_markdown(text, {})


def test_the_nplurals_table_is_the_servers():
    t, _ = BK
    for lang, n in fm._NPLURALS.items():
        assert t._PLURAL_RULES[lang][0] == n, lang


def test_yaml_reader_reads_what_the_server_reads_in_a_real_locale_file():
    t, _ = BK
    text = ("en:\n  errors:\n    messages:\n      blank: \"can't be blank\"\n"
            "      too_long:\n        one: 'is too long (maximum is 1 character)'\n"
            "        other: 'is too long (maximum is %{count} characters)'\n"
            "  date:\n    formats:\n      default: \"%Y-%m-%d\"\n    day_names: [Sunday, Monday]\n")
    theirs, _ = t._parse_yaml(text, {})
    mine = miniyaml.load(text)
    assert {k: v for k, v in fm.read_yaml(text).items() if isinstance(v, str)} == theirs
    assert mine["en"]["date"]["day_names"] == ["Sunday", "Monday"]
