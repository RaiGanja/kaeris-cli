"""The YAML reader `kaeris check` uses reads what PyYAML (the server's reader) reads, and refuses
what it does not read. PyYAML is a dev-only comparison: the package itself has no dependencies."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kaeris import miniyaml as my  # noqa: E402

CORPUS = {
    "rails": 'en:\n  greeting: "Hello, %{name}"   # trailing comment\n  save: Save changes\n'
             '  cart:\n    items: "You have {count} items"\n',
    "types": "a: yes\nb: no\nc: ~\nd: null\ne: 5\nf: 3.5\ng: 0x1f\nh: on\ni: 'yes'\nj: \"5\"\nk:\n",
    "lists": "items:\n  - a\n  - \"b c\"\n  - key: v\n    other: w\n  - - nested\n    - list\nsame:\n- 1\n- 2\n",
    "flow": "inline: [x, \"y, z\", {k: v, n: 2}]\nmap: {a: 1, b: [1, 2]}\nempty: []\nem: {}\n",
    "block_literal": "text: |\n  line one\n    indented\n  line three\nafter: x\n",
    "block_strip": "text: |-\n  one\n  two\n\nnext: y\n",
    "block_keep": "text: |+\n  one\n\nnext: y\n",
    "folded": "text: >\n  folded\n  text here\n\n  new paragraph\nafter: z\n",
    "folded_strip": "text: >-\n  folded\n  text\n",
    "quoted_multi": 'multi: "first\n  second\n\n  third"\nsq: \'it\'\'s\'\n',
    "escapes": 'a: "tab\\there \\u00e9 \\x41 \\\\ \\" end"\nb: "line\\nbreak"\n',
    "plain_multi": "plain: long text\n  continues here\nnext: ok\n",
    "urls": "url: http://x.y/z\nmail: a@b.c\ntime: 12:30\nratio: 3:2\n",
    "colon_in_value": "title: 'Note: this'\nother: a - b\n",
    "numbers_as_keys": "404: not found\nyes: affirmative\n",
    "dates": "d: 2001-02-03\nnot_a_date: 2001-02-03 text\n",
    "document_start": "---\nen:\n  a: b\n",
    "comments_and_blanks": "# top\n\nen:\n\n  # inner\n  a: b   # c\n\n  c: d\n",
    "unicode": "en:\n  hi: Привет, 世界\n",
    "nested_deep": "a:\n  b:\n    c:\n      d:\n        e: f\n",
    "empty_values": "a:\nb: ''\nc: \"\"\n",
    "dash_strings": "a: -1\nb: - not a list\nc: -x\n",
}


def _norm(value):
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_reads_what_pyyaml_reads(name):
    yaml = pytest.importorskip("yaml")
    text = CORPUS[name]
    if name == "numbers_as_keys":
        # the server keeps keys as their written text; PyYAML types them
        expected = {"404": "not found", "yes": "affirmative"}
    elif name == "dash_strings":
        # `b: - not a list` is invalid YAML for PyYAML; this reader takes it as text
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(text)
        assert my.load(text)["b"] == "- not a list"
        return
    else:
        expected = _norm(yaml.safe_load(text))
    assert my.load(text) == expected


@pytest.mark.parametrize("text,word", [
    ("en:\n  a: &x Hello\n  b: *x\n", "anchor"),
    ("a: &x 1\n", "anchor"),
    ("other:\n  <<: {a: 1}\n", "merge"),
    ("a: !!str 5\n", "tag"),
    ("a: 1\n---\nb: 2\n", "document"),
    ("? complex\n: value\n", "complex"),
    ("%YAML 1.2\n---\na: 1\n", "directive"),
])
def test_refuses_by_name_what_it_does_not_read(text, word):
    with pytest.raises(my.YamlUnsupported) as e:
        my.load(text)
    assert word in str(e.value).lower()


@pytest.mark.parametrize("text", [
    "a: 'never closes\n",
    "a:\n\tb: 1\n",
    "- just\n- a list\n",
    "just a scalar\n",
    "a: [1, 2\n",
])
def test_broken_files_are_errors(text):
    with pytest.raises(ValueError):
        my.load(text)
