"""kaeris-glossary.json: a term of the project and how each language must say it.

    {"terms": [
      {"term": "workspace",
       "translations": {"de": "Arbeitsbereich", "fr": ["espace de travail", "espace"]},
       "case_sensitive": false, "pos": "noun", "note": "the shared project area"},
      {"term": "KAERIS", "keep": true}
    ]}

`translations` names, per language, the word to use; a list gives accepted variants, the first
is the one the translator is asked for. `keep: true` is the old `--keep`: never translated.
`pos` and `note` only go to the translator, as context.

The same file serves two places. `translate` hands the terms to the model and keeps the lock
and the project memory honest about them; `check` reads every translation against them.

How a translation is judged, and why so carefully:
  * In the source a term counts as a whole word ("Pro" is not in "product"), in any case unless
    `case_sensitive`, with an English plural ("workspaces").
  * In the translation the agreed word passes, and so does an inflected form of it: every word
    of it found by its stem (Arbeitsbereichs, рабочем пространстве).
  * Where even the stem is missing, a language whose words keep their shape (French, Spanish,
    Japanese…) gets an error: the agreed word is simply not there. A language whose words change
    shape or glue together (Russian, Polish, Finnish, German compounds…) gets a warning: "not
    found" there can still be correct grammar, and a false red build teaches people to ignore
    the check.
"""
import hashlib
import json
import os
import re

from .detectors import _PH_RE
from .encoding import read_text

FILENAME = "kaeris-glossary.json"

# Words keep their shape and stand apart: a miss here is a fact, so it is an error.
_STRICT = frozenset("en fr es it pt ca gl id ms vi tl fil zh ja th lo km my bo".split())
# No spaces between words, or words glued into compounds, or prefixes glued on (Arabic
# «ال», Hebrew «ה»): a rendering is looked for anywhere in the text, not at a word start.
_ANYWHERE = frozenset("zh ja th lo km my bo de nl sv da no nb nn is fi et hu af ar he yi".split())

_TAG_RE = re.compile(r"</?[a-zA-Z0-9][\w:-]*(?:\s[^<>]*?)?/?>")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_NO_SPACE_CHARS_RE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿฀-໿က-႟ក-៿ༀ-࿿]")


class GlossaryError(ValueError):
    """A glossary file that cannot be used. Said, never skipped: a glossary that silently does
    nothing looks exactly like a glossary that found nothing wrong."""


def _base(lang):
    return str(lang).replace("_", "-").split("-")[0].lower()


class Entry:
    __slots__ = ("term", "translations", "case_sensitive", "pos", "note")

    def __init__(self, term, translations, case_sensitive=False, pos="", note=""):
        self.term = term
        self.translations = translations      # {lang as written: [rendering, …]}
        self.case_sensitive = case_sensitive
        self.pos = pos
        self.note = note

    def renderings(self, lang):
        """The accepted words for `lang`: its own entry (pt-BR, pt_br), else its language's (pt)."""
        want = str(lang).replace("_", "-").lower()
        for name, words in self.translations.items():
            if name.replace("_", "-").lower() == want:
                return list(words)
        for name, words in self.translations.items():
            if name.replace("_", "-").lower() == _base(lang):
                return list(words)
        return []

    def for_api(self):
        out = {"term": self.term, "translations": {k: list(v) for k, v in self.translations.items()}}
        if self.case_sensitive:
            out["case_sensitive"] = True
        if self.pos:
            out["pos"] = self.pos
        if self.note:
            out["note"] = self.note
        return out

    def fingerprint(self, lang):
        """What this term asks of one language, as a short hash — recorded in the lock so a
        change to it is noticed on the next run."""
        body = json.dumps([self.term, self.case_sensitive, self.renderings(lang), self.pos,
                           self.note], ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


class Glossary:
    def __init__(self, entries=(), keep=()):
        self.entries = list(entries)
        self.keep = list(keep)

    def __bool__(self):
        return bool(self.entries or self.keep)

    def for_api(self):
        return [e.for_api() for e in self.entries]

    def fingerprints(self, lang):
        return {e.term.casefold(): e.fingerprint(lang) for e in self.entries if e.renderings(lang)}


def _text(value, what, where, limit):
    if not isinstance(value, str) or not value.strip():
        raise GlossaryError(f"{where}: {what} must be a non-empty string")
    value = " ".join(value.split())
    if len(value) > limit:
        raise GlossaryError(f"{where}: {what} is longer than {limit} characters")
    return value


def parse(data):
    """A Glossary from the file's JSON: {"terms": [...]} or the list itself."""
    items = data.get("terms") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise GlossaryError('expected {"terms": [ … ]} or a list of terms')
    entries, keep, seen = [], [], set()
    for i, item in enumerate(items, 1):
        where = f"term #{i}"
        if not isinstance(item, dict):
            raise GlossaryError(f"{where}: expected an object with \"term\"")
        term = _text(item.get("term"), '"term"', where, 60)
        where = f'term #{i} "{term}"'
        if term.casefold() in seen:
            raise GlossaryError(f"{where}: listed twice")
        seen.add(term.casefold())
        if item.get("keep") is True:
            keep.append(term)
            continue
        cs = item.get("case_sensitive", False)
        if not isinstance(cs, bool):
            raise GlossaryError(f"{where}: case_sensitive must be true or false")
        tr = item.get("translations")
        if not isinstance(tr, dict) or not tr:
            raise GlossaryError(f'{where}: "translations" must be an object like '
                                f'{{"de": "Arbeitsbereich"}} (or set "keep": true)')
        translations = {}
        for lang, words in tr.items():
            words = words if isinstance(words, list) else [words]
            if not words:
                raise GlossaryError(f'{where}: "translations" for {lang} is empty')
            translations[str(lang)] = [_text(w, f'"translations" for {lang}', where, 80)
                                       for w in words]
        pos = _text(item["pos"], '"pos"', where, 20) if item.get("pos") else ""
        note = _text(item["note"], '"note"', where, 160) if item.get("note") else ""
        entries.append(Entry(term, translations, cs, pos, note))
    return Glossary(entries, keep)


def load(path):
    try:
        data = json.loads(read_text(path))
    except OSError as e:
        raise GlossaryError(f"{path}: cannot read it ({e.strerror or e})")
    except ValueError as e:
        raise GlossaryError(f"{path}: not valid JSON ({e})")
    try:
        return parse(data)
    except GlossaryError as e:
        raise GlossaryError(f"{path}: {e}")


def find(explicit=None, config=None):
    """The glossary of this run: --glossary-file, else "glossary_file" in kaeris.json, else
    kaeris-glossary.json in the current folder if there is one. None when there is none.
    A file that was NAMED and is missing is an error; the default one is optional."""
    path = explicit or (config or {}).get("glossary_file")
    if path:
        if not os.path.isfile(path):
            raise GlossaryError(f"{path}: glossary file not found")
        return load(path)
    if os.path.isfile(FILENAME):
        return load(FILENAME)
    return None


# ── matching ─────────────────────────────────────────────────────────────────

def _visible(text):
    """The words a reader sees: placeholders, tags and links are not vocabulary — "{workspace}"
    is a variable, not the term."""
    text = _URL_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _PH_RE.sub(" ", text)


def _source_spans(text, entry):
    words = entry.term.split()
    if _NO_SPACE_CHARS_RE.search(entry.term):
        rx = re.escape(entry.term)
    else:
        rx = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?:s|es)?(?!\w)"
    flags = 0 if entry.case_sensitive else re.IGNORECASE
    return [m.span() for m in re.finditer(rx, text, flags)]


def term_in(source, entry):
    """True when the source uses the term as a word of its own."""
    return bool(_source_spans(_visible(source), entry)) if isinstance(source, str) else False


def _stem(word):
    w = word.casefold()
    return w if len(w) <= 4 else w[:max(4, len(w) - 2)]


def _found(text, rendering, lang):
    """"exact", "stem" or None: how `rendering` occurs in the translation."""
    hay = text.casefold()
    anywhere = _base(lang) in _ANYWHERE or bool(_NO_SPACE_CHARS_RE.search(rendering))
    want = rendering.casefold()
    if anywhere:
        if want in hay:
            return "exact"
    elif re.search(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in want.split()) + r"(?!\w)", hay):
        return "exact"
    words = want.split() or [want]
    for w in words:
        stem = _stem(w)
        if anywhere:
            if stem not in hay:
                return None
        elif not re.search(r"(?<!\w)" + re.escape(stem), hay):
            return None
    return "stem"


def _same_case(text, rendering):
    return rendering in text or (rendering[:1].swapcase() + rendering[1:]) in text


def faults(source, translation, lang, entries):
    """Findings of one string, in check's shape: [{"severity", "msg"}]."""
    if not entries or not isinstance(source, str) or not isinstance(translation, str):
        return []
    src = _visible(source)
    tr = _visible(translation)
    spans = {id(e): _source_spans(src, e) for e in entries if e.renderings(lang)}
    out = []
    for e in entries:
        own = spans.get(id(e)) or []
        longer = [s for o in entries if o is not e and len(o.term) > len(e.term)
                  for s in spans.get(id(o), [])]
        own = [s for s in own if not any(ls <= s[0] and s[1] <= le for ls, le in longer)]
        if not own:
            continue
        words = e.renderings(lang)
        hits = [(w, _found(tr, w, lang)) for w in words]
        hits = [(w, how) for w, how in hits if how]
        want = words[0]
        if not hits:
            if _base(lang) in _STRICT:
                out.append({"severity": "error", "msg": f'glossary: "{e.term}" should be "{want}"'})
            else:
                out.append({"severity": "warn",
                            "msg": f'glossary: "{e.term}" should be "{want}" — not found, not even '
                                   f'as an inflected form; check this line'})
            continue
        if e.case_sensitive and any(how == "exact" for _, how in hits) \
                and not any(_same_case(tr, w) for w, how in hits if how == "exact"):
            out.append({"severity": "warn",
                        "msg": f'glossary: "{e.term}" should be written "{want}" — found in '
                               f'another case'})
    return out


def complies(source, translation, lang, entries):
    """True when nothing in `entries` asks this translation for a word it does not have."""
    return not faults(source, translation, lang, entries)


def filter_reuse(hits, source_flat, entries):
    """Project-memory hits minus the ones that say a term the old way: the memory is keyed by
    text, so a glossary change would otherwise be undone by it."""
    if not hits or not entries:
        return hits
    out = {}
    for lang, kv in hits.items():
        kept = {k: v for k, v in kv.items()
                if complies(source_flat.get(k, ""), v, lang, entries)}
        if kept:
            out[lang] = kept
    return out


def changed_terms(locked, current):
    """Terms of one language whose rule is new or different since the lock. A term removed
    from the glossary asks nothing of anybody, so it is not a change that costs a translation."""
    locked = locked if isinstance(locked, dict) else {}
    return {t for t, fp in current.items() if locked.get(t) != fp}


def stale_keys(source_flat, existing_flat, lang, entries, changed):
    """Keys whose source uses a changed term and whose translation does not say it the new way.
    A translation that already does is left alone — and not paid for again."""
    if not changed:
        return set()
    pick = [e for e in entries if e.term.casefold() in changed]
    out = set()
    for k, src in source_flat.items():
        tr = existing_flat.get(k)
        if not isinstance(src, str) or not isinstance(tr, str):
            continue
        if any(term_in(src, e) for e in pick) and not complies(src, tr, lang, pick):
            out.add(k)
    return out
