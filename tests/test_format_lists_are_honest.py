"""Found 04.08 (evening) while checking every channel `.xcstrings` and Markdown had to reach:
the README says "13 formats" and names them all, `kaeris --help` names them all — and the two
windows that face a STRANGER still listed the pre-04.08 set as if it were complete:

  * `kaeris/__init__.py` — the package's own docstring
  * `pyproject.toml` `description` — the one line PyPI shows in search results, where an
    iOS developer looking for a String Catalog tool decides whether to click

Neither is a lie about a feature; both make our newest formats invisible in the place people
look first. The rule this test enforces is not "name all 13 everywhere" — a PyPI tagline has
to stay short — but "a short list must SAY it is short". A closed list that omits formats we
support reads as the complete answer.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every format the CLI reads, taken from the `files` argument's own help text — the list a
# user is told to obey. Deriving it here means a 14th format cannot be added without this
# test noticing.
_HELP_LIST = re.search(
    r"Source file\(s\) \(([^)]+)\)", (ROOT / "kaeris" / "cli.py").read_text(encoding="utf-8"))


def canonical_exts() -> set[str]:
    assert _HELP_LIST, "the `files` argument no longer documents which extensions it takes"
    return {e.strip().lstrip(".").lower() for e in _HELP_LIST.group(1).split("/") if e.strip()}


# How each format may be named in prose. A window "names" a format if any of its spellings is
# there — `.xcstrings` is usually written as "Xcode String Catalog", Markdown never as ".md".
SPELLINGS = {
    "json": ("json",),
    "yml": ("yaml", "yml"),
    "yaml": ("yaml", "yml"),
    "strings": (".strings",),
    "xcstrings": (".xcstrings", "string catalog"),
    "md": ("markdown", ".md"),
    "mdx": ("markdown", "mdx"),
    "po": (".po", "gettext"),
    "arb": ("arb",),
    "xml": ("android",),
    "csv": ("csv",),
    "xliff": ("xliff",),
    "properties": (".properties",),
    "resx": ("resx",),
    "ftl": (".ftl", "fluent"),
}

# Phrases that admit the list is a sample rather than the whole set.
OPEN_ENDED = ("& more", "and more", "& 6 more", "more formats", "13 formats", "…", "...")


def windows() -> dict[str, str]:
    init_doc = (ROOT / "kaeris" / "__init__.py").read_text(encoding="utf-8")
    doc = re.match(r'\s*"""(.*?)"""', init_doc, re.S)
    assert doc, "the package docstring is gone"

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    desc = re.search(r'^description\s*=\s*"([^"]+)"', pyproject, re.M)
    assert desc, "pyproject has no description — that line is the PyPI search snippet"

    return {"kaeris/__init__.py docstring": doc.group(1),
            "pyproject.toml description": desc.group(1)}


def test_every_format_has_a_known_spelling():
    """Guard on the guard: a new format must get its prose spellings here, or the check below
    would silently stop covering it."""
    missing = canonical_exts() - set(SPELLINGS)
    assert not missing, f"no prose spelling defined for {sorted(missing)}"


def test_a_short_format_list_says_that_it_is_short():
    exts = canonical_exts()
    for where, text in windows().items():
        low = text.lower()
        named = {e for e in exts if any(s in low for s in SPELLINGS[e])}
        if not named:
            continue                       # mentions no formats at all — nothing to promise
        unnamed = exts - named
        if not unnamed:
            continue                       # names every one of them — complete and honest
        assert any(marker in low for marker in OPEN_ENDED), (
            f"{where} lists formats but omits {sorted(unnamed)} without saying the list is "
            f"partial — a reader takes it for the complete set:\n  {text.strip()[:200]}")


def test_the_newest_formats_are_visible_where_strangers_look():
    """The two formats added 04.08 are our wedge into Apple and docs projects. A stranger who
    searches PyPI for a String Catalog tool has one line to go on: if neither window names
    them, that search never reaches us."""
    joined = " ".join(windows().values()).lower()
    for fmt, spellings in (("String Catalogs", SPELLINGS["xcstrings"]),
                           ("Markdown", SPELLINGS["md"])):
        assert any(s in joined for s in spellings), (
            f"{fmt} is supported but named in neither the package docstring nor the PyPI "
            "description")
