"""Two things a site noticed on its first run in fa, bn and zh-Hant (client report, 0.2.31).

Digits: the model writes Persian ۲۰۲۷ and Bengali ২০২৭ — correct for those scripts, and what
some sites want, but this one shows years and prices in 0-9 everywhere. `--digits latin`
turns every non-ASCII decimal digit in the result into 0-9; placeholders are left as they are.
The default stays `native`: nobody else's output changes.

File names: the server names its files after the language code as the API spells it,
`zh-Hant.json` and `devlog/zh-Hant/`. A project that already has `zh-hant.json` got a second
file next to it on Linux (and its own file renamed on Windows). Now an existing file or folder
that differs only in case is written to under its own name; `--lang-case lower` does the same
for a project that has none yet.
"""
import io
import json
import unittest.mock as mock

import pytest

from kaeris import cli as K
from kaeris import digits


# ── digits ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("native, latin", [
    ("در سال ۲۰۲۷", "در سال 2027"),            # Persian (Extended Arabic-Indic)
    ("২০২৭ সালে", "2027 সালে"),                # Bengali
    ("सन् २०२७", "सन् 2027"),                  # Devanagari
    ("عام ٢٠٢٧", "عام 2027"),                  # Arabic-Indic
    ("खोज ४०४", "खोज 404"),
    ("already 2027", "already 2027"),
])
def test_native_digits_become_latin(native, latin):
    assert digits.to_latin(native) == latin


@pytest.mark.parametrize("text", [
    "{count} ۲ مورد",
    "%1$s ۲",
    "{{name}} ۲",
    "<b>۲</b>",
])
def test_placeholders_are_left_alone(text):
    out = digits.to_latin(text)
    for token in ("{count}", "%1$s", "{{name}}", "<b>", "</b>"):
        if token in text:
            assert token in out
    assert "2" in out


def test_a_digit_inside_a_placeholder_is_not_touched():
    # A placeholder is the developer's text, not the model's; whatever is in it stays.
    assert digits.to_latin("{n۲} و ۳") == "{n۲} و 3"


def test_text_without_digits_is_returned_as_is():
    s = "سلام دنیا"
    assert digits.to_latin(s) is s or digits.to_latin(s) == s


# ── the CLI ──────────────────────────────────────────────────────────────────

class _Server:
    def __init__(self, answers):
        self.answers = answers          # {lang: text}
        self.jobs = []

    def key_info(self):
        return {}

    def languages(self):
        return {l: l for l in ("fa", "bn", "de", "zh-Hant", "pt-BR")}

    def submit(self, fname, content, langs, glossary=None, **kw):
        self.jobs.append((fname, content.decode("utf-8"), list(langs)))
        return str(len(self.jobs) - 1)

    def poll(self, job, **kw):
        return {"failed_langs": []}

    def download(self, job):
        fname, text, langs = self.jobs[int(job)]
        ext = fname.rsplit(".", 1)[1]
        if ext == "json":
            keys = json.loads(text)
            return {f"{l}.json": json.dumps({k: self.answers[l] for k in keys},
                                            ensure_ascii=False).encode() for l in langs}
        return {f"{l}.{ext}": self.answers[l].encode() for l in langs}

    def preview(self, job):
        return {}

    def receipt(self, job):
        raise RuntimeError


def _run(srv, monkeypatch, *argv):
    monkeypatch.setattr(K, "_client", lambda args: srv)
    err, out = io.StringIO(), io.StringIO()
    with mock.patch("sys.stderr", err), mock.patch("sys.stdout", out):
        code = K.main(["translate", *argv])
    return code, err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "en.json").write_text('{"year": "Coming in {year}, 2027"}',
                                              encoding="utf-8")
    (tmp_path / "devlog" / "en").mkdir(parents=True)
    (tmp_path / "devlog" / "en" / "post.md").write_text("# Launch 2027\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


ANSWERS = {"fa": "عرضه در {year}، ۲۰۲۷", "bn": "{year}, ২০২৭ সালে", "de": "Ab {year}, 2027",
           "zh-Hant": "{year}，2027 年", "pt-BR": "Em {year}, 2027"}


def test_default_keeps_native_digits(project, monkeypatch):
    code, err = _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "fa,bn")
    assert code == 0, err
    assert "۲۰۲۷" in (project / "i18n" / "fa.json").read_text("utf-8")


def test_digits_latin_rewrites_json_and_markdown(project, monkeypatch):
    srv = _Server(dict(ANSWERS, fa="# عرضه ۲۰۲۷\n"))
    code, err = _run(srv, monkeypatch, "devlog/en/post.md", "--langs", "fa", "--digits", "latin")
    assert code == 0, err
    assert (project / "devlog" / "fa" / "post.md").read_text("utf-8") == "# عرضه 2027\n"

    code, err = _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "fa,bn",
                     "--digits", "latin")
    assert code == 0, err
    fa = json.loads((project / "i18n" / "fa.json").read_text("utf-8"))
    bn = json.loads((project / "i18n" / "bn.json").read_text("utf-8"))
    assert fa["year"] == "عرضه در {year}، 2027"
    assert bn["year"] == "{year}, 2027 সালে"


def test_digits_latin_under_only_new(project, monkeypatch):
    code, err = _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "fa",
                     "--only-new", "--digits", "latin")
    assert code == 0, err
    assert json.loads((project / "i18n" / "fa.json").read_text("utf-8"))["year"] == \
        "عرضه در {year}، 2027"


def test_digits_from_the_config(project, monkeypatch):
    (project / "kaeris.json").write_text(json.dumps({"digits": "latin"}), encoding="utf-8")
    _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "bn")
    assert "2027" in (project / "i18n" / "bn.json").read_text("utf-8")


def test_digits_is_in_translate_help(capsys):
    with pytest.raises(SystemExit):
        K.main(["translate", "--help"])
    out = capsys.readouterr().out
    assert "--digits" in out and "latin" in out and "native" in out
    # A legacy Windows console (cp1252) must still be able to print it: an example like
    # "fa ۲۰۲۷" in the help made `kaeris translate --help` raise UnicodeEncodeError there.
    out.encode("cp1252")


# ── file-name case ───────────────────────────────────────────────────────────

def test_an_existing_lowercase_file_is_written_under_its_own_name(project, monkeypatch):
    (project / "i18n" / "zh-hant.json").write_text("{}", encoding="utf-8")
    code, err = _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "zh-Hant")
    assert code == 0, err
    names = sorted(p.name for p in (project / "i18n").iterdir())
    assert names == ["en.json", "zh-hant.json"]
    assert "2027" in (project / "i18n" / "zh-hant.json").read_text("utf-8")


def test_an_existing_lowercase_folder_is_used(project, monkeypatch):
    (project / "devlog" / "zh-hant").mkdir()
    code, err = _run(_Server(dict(ANSWERS, **{"zh-Hant": "# 2027 年\n"})), monkeypatch,
                     "devlog/en/post.md", "--langs", "zh-Hant")
    assert code == 0, err
    assert sorted(p.name for p in (project / "devlog").iterdir()) == ["en", "zh-hant"]
    assert (project / "devlog" / "zh-hant" / "post.md").is_file()


def test_only_new_reads_and_writes_the_existing_lowercase_file(project, monkeypatch):
    (project / "i18n" / "zh-hant.json").write_text('{"old": "舊"}', encoding="utf-8")
    code, err = _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "zh-Hant",
                     "--only-new")
    assert code == 0, err
    assert sorted(p.name for p in (project / "i18n").iterdir()
                  if p.suffix == ".json" and "tm" not in p.name) == ["en.json", "zh-hant.json"]
    assert "2027" in (project / "i18n" / "zh-hant.json").read_text("utf-8")


def test_without_a_file_and_without_the_option_the_name_is_as_before(project, monkeypatch):
    _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "--langs", "zh-Hant,pt-BR")
    names = {p.name for p in (project / "i18n").iterdir()}
    assert {"zh-Hant.json", "pt-BR.json"} <= names


def test_lang_case_lower_names_new_files_in_lowercase(project, monkeypatch):
    code, err = _run(_Server(ANSWERS), monkeypatch, "i18n/en.json", "devlog/en/post.md",
                     "--langs", "zh-Hant", "--lang-case", "lower")
    assert code == 0, err
    assert (project / "i18n" / "zh-hant.json").is_file()
    assert sorted(p.name for p in (project / "devlog").iterdir()) == ["en", "zh-hant"]


def test_check_finds_the_lowercase_file(project, monkeypatch):
    (project / "i18n" / "zh-hant.json").write_text('{"year": "{year}，2027 年"}',
                                                   encoding="utf-8")
    err = io.StringIO()
    with mock.patch("sys.stderr", err), mock.patch("sys.stdout", io.StringIO()):
        code = K.main(["check", "--source", "i18n/en.json", "--langs", "zh-Hant"])
    assert code == 0, err.getvalue()
