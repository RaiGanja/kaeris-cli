"""`kaeris check` reads every format `kaeris translate` writes, not only JSON and ARB.

Each format has one fixture with a known fault in each of the two ways a locale breaks an app:
a placeholder that no longer matches the source, and a key that is not translated. A good
translation of the same strings must pass, so the check is shown to tell the two apart.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(source, langs="de", *extra):
    """Through the real argv, like CI runs it."""
    r = subprocess.run(
        [sys.executable, "-m", "kaeris", "check", "--source", str(source), "--langs", langs, *extra],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"})
    return r.returncode, r.stdout + r.stderr


def put(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


# name -> (source file, files that make a good project, files with a broken placeholder,
#          files with an untranslated key, word the report must contain for the placeholder)
FORMATS = {}


def fmt(name, source, good, bad_placeholder, missing, ph_word):
    FORMATS[name] = (source, good, bad_placeholder, missing, ph_word)


fmt("yaml", "en.yml",
    {"en.yml": 'en:\n  greeting: "Hello, %{name}"\n  save: Save changes\n  cart:\n    items: "You have {count} items"\n',
     "de.yml": 'de:\n  greeting: "Hallo, %{name}"\n  save: Änderungen speichern\n  cart:\n    items: "Sie haben {count} Artikel"\n'},
    {"de.yml": 'de:\n  greeting: "Hallo, %{nom}"\n  save: Änderungen speichern\n  cart:\n    items: "Sie haben {count} Artikel"\n'},
    {"de.yml": 'de:\n  greeting: "Hallo, %{name}"\n  save: Änderungen speichern\n'},
    "greeting")

fmt("strings", "en.strings",
    {"en.strings": '"greeting" = "Hello, %@";\n"save" = "Save changes";\n"count" = "%d items";\n',
     "de.strings": '"greeting" = "Hallo, %@";\n"save" = "Änderungen speichern";\n"count" = "%d Artikel";\n'},
    {"de.strings": '"greeting" = "Hallo, %@";\n"save" = "Änderungen speichern";\n"count" = "Artikel";\n'},
    {"de.strings": '"greeting" = "Hallo, %@";\n"count" = "%d Artikel";\n'},
    "count")

PO_SRC = ('msgid ""\nmsgstr ""\n"Language: en\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
          'msgid "Hello, %(name)s"\nmsgstr ""\n\nmsgid "Save changes"\nmsgstr ""\n')
PO_HEAD_DE = ('msgid ""\nmsgstr ""\n"Language: de\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n')
fmt("po", "en.po",
    {"en.po": PO_SRC,
     "de.po": PO_HEAD_DE + 'msgid "Hello, %(name)s"\nmsgstr "Hallo, %(name)s"\n\n'
                           'msgid "Save changes"\nmsgstr "Änderungen speichern"\n'},
    {"de.po": PO_HEAD_DE + 'msgid "Hello, %(name)s"\nmsgstr "Hallo, %(nom)s"\n\n'
                           'msgid "Save changes"\nmsgstr "Änderungen speichern"\n'},
    {"de.po": PO_HEAD_DE + 'msgid "Hello, %(name)s"\nmsgstr "Hallo, %(name)s"\n\n'
                           'msgid "Save changes"\nmsgstr ""\n'},
    "name")

fmt("arb", "app_en.arb",
    {"app_en.arb": json.dumps({"@@locale": "en", "greeting": "Hello, {name}", "save": "Save changes"}),
     "app_de.arb": json.dumps({"@@locale": "de", "greeting": "Hallo, {name}", "save": "Speichern"})},
    {"app_de.arb": json.dumps({"@@locale": "de", "greeting": "Hallo, {nom}", "save": "Speichern"})},
    {"app_de.arb": json.dumps({"@@locale": "de", "greeting": "Hallo, {name}"})},
    "greeting")

fmt("json", "en.json",
    {"en.json": json.dumps({"greeting": "Hello, {name}", "save": "Save changes"}),
     "de.json": json.dumps({"greeting": "Hallo, {name}", "save": "Änderungen speichern"})},
    {"de.json": json.dumps({"greeting": "Hallo, {nom}", "save": "Änderungen speichern"})},
    {"de.json": json.dumps({"greeting": "Hallo, {name}"})},
    "greeting")

ANDROID = ('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
           '    <string name="greeting">%s</string>\n    <string name="save">%s</string>\n</resources>\n')
fmt("android", "res/values/strings.xml",
    {"res/values/strings.xml": ANDROID % ("Hello, %1$s", "Save changes"),
     "res/values-de/strings.xml": ANDROID % ("Hallo, %1$s", "Änderungen speichern")},
    {"res/values-de/strings.xml": ANDROID % ("Hallo, %2$s", "Änderungen speichern")},
    {"res/values-de/strings.xml": '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                                  '    <string name="greeting">Hallo, %1$s</string>\n</resources>\n'},
    "greeting")

fmt("csv", "translations.csv",
    {"translations.csv": 'key,en,de\ngreeting,"Hello, {name}","Hallo, {name}"\nsave,Save changes,Änderungen speichern\n'},
    {"translations.csv": 'key,en,de\ngreeting,"Hello, {name}","Hallo, {nom}"\nsave,Save changes,Änderungen speichern\n'},
    {"translations.csv": 'key,en,de\ngreeting,"Hello, {name}","Hallo, {name}"\nsave,Save changes,\n'},
    "greeting")

XLIFF = ('<?xml version="1.0" encoding="UTF-8"?>\n<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
         '  <file original="app" source-language="en"%s datatype="plaintext">\n    <body>\n%s    </body>\n  </file>\n</xliff>\n')


def _units(pairs):
    return "".join(f'      <trans-unit id="{k}"><source>{s}</source>{"<target>%s</target>" % t if t is not None else ""}'
                   f'</trans-unit>\n' for k, s, t in pairs)


fmt("xliff", "en.xliff",
    {"en.xliff": XLIFF % ("", _units([("greeting", "Hello, {name}", None), ("save", "Save changes", None)])),
     "de.xliff": XLIFF % (' target-language="de"', _units([("greeting", "Hello, {name}", "Hallo, {name}"),
                                                           ("save", "Save changes", "Änderungen speichern")]))},
    {"de.xliff": XLIFF % (' target-language="de"', _units([("greeting", "Hello, {name}", "Hallo, {nom}"),
                                                          ("save", "Save changes", "Änderungen speichern")]))},
    {"de.xliff": XLIFF % (' target-language="de"', _units([("greeting", "Hello, {name}", "Hallo, {name}"),
                                                          ("save", "Save changes", "")]))},
    "greeting")

fmt("properties", "en.properties",
    {"en.properties": "greeting=Hello, {0}\nsave=Save changes\n",
     "de.properties": "greeting=Hallo, {0}\nsave=\\u00c4nderungen speichern\n"},
    {"de.properties": "greeting=Hallo, {1}\nsave=\\u00c4nderungen speichern\n"},
    {"de.properties": "greeting=Hallo, {0}\n"},
    "greeting")

RESX = ('<?xml version="1.0" encoding="utf-8"?>\n<root>\n'
        '  <resheader name="resmimetype"><value>text/microsoft-resx</value></resheader>\n%s</root>\n')


def _data(pairs):
    return "".join(f'  <data name="{k}" xml:space="preserve"><value>{v}</value></data>\n' for k, v in pairs)


fmt("resx", "en.resx",
    {"en.resx": RESX % _data([("Greeting", "Hello, {0}"), ("Save", "Save changes")]),
     "de.resx": RESX % _data([("Greeting", "Hallo, {0}"), ("Save", "Änderungen speichern")])},
    {"de.resx": RESX % _data([("Greeting", "Hallo, {1}"), ("Save", "Änderungen speichern")])},
    {"de.resx": RESX % _data([("Greeting", "Hallo, {0}")])},
    "Greeting")

fmt("ftl", "en.ftl",
    {"en.ftl": "greeting = Hello, { $name }!\nsave = Save changes\n",
     "de.ftl": "greeting = Hallo, { $name }!\nsave = Änderungen speichern\n"},
    {"de.ftl": "greeting = Hallo, { $nom }!\nsave = Änderungen speichern\n"},
    {"de.ftl": "greeting = Hallo, { $name }!\n"},
    "greeting")

MD_EN = "# Welcome\n\nRead the [docs](https://example.com/docs) and run `kaeris check`.\n\n- Fast\n- Free\n"
fmt("markdown", "en.md",
    {"en.md": MD_EN,
     "de.md": "# Willkommen\n\nLesen Sie die [Dokumentation](https://example.com/docs) und führen Sie `kaeris check` aus.\n\n- Schnell\n- Kostenlos\n"},
    {"de.md": "# Willkommen\n\nLesen Sie die Dokumentation und führen Sie `kaeris check` aus.\n\n- Schnell\n- Kostenlos\n"},
    {"de.md": "# Willkommen\n\nLesen Sie die [Dokumentation](https://example.com/docs) und führen Sie `kaeris check` aus.\n\n- Schnell\n"},
    "{")


def _catalog(strings):
    return json.dumps({"sourceLanguage": "en", "version": "1.0", "strings": strings}, ensure_ascii=False)


def _u(v):
    return {"stringUnit": {"state": "translated", "value": v}}


fmt("xcstrings", "Localizable.xcstrings",
    {"Localizable.xcstrings": _catalog({
        "greeting": {"localizations": {"en": _u("Hello, %@"), "de": _u("Hallo, %@")}},
        "save": {"localizations": {"en": _u("Save changes"), "de": _u("Änderungen speichern")}}})},
    {"Localizable.xcstrings": _catalog({
        "greeting": {"localizations": {"en": _u("Hello, %@"), "de": _u("Hallo")}},
        "save": {"localizations": {"en": _u("Save changes"), "de": _u("Änderungen speichern")}}})},
    {"Localizable.xcstrings": _catalog({
        "greeting": {"localizations": {"en": _u("Hello, %@"), "de": _u("Hallo, %@")}},
        "save": {"localizations": {"en": _u("Save changes")}}})},
    "greeting")

NAMES = sorted(FORMATS)


@pytest.mark.parametrize("name", NAMES)
def test_a_good_translation_passes(name, tmp_path):
    source, good, _bad, _missing, _word = FORMATS[name]
    put(tmp_path, good)
    code, out = run(tmp_path / source)
    assert code == 0, out
    assert "complete" in out


@pytest.mark.parametrize("name", NAMES)
def test_a_broken_placeholder_fails_the_build(name, tmp_path):
    source, good, bad, _missing, word = FORMATS[name]
    put(tmp_path, good)
    put(tmp_path, bad)
    code, out = run(tmp_path / source)
    assert code == 1, out
    assert "placeholder" in out.lower() or "lost" in out.lower(), out
    assert word in out, out


@pytest.mark.parametrize("name", NAMES)
def test_an_untranslated_key_fails_the_build(name, tmp_path):
    source, good, _bad, missing, _word = FORMATS[name]
    put(tmp_path, good)
    for rel in missing:
        (tmp_path / rel).unlink(missing_ok=True)
    put(tmp_path, missing)
    code, out = run(tmp_path / source)
    assert code == 1, out
    assert "missing" in out.lower(), out


@pytest.mark.parametrize("name", NAMES)
def test_json_report_is_the_same_shape_for_every_format(name, tmp_path):
    source, good, bad, _missing, _word = FORMATS[name]
    put(tmp_path, good)
    put(tmp_path, bad)
    code, out = run(tmp_path / source, "de", "--json")
    body = json.loads(out[out.index("{"):])
    assert code == 1
    assert body["ok"] is False
    assert {"missing", "extra", "placeholder_issues", "faults", "warnings"} <= set(body)


# ── native plurals ────────────────────────────────────────────────────────────

ANDROID_PLURALS = ('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                   '    <plurals name="files">\n%s    </plurals>\n</resources>\n')


def _items(pairs):
    return "".join(f'        <item quantity="{q}">{t}</item>\n' for q, t in pairs)


def test_android_plurals_a_russian_translation_with_two_forms_fails(tmp_path):
    put(tmp_path, {
        "res/values/strings.xml": ANDROID_PLURALS % _items([("one", "%d file"), ("other", "%d files")]),
        "res/values-ru/strings.xml": ANDROID_PLURALS % _items([("one", "%d файл"), ("other", "%d файла")])})
    code, out = run(tmp_path / "res/values/strings.xml", "ru")
    assert code == 1, out
    assert "few" in out and "many" in out, out


def test_android_plurals_a_complete_russian_translation_passes(tmp_path):
    put(tmp_path, {
        "res/values/strings.xml": ANDROID_PLURALS % _items([("one", "%d file"), ("other", "%d files")]),
        "res/values-ru/strings.xml": ANDROID_PLURALS % _items(
            [("one", "%d файл"), ("few", "%d файла"), ("many", "%d файлов"), ("other", "%d файла")])})
    code, out = run(tmp_path / "res/values/strings.xml", "ru")
    assert code == 0, out


def test_android_plurals_japanese_has_only_other_and_that_is_complete(tmp_path):
    put(tmp_path, {
        "res/values/strings.xml": ANDROID_PLURALS % _items([("one", "%d file"), ("other", "%d files")]),
        "res/values-ja/strings.xml": ANDROID_PLURALS % _items([("other", "%d ファイル")])})
    code, out = run(tmp_path / "res/values/strings.xml", "ja")
    assert code == 0, out


def test_android_plurals_a_lost_count_in_a_form_fails(tmp_path):
    put(tmp_path, {
        "res/values/strings.xml": ANDROID_PLURALS % _items([("one", "%d file"), ("other", "%d files")]),
        "res/values-de/strings.xml": ANDROID_PLURALS % _items([("one", "%d Datei"), ("other", "Dateien")])})
    code, out = run(tmp_path / "res/values/strings.xml", "de")
    assert code == 1, out


PO_PLURAL_SRC = ('msgid ""\nmsgstr ""\n"Language: en\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
                 'msgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] ""\nmsgstr[1] ""\n')


def po_ru(nplurals, forms):
    head = ('msgid ""\nmsgstr ""\n"Language: ru\\n"\n'
            f'"Plural-Forms: nplurals={nplurals}; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && '
            '(n%100<10 || n%100>=20) ? 1 : 2);\\n"\n\n')
    body = 'msgid "%d file"\nmsgid_plural "%d files"\n' + "".join(
        f'msgstr[{i}] "{t}"\n' for i, t in enumerate(forms))
    return head + body


def test_po_plurals_three_russian_forms_pass(tmp_path):
    put(tmp_path, {"en.po": PO_PLURAL_SRC,
                   "ru.po": po_ru(3, ["%d файл", "%d файла", "%d файлов"])})
    code, out = run(tmp_path / "en.po", "ru")
    assert code == 0, out


def test_po_plurals_a_header_that_declares_two_forms_for_russian_fails(tmp_path):
    put(tmp_path, {"en.po": PO_PLURAL_SRC, "ru.po": po_ru(2, ["%d файл", "%d файла"])})
    code, out = run(tmp_path / "en.po", "ru")
    assert code == 1, out
    assert "nplurals" in out, out


def test_po_plurals_an_entry_with_two_of_three_forms_fails(tmp_path):
    put(tmp_path, {"en.po": PO_PLURAL_SRC, "ru.po": po_ru(3, ["%d файл", "%d файла"])})
    code, out = run(tmp_path / "en.po", "ru")
    assert code == 1, out


def test_po_plurals_a_lost_count_in_the_third_form_fails(tmp_path):
    put(tmp_path, {"en.po": PO_PLURAL_SRC,
                   "ru.po": po_ru(3, ["%d файл", "%d файла", "файлов"])})
    code, out = run(tmp_path / "en.po", "ru")
    assert code == 1, out
    assert "%d" in out, out


FTL_PLURAL_EN = "emails = { $n ->\n    [one] { $n } email\n   *[other] { $n } emails\n  }\n"


def test_fluent_plurals_a_russian_selector_without_few_and_many_fails(tmp_path):
    put(tmp_path, {"en.ftl": FTL_PLURAL_EN,
                   "ru.ftl": "emails = { $n ->\n    [one] { $n } письмо\n   *[other] { $n } письма\n  }\n"})
    code, out = run(tmp_path / "en.ftl", "ru")
    assert code == 1, out
    assert "few" in out and "many" in out, out


def test_fluent_plurals_a_complete_russian_selector_passes(tmp_path):
    put(tmp_path, {"en.ftl": FTL_PLURAL_EN,
                   "ru.ftl": ("emails = { $n ->\n    [one] { $n } письмо\n    [few] { $n } письма\n"
                              "    [many] { $n } писем\n   *[other] { $n } письма\n  }\n")})
    code, out = run(tmp_path / "en.ftl", "ru")
    assert code == 0, out


def test_xcstrings_plurals_a_russian_plural_with_two_forms_fails(tmp_path):
    plural = lambda one, other: {"variations": {"plural": {"one": _u(one), "other": _u(other)}}}
    put(tmp_path, {"Localizable.xcstrings": _catalog({
        "files": {"localizations": {"en": plural("%lld file", "%lld files"),
                                    "ru": plural("%lld файл", "%lld файла")}}})})
    code, out = run(tmp_path / "Localizable.xcstrings", "ru")
    assert code == 1, out
    assert "few" in out and "many" in out, out


# ── what is read from where ───────────────────────────────────────────────────

def test_an_unreadable_target_is_a_clear_error_not_a_pass(tmp_path):
    put(tmp_path, {"en.xliff": FORMATS["xliff"][1]["en.xliff"], "de.xliff": "<xliff><oops"})
    code, out = run(tmp_path / "en.xliff")
    assert code == 2, out
    assert "de" in out


def test_a_yaml_feature_the_reader_does_not_cover_is_named_not_guessed(tmp_path):
    put(tmp_path, {"en.yml": "en:\n  a: &x Hello\n  b: *x\n", "de.yml": "de:\n  a: Hallo\n  b: Hallo\n"})
    code, out = run(tmp_path / "en.yml")
    assert code == 2, out
    assert "anchor" in out.lower() or "alias" in out.lower(), out


def test_a_format_that_is_not_supported_says_which_are(tmp_path):
    p = tmp_path / "en.stringsdict"
    p.write_text("<plist/>", encoding="utf-8")
    code, out = run(p)
    assert code == 2, out
    assert "stringsdict" in out
