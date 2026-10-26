"""The parity corpus for the npm port of `kaeris check`, and the answers the Python original gives.

    py -3 npm/test/gen_baselines.py --write    # rebuild npm/test/corpus and npm/test/baseline
    py -3 npm/test/gen_baselines.py --check    # exit 1 if the committed files differ (CI runs this
                                               # through tests/test_npm_baselines_are_current.py)

Two corpora, both answered by the Python code in THIS checkout (never an installed copy):

  corpus/<case>/    a project directory plus `case.json` {"args": [...]}; `baseline/<case>.json`
                    holds, for the same command with --json and without it, the exit code, the
                    JSON report (or the human report on stderr) and any SARIF file written.
  pairs.json        (source, translation, language, glossary) strings, written by hand and by a
                    seeded generator, with what string_faults answers for each, and files of
                    those strings with what file_faults answers.

npm/test/parity.test.js replays both against the JavaScript port.
"""
import contextlib
import importlib.util
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from kaeris import cli, detectors as det  # noqa: E402

assert os.path.dirname(os.path.dirname(cli.__file__)) == ROOT, "not the checkout's kaeris: " + cli.__file__

CORPUS = os.path.join(HERE, "corpus")
BASELINE = os.path.join(HERE, "baseline")
PAIRS = os.path.join(HERE, "pairs.json")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# the fixtures the Python suite already keeps for every format
EVERY = _load("every_format_fixtures", os.path.join(ROOT, "tests", "test_check_every_format.py"))

CASES = {}   # name -> {"files": {path: str|bytes}, "args": [...]}


def case(name, files, args):
    assert name not in CASES, name
    CASES[name] = {"files": files, "args": args}


def J(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ── A. every format: a good translation, a broken placeholder, an untranslated key ──────────────
for fname, (source, good, bad, missing, _word) in sorted(EVERY.FORMATS.items()):
    case(f"{fname}-good", dict(good), ["--source", source, "--langs", "de"])
    case(f"{fname}-placeholder", {**good, **bad}, ["--source", source, "--langs", "de"])
    miss = {k: v for k, v in good.items() if k not in missing}
    miss.update(missing)
    case(f"{fname}-missing", miss, ["--source", source, "--langs", "de"])

# ── B. native plurals (fixtures from the same suite) ─────────────────────────────────────────────
AP, items = EVERY.ANDROID_PLURALS, EVERY._items
ANDROID_EN = AP % items([("one", "%d file"), ("other", "%d files")])
case("android-plural-ru-two-forms", {"res/values/strings.xml": ANDROID_EN,
     "res/values-ru/strings.xml": AP % items([("one", "%d файл"), ("other", "%d файла")])},
     ["--source", "res/values/strings.xml", "--langs", "ru"])
case("android-plural-ru-complete", {"res/values/strings.xml": ANDROID_EN,
     "res/values-ru/strings.xml": AP % items([("one", "%d файл"), ("few", "%d файла"), ("many", "%d файлов"), ("other", "%d файла")])},
     ["--source", "res/values/strings.xml", "--langs", "ru"])
case("android-plural-ja-other-only", {"res/values/strings.xml": ANDROID_EN,
     "res/values-ja/strings.xml": AP % items([("other", "%d ファイル")])},
     ["--source", "res/values/strings.xml", "--langs", "ja"])
case("android-plural-singular-without-number", {"res/values/strings.xml": ANDROID_EN,
     "res/values-de/strings.xml": AP % items([("one", "Eine Datei"), ("other", "%d Dateien")])},
     ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-plural-lost-second-placeholder", {
     "res/values/strings.xml": AP % items([("one", "%1$s sent %2$d file"), ("other", "%1$s sent %2$d files")]),
     "res/values-de/strings.xml": AP % items([("one", "%1$s sendete %2$d Datei"), ("other", "%1$s sendete Dateien")])},
     ["--source", "res/values/strings.xml", "--langs", "de"])
PO_SRC = EVERY.PO_PLURAL_SRC
case("po-plural-ru-three", {"en.po": PO_SRC, "ru.po": EVERY.po_ru(3, ["%d файл", "%d файла", "%d файлов"])},
     ["--source", "en.po", "--langs", "ru"])
case("po-plural-ru-header-two", {"en.po": PO_SRC, "ru.po": EVERY.po_ru(2, ["%d файл", "%d файла"])},
     ["--source", "en.po", "--langs", "ru"])
case("po-plural-ru-entry-two-of-three", {"en.po": PO_SRC, "ru.po": EVERY.po_ru(3, ["%d файл", "%d файла"])},
     ["--source", "en.po", "--langs", "ru"])
case("po-plural-ru-lost-count-third", {"en.po": PO_SRC, "ru.po": EVERY.po_ru(3, ["%d файл", "%d файла", "файлов"])},
     ["--source", "en.po", "--langs", "ru"])
PO_DE = ('msgid ""\nmsgstr ""\n"Language: de\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
         'msgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] "Eine Datei"\nmsgstr[1] "{}"\n')
case("po-plural-singular-without-number", {"en.po": PO_SRC, "de.po": PO_DE.format("%d Dateien")},
     ["--source", "en.po", "--langs", "de"])
case("po-plural-lost-count", {"en.po": PO_SRC, "de.po": PO_DE.format("Dateien")},
     ["--source", "en.po", "--langs", "de"])
case("ftl-plural-ru-incomplete", {"en.ftl": EVERY.FTL_PLURAL_EN,
     "ru.ftl": "emails = { $n ->\n    [one] { $n } письмо\n   *[other] { $n } письма\n  }\n"},
     ["--source", "en.ftl", "--langs", "ru"])
case("ftl-plural-ru-complete", {"en.ftl": EVERY.FTL_PLURAL_EN,
     "ru.ftl": ("emails = { $n ->\n    [one] { $n } письмо\n    [few] { $n } письма\n"
                "    [many] { $n } писем\n   *[other] { $n } письма\n  }\n")},
     ["--source", "en.ftl", "--langs", "ru"])


def _u(v):
    return {"stringUnit": {"state": "translated", "value": v}}


def catalog(strings, lang="en"):
    return json.dumps({"sourceLanguage": lang, "version": "1.0", "strings": strings}, ensure_ascii=False)


def plural(one, other, few=None, many=None):
    arms = {"one": _u(one)}
    if few:
        arms["few"] = _u(few)
    if many:
        arms["many"] = _u(many)
    arms["other"] = _u(other)
    return {"variations": {"plural": arms}}


case("xcstrings-plural-ru-two-forms", {"L.xcstrings": catalog({"files": {"localizations": {
     "en": plural("%lld file", "%lld files"), "ru": plural("%lld файл", "%lld файла")}}})},
     ["--source", "L.xcstrings", "--langs", "ru"])
case("xcstrings-rich", {"L.xcstrings": catalog({
     "greeting": {"localizations": {"en": _u("Hello, %@"), "de": _u("Hallo, %@"), "fr": _u("Salut")}},
     "files": {"localizations": {"en": plural("%lld file", "%lld files"),
                                 "de": plural("%lld Datei", "%lld Dateien"),
                                 "fr": plural("%lld fichier", "%lld fichiers")}},
     "device": {"localizations": {"en": {"variations": {"device": {"iphone": _u("Tap here"), "mac": _u("Click here")}}},
                                  "de": {"variations": {"device": {"iphone": _u("Tippe hier"), "mac": _u("Klicke hier")}}}}},
     "bare key": {},
     "skip me": {"shouldTranslate": False, "localizations": {"en": _u("Keep")}},
     "sub": {"localizations": {"en": {"stringUnit": {"value": "%#@n@"}, "substitutions": {
         "n": {"variations": {"plural": {"one": _u("%arg item"), "other": _u("%arg items")}}}}}}},
     })}, ["--source", "L.xcstrings", "--langs", "de,fr,es"])
case("xcstrings-not-a-catalog", {"L.xcstrings": '{"hello": "world"}'}, ["--source", "L.xcstrings", "--langs", "de"])
case("xcstrings-source-lang-config", {
     "L.xcstrings": catalog({"a": {"localizations": {"de": _u("Hallo {n}"), "en": _u("Hello {n}")}}}, lang="de"),
     "kaeris.json": J({"source_lang": "en", "langs": ["de"]})}, ["--source", "L.xcstrings"])

# ── C. JSON and ARB ─────────────────────────────────────────────────────────────────────────────
JSON_EN = {
    "app": {"title": "My app", "greeting": "Hello, {name}!", "items": "You have {count, plural, one {# item} other {# items}}"},
    "buttons": ["Save", "Cancel", "Delete {n} files"],
    "price": "Only $9.99 today", "percent": "100% sure", "dollar": "Total: ${total}", "rails": "Hi %{user}",
    "printf": "%1$s sent %2$d messages", "ios": "Open %@", "vue": "Welcome {{ user.name }}", "ref": "See $t(app.title)",
    "version": "Version 2.4.1", "tag": "Click <b>here</b> or <a href=\"/x\">there</a>", "flag": True, "count": 42,
    "empty": "", "nothing": None, "html": "A &amp; B", "multi": "Line one\nLine two",
    "_service": "internal", "emph": "This is *important* now",
}
JSON_DE = {
    "app": {"title": "Meine App", "greeting": "Hallo, {name}!", "items": "Sie haben {count, plural, one {# Element} other {# Elemente}}"},
    "buttons": ["Speichern", "Abbrechen", "Lösche {n} Dateien"],
    "price": "Nur 9,99 $ heute", "percent": "100% sicher", "dollar": "Summe: ${total}", "rails": "Hallo %{user}",
    "printf": "%1$s sendete %2$d Nachrichten", "ios": "Öffne %@", "vue": "Willkommen {{ user.name }}", "ref": "Siehe $t(app.title)",
    "version": "Version 2.4.1", "tag": "Klicke <b>hier</b> oder <a href=\"/x\">dort</a>", "flag": True, "count": 42,
    "empty": "", "nothing": None, "html": "A &amp; B", "multi": "Zeile eins\nZeile zwei",
    "_service": "intern", "emph": "Das ist *wichtig* jetzt",
}
case("json-rich-good", {"en.json": J(JSON_EN), "de.json": J(JSON_DE)}, ["--source", "en.json", "--langs", "de"])
broken = json.loads(json.dumps(JSON_DE))
broken["app"]["greeting"] = "Hallo, {nom}!"
broken["app"]["items"] = "Sie haben {count, plural, one {# Element}}"
broken["printf"] = "%1$s sendete %2$s Nachrichten"
broken["dollar"] = "Summe: {total}"
broken["vue"] = "Willkommen {user.name}"
broken["ref"] = "Siehe Titel"
broken["version"] = "Version 2.4.2"
broken["price"] = "Nur 9,99 € heute"
broken["tag"] = "Klicke hier oder <a>dort</a>"
broken["html"] = "A &amp;amp; B"
broken["multi"] = "Zeile eins Zeile zwei"
broken["emph"] = "Das ist *wichtig jetzt"
broken["buttons"] = ["Speichern", "Abbrechen"]
del broken["percent"]
broken["stale"] = "alt"
case("json-rich-broken", {"en.json": J(JSON_EN), "de.json": J(broken)}, ["--source", "en.json", "--langs", "de"])
case("json-rich-broken-strict", {"en.json": J(JSON_EN), "de.json": J(broken)}, ["--source", "en.json", "--langs", "de", "--strict"])
case("json-rich-russian-icu", {"en.json": J({"n": "{count, plural, =0 {No files} one {# file} other {# files}}", "s": "{g, select, male {He} female {She} other {They}} came"}),
     "ru.json": J({"n": "{count, plural, one {# файл} other {# файла}}", "s": "{g, select, male {Он} other {Они}} пришёл"})},
     ["--source", "en.json", "--langs", "ru"])
case("json-clean-with-extra-strict", {"en.json": J({"a": "One"}), "de.json": J({"a": "Eins", "b": "Zwei"})},
     ["--source", "en.json", "--langs", "de", "--strict", "--ci"])
case("json-two-langs-one-missing-file", {"en.json": J({"a": "Hello world"}), "de.json": J({"a": "Hallo Welt"})},
     ["--source", "en.json", "--langs", "de,fr"])
case("json-lang-codes-any-case", {"en.json": J({"a": "Hello world"}), "pt-BR.json": J({"a": "Olá mundo"}), "zh-Hant.json": J({"a": "你好世界"})},
     ["--source", "en.json", "--langs", "pt_br, ZH-hant,pt-BR"])
case("json-glossary-flag", {"en.json": J({"a": "Welcome to KAERIS Cloud", "b": "Use GitHub to log in", "c": "Pro plan", "d": "Product tour"}),
     "de.json": J({"a": "Willkommen bei Wolke", "b": "Melden Sie sich mit github an", "c": "Pro-Tarif", "d": "Produkttour"})},
     ["--source", "en.json", "--langs", "de", "--glossary", "KAERIS,GitHub,Pro"])
case("json-glossary-collapse", {"en.json": J({"q": "How do I get started with KAERIS today?"}), "de.json": J({"q": "KAERIS"})},
     ["--source", "en.json", "--langs", "de", "--keep", "KAERIS"])
case("json-config-driven", {"locales/en.json": J({"a": "Hello {name}", "b": "KAERIS rocks"}),
     "locales/de.json": J({"a": "Hallo {name}", "b": "Es rockt"}), "locales/fr.json": J({"a": "Bonjour", "b": "KAERIS déchire"}),
     "kaeris.json": J({"//": "config", "_note": "x", "source": "locales/en.json", "langs": ["de", "fr"], "keep": ["KAERIS"]})}, [])
case("json-config-out-dir", {"src/en.json": J({"a": "Hello"}), "i18n/de.json": J({"a": "Hallo"}),
     "kaeris.json": J({"source": "src/en.json", "langs": "de", "out": "i18n"})}, [])
case("json-namespace-layout", {"locales/en/common.json": J({"a": "Hello {n}"}), "locales/de/common.json": J({"a": "Hallo {n}"}),
     "locales/fr/common.json": J({"a": "Bonjour"})}, ["--source", "locales/en/common.json", "--langs", "de,fr"])
case("json-prefixed-layout", {"messages_en.json": J({"a": "Hello"}), "messages_de.json": J({"a": "Hallo"})},
     ["--source", "messages_en.json", "--langs", "de"])
case("json-unknown-layout-nested", {"strings.json": J({"a": "Hello"}), "de/strings.json": J({"a": "Hallo"})},
     ["--source", "strings.json", "--langs", "de"])
case("json-pattern", {"en.json": J({"a": "Hello"}), "x/de-DE.json": J({"a": "Hallo"})},
     ["--source", "en.json", "--langs", "de", "--pattern", "x/de-DE.json"])
case("json-out-flag", {"en.json": J({"a": "Hello"}), "out/de.json": J({"a": "Hallo"})},
     ["--source", "en.json", "--langs", "de", "--out", "out"])
case("json-glob-source", {"src/en.json": J({"a": "Hello"}), "src/de.json": J({"a": "Hallo"})},
     ["--source", "src/e*.json", "--langs", "de"])
case("json-bom-utf8", {"en.json": b"\xef\xbb\xbf" + J({"a": "Hello {n}"}).encode("utf-8"),
     "de.json": b"\xef\xbb\xbf" + J({"a": "Hallo {n}"}).encode("utf-8")}, ["--source", "en.json", "--langs", "de"])
case("json-utf16", {"en.json": J({"a": "Hello {n}"}).encode("utf-16"), "de.json": J({"a": "Hallo {n}"}).encode("utf-16-le")},
     ["--source", "en.json", "--langs", "de"])
case("json-not-utf8", {"en.json": b'{"a": "caf\xe9"}'}, ["--source", "en.json", "--langs", "de"])
case("json-target-not-utf8", {"en.json": J({"a": "Hi"}), "de.json": b'{"a": "caf\xe9"}'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source", {"en.json": '{"a": "Hello",}'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source-2", {"en.json": '{"a": "Hello" "b": 1}'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source-3", {"en.json": '{"a": \'x\'}'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source-4", {"en.json": '{"a": "x"} trailing'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source-5", {"en.json": '{"a": "x\ny"}'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source-6", {"en.json": '{"a": "x\\q"}'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-source-7", {"en.json": ''}, ["--source", "en.json", "--langs", "de"])
case("json-source-is-array", {"en.json": '["a", "b"]'}, ["--source", "en.json", "--langs", "de"])
case("json-target-is-array", {"en.json": J({"a": "x"}), "de.json": '["a"]'}, ["--source", "en.json", "--langs", "de"])
case("json-broken-target", {"en.json": J({"a": "x"}), "de.json": '{"a": '}, ["--source", "en.json", "--langs", "de"])
case("json-no-source-given", {}, ["--langs", "de"])
case("json-no-langs", {"en.json": J({"a": "x"})}, ["--source", "en.json"])
case("json-source-missing", {}, ["--source", "nope.json", "--langs", "de"])
case("json-unsupported-extension", {"en.txt": "hello"}, ["--source", "en.txt", "--langs", "de"])
case("json-numeric-keys-order", {"en.json": '{"10": "ten", "2": "two", "a": "x"}', "de.json": '{"2": "zwei", "a": "x"}'},
     ["--source", "en.json", "--langs", "de"])
case("json-nonstring-values", {"en.json": J({"a": 1, "b": [1, 2, {"c": "x {n}"}], "c": {"d": None, "e": False}}),
     "de.json": J({"a": "eins", "b": [1, "zwei", {"c": "y"}], "c": {"d": 3.5}})}, ["--source", "en.json", "--langs", "de"])
case("json-deep-nesting", {"en.json": '{"a":' * 60 + '"x"' + '}' * 60, "de.json": J({"a": "y"})}, ["--source", "en.json", "--langs", "de"])
case("json-warnings-only", {"en.json": J({"save": "Save", "cancel": "Cancel", "title": "Delete this route? This cannot be undone.",
     "ok": "OK", "label": "Page not found", "go": "Go", "x": "Search results"}),
     "de.json": J({"save": "Speichern", "cancel": "Abbrechen", "title": "Diese Route löschen? Das kann nicht rückgängig gemacht werden. Wirklich.",
     "ok": "OK", "label": "Die Seite wurde leider nicht gefunden", "go": "Weiterführende Informationen", "x": "Search results"})},
     ["--source", "en.json", "--langs", "de"])
case("json-warnings-only-strict", {"en.json": J({"save": "Save", "go": "Go"}), "de.json": J({"save": "Speichern", "go": "Weiterführende Informationen"})},
     ["--source", "en.json", "--langs", "de", "--strict"])
case("json-register-mix", {"en.json": J({"a": "You can do it", "b": "Please log in", "c": "Thank you", "d": "Your account"}),
     "de.json": J({"a": "Du kannst es tun", "b": "Bitte melde dich an, du", "c": "Danke Ihnen", "d": "Ihr Konto, Sie"})},
     ["--source", "en.json", "--langs", "de"])
case("json-consistency", {"en.json": J({"a": "Cancel", "b": "Cancel", "c": "Save", "d": "save"}),
     "ru.json": J({"a": "Отмена", "b": "Отменить", "c": "Сохранить", "d": "Сохранить"})}, ["--source", "en.json", "--langs", "ru"])
case("json-untranslated-ru", {"en.json": J({"a": "Save changes to the file", "b": "GitHub Actions", "c": "How do I get my API key?", "d": "Hello"}),
     "ru.json": J({"a": "Save changes to the file", "b": "GitHub Actions", "c": "How do I get my API key?", "d": "Привет"})},
     ["--source", "en.json", "--langs", "ru"])
case("json-untranslated-same-script", {"en.json": J({"a": "Save changes to the file", "b": "Menu", "c": "Sign in to continue please"}),
     "de.json": J({"a": "Save changes to the file", "b": "Menu", "c": "Sign in to continue please"})},
     ["--source", "en.json", "--langs", "de"])
case("json-numbers-and-currency", {"en.json": J({"a": "Only $79 once", "b": "15k users", "c": "at least one language", "d": "Delete 5 files",
     "e": "Ninety seconds", "f": "Costs 1,000 USD", "g": "an hour"}),
     "ja.json": J({"a": "たった79ドル", "b": "1万5千人", "c": "少なくとも1つの言語", "d": "5個のファイルを削除", "e": "90秒", "f": "1.000 USD", "g": "1時間"}),
     "pt.json": J({"a": "Apenas R$79", "b": "15 mil usuários", "c": "pelo menos uma língua", "d": "Excluir 50 arquivos", "e": "Noventa segundos",
                   "f": "1.000 EUR", "g": "uma hora"})},
     ["--source", "en.json", "--langs", "ja,pt"])
case("json-answered-instead", {"en.json": J({"q": "How do I get my API key?"}),
     "fr.json": J({"q": "Vous pouvez obtenir votre clé API en vous inscrivant sur le site web du fournisseur de services. " * 3})},
     ["--source", "en.json", "--langs", "fr"])
case("json-script-checks", {"en.json": J({"a": "Hello world friend", "b": "Welcome back", "c": "Settings"}),
     "ar.json": J({"a": "مرحبا بالعالم", "b": "Welcome back", "c": "الإعدادات"}), "zh.json": J({"a": "你好世界", "b": "欢迎回来", "c": "Settings page"}),
     "hi.json": J({"a": "नमस्ते दुनिया", "b": "वापसी पर स्वागत है", "c": "सेटिंग्स"}), "he.json": J({"a": "שלום עולם", "b": "ברוך שובך", "c": "הגדרות"})},
     ["--source", "en.json", "--langs", "ar,zh,hi,he"])
case("arb-meta", {"app_en.arb": J({"@@locale": "en", "hello": "Hello {name}", "@hello": {"description": "x", "placeholders": {"name": {}}}, "bye": "Bye"}),
     "app_de.arb": J({"@@locale": "de", "hello": "Hallo {nom}", "@hello": {"description": "y"}, "bye": "Tschüss"}),
     "app_fr.arb": J({"@@locale": "fr", "hello": "Bonjour {name}", "extra": "x"})},
     ["--source", "app_en.arb", "--langs", "de,fr"])

# ── D. the other text formats ───────────────────────────────────────────────────────────────────
case("yaml-rich", {"en.yml": ("en:\n  title: My app  # comment\n  empty:\n  yes_value: yes\n  num: 12\n  date: 2024-02-29\n  bad_date: 2024-13-45\n"
                             "  quoted: \"Hello, %{name}\"\n  single: 'It''s {n}'\n  multi: |\n    Line one\n    Line two\n  folded: >\n    folded\n    text\n"
                             "  list:\n    - One\n    - Two {x}\n    - key: nested\n  flow: [a, 'b c', 3]\n  map: {k: v, z: 'y {q}'}\n  long: first\n    continued line\n"
                             "  dq: \"line one\n    line two\"\n  hex: 0x1F\n  inf: .inf\n  time: 12:30\n"),
     "de.yml": ("de:\n  title: Meine App\n  empty:\n  yes_value: ja\n  num: 12\n  date: 2024-02-29\n  bad_date: 2024-13-45\n"
                "  quoted: \"Hallo, %{nom}\"\n  single: 'Es ist {n}'\n  multi: |\n    Zeile eins\n    Zeile zwei\n  folded: >\n    gefalteter\n    Text\n"
                "  list:\n    - Eins\n    - Zwei\n    - key: verschachtelt\n  flow: [a, 'b c', 3]\n  map: {k: v, z: 'y'}\n  long: erste\n    weiter\n"
                "  dq: \"zeile eins\n    zeile zwei\"\n  hex: 0x1F\n  inf: .inf\n  time: 12:30\n")},
     ["--source", "en.yml", "--langs", "de"])
case("yaml-no-root", {"en.yaml": "a: Hello\nb:\n  c: Bye\n", "de.yaml": "a: Hallo\nb:\n  c: Tschüss\n"}, ["--source", "en.yaml", "--langs", "de"])
case("yaml-anchor", {"en.yml": "a: &x Hello\nb: *x\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-merge-key", {"en.yml": "base:\n  a: 1\nd:\n  <<: *base\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-two-docs", {"en.yml": "a: x\n---\nb: y\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-tab", {"en.yml": "a:\n\tb: x\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-broken", {"en.yml": "a: x\n  b: y\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-tag", {"en.yml": "a: !!str 5\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-unclosed-quote", {"en.yml": "a: \"never closes\nb: x\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-not-a-mapping", {"en.yml": "- a\n- b\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-empty-source", {"en.yml": "# nothing\n"}, ["--source", "en.yml", "--langs", "de"])
case("yaml-bad-target", {"en.yml": "a: x\n", "de.yml": "a: [unclosed\n"}, ["--source", "en.yml", "--langs", "de"])
case("strings-escapes", {"en.strings": '/* c */\n"a" = "Say \\"hi\\" %@";\n"b" = "Tab\\there\\n%d items";\n"c\\"q" = "C:\\\\path";\n',
     "de.strings": '"a" = "Sag \\"hallo\\" %@";\n"b" = "Tab\\thier\\n%d Dinge";\n"c\\"q" = "C:\\\\pfad";\n'}, ["--source", "en.strings", "--langs", "de"])
case("strings-utf16", {"en.lproj/L.strings": '"a" = "Hello %@";\n'.encode("utf-16"), "de.lproj/L.strings": '"a" = "Hallo";\n'.encode("utf-16")},
     ["--source", "en.lproj/L.strings", "--langs", "de"])
PO = ('msgid ""\nmsgstr ""\n"Language: de\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
      '#: a.c:1\nmsgid "Hello, %(name)s"\nmsgstr "Hallo, %(name)s"\n\n'
      'msgctxt "menu"\nmsgid "Open"\nmsgstr "Öffnen"\n\n'
      'msgctxt "verb"\nmsgid "Open"\nmsgstr "Aufmachen {x}"\n\n'
      'msgid "Dup"\nmsgstr "Doppelt"\n\nmsgid "Dup"\nmsgstr "Doppelt 2"\n\n'
      'msgid "Multi"\nmsgstr ""\n"Zeile eins "\n"Zeile zwei"\n\n'
      '#~ msgid "Old"\n#~ msgstr "Alt"\n')
PO_SRC_2 = ('msgid ""\nmsgstr ""\n"Language: en\\n"\n\nmsgid "Hello, %(name)s"\nmsgstr ""\n\nmsgctxt "menu"\nmsgid "Open"\nmsgstr ""\n\n'
            'msgctxt "verb"\nmsgid "Open"\nmsgstr ""\n\nmsgid "Dup"\nmsgstr ""\n\nmsgid "Dup"\nmsgstr ""\n\nmsgid "Multi"\nmsgstr ""\n\nmsgid "Untranslated"\nmsgstr ""\n')
case("po-rich", {"en.po": PO_SRC_2, "de.po": PO}, ["--source", "en.po", "--langs", "de"])
case("android-rich", {"res/values/strings.xml": (
    '<?xml version="1.0" encoding="utf-8"?>\n<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n'
    '  <string name="app">My app</string>\n  <string name="hi">Hello <xliff:g id="n" example="Bob">%1$s</xliff:g>!</string>\n'
    '  <string name="bold">Click <b>here</b> &amp; <i>there</i></string>\n  <string name="q">It\\\'s \\"quoted\\" \\n done</string>\n'
    '  <string name="cd"><![CDATA[Some <b>cdata</b> text]]></string>\n  <string name="skip" translatable="false">Skip</string>\n'
    '  <string-array name="days">\n    <item>Mon</item>\n    <item>Tue</item>\n  </string-array>\n'
    '  <plurals name="files">\n    <item quantity="one">%d file</item>\n    <item quantity="other">%d files</item>\n  </plurals>\n'
    '  <!-- comment -->\n  <string name="empty"></string>\n</resources>\n'),
    "res/values-de/strings.xml": (
    '<?xml version="1.0" encoding="utf-8"?>\n<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n'
    '  <string name="app">Meine App</string>\n  <string name="hi">Hallo <xliff:g id="n" example="Bob">%1$s</xliff:g>!</string>\n'
    '  <string name="bold">Klicken <b>hier</b> &amp; <i>dort</i></string>\n  <string name="q">Es ist \\"zitiert\\" \\n fertig</string>\n'
    '  <string name="cd"><![CDATA[Einige <b>cdata</b> Texte]]></string>\n'
    '  <string-array name="days">\n    <item>Mo</item>\n  </string-array>\n'
    '  <plurals name="files">\n    <item quantity="one">Eine Datei</item>\n    <item quantity="other">%d Dateien</item>\n  </plurals>\n'
    '  <string name="empty"></string>\n</resources>\n')}, ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-bad-ph-in-xliff-g", {"res/values/strings.xml": '<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2"><string name="hi">Hi <xliff:g id="n">%1$s</xliff:g></string></resources>',
     "res/values-de/strings.xml": '<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2"><string name="hi">Hallo <xliff:g id="n">%2$s</xliff:g></string></resources>'},
     ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-doctype", {"res/values/strings.xml": '<!DOCTYPE x [<!ENTITY e "v">]><resources><string name="a">&e;</string></resources>'},
     ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-wrong-root", {"res/values/strings.xml": '<strings><string name="a">x</string></strings>'}, ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-broken-xml", {"res/values/strings.xml": '<resources><string name="a">x</resources>'}, ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-broken-target", {"res/values/strings.xml": '<resources><string name="a">x</string></resources>',
     "res/values-de/strings.xml": '<resources><string name="a">x</string>'}, ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-source-empty", {"res/values/strings.xml": '<resources></resources>'}, ["--source", "res/values/strings.xml", "--langs", "de"])
case("android-pt-br", {"res/values/strings.xml": '<resources><string name="a">Hello %s</string></resources>',
     "res/values-pt-BR/strings.xml": '<resources><string name="a">Olá %s</string></resources>'}, ["--source", "res/values/strings.xml", "--langs", "pt-BR"])
XL = ('<?xml version="1.0"?>\n<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:x="urn:x">\n<file original="a" source-language="en" datatype="plaintext">\n<body>\n%s</body>\n</file>\n</xliff>\n')
case("xliff-rich", {"en.xlf": XL % ('<trans-unit id="a"><source>Hello <g id="1">bold</g> <x id="2"/></source></trans-unit>\n'
                                    '<trans-unit id="b"><source>Save</source></trans-unit>\n<trans-unit id="c"><source>Third {n}</source></trans-unit>\n'
                                    '<trans-unit><source>no id</source></trans-unit>\n<trans-unit id="d"><source>Fourth</source></trans-unit>\n'),
     "de.xlf": XL % ('<trans-unit id="a"><source>Hello</source><target>Hallo <g id="1">fett</g> <x id="2"/></target></trans-unit>\n'
                     '<trans-unit id="b"><source>Save</source><target state="needs-translation">Speichern</target></trans-unit>\n'
                     '<trans-unit id="c"><source>Third {n}</source><target>Dritte {m}</target></trans-unit>\n'
                     '<trans-unit id="d"><source>Fourth</source><target x:state="y">Vierte</target></trans-unit>\n')},
     ["--source", "en.xlf", "--langs", "de"])
case("xliff-v2", {"en.xliff": '<xliff version="2.0"><file/></xliff>'}, ["--source", "en.xliff", "--langs", "de"])
case("xliff-wrong-root", {"en.xliff": '<root/>'}, ["--source", "en.xliff", "--langs", "de"])
case("properties-rich", {"en.properties": ("# comment\n! other comment\ngreeting = Hello, {0}\nlong=first \\\n    second\\\n  third\nkey\\ with\\ space: value {1}\n"
                                           "empty=\nuni=\\u00e9 \\ud83d\\ude00 \\u0041\ntabbed\\tkey=v\nnoval\n  indented=ok\ncolon:Hi\n"),
     "de.properties": ("greeting = Hallo, {0}\nlong=erst \\\n    zweit\\\n  dritt\nkey\\ with\\ space: Wert {2}\nempty=\nuni=\\u00e9 \\ud83d\\ude00 \\u0041\n"
                       "tabbed\\tkey=w\nnoval\nindented=ok\ncolon:Hi\nextra=x\n")}, ["--source", "en.properties", "--langs", "de"])
case("properties-latin1", {"en.properties": b"a=caf\xe9 {0}\n", "de.properties": b"a=Caf\xe9 {0}\n"}, ["--source", "en.properties", "--langs", "de"])
case("resx-rich", {"en.resx": ('<?xml version="1.0" encoding="utf-8"?>\n<root>\n  <resheader name="r"><value>x</value></resheader>\n'
                               '  <data name="A" xml:space="preserve"><value>Hello {0}</value><comment>c</comment></data>\n'
                               '  <data name="Pic" type="System.Resources.ResXFileRef"><value>x.png</value></data>\n'
                               '  <data name="B"><value>Bye</value></data>\n  <data name="C">Direct</data>\n</root>\n'),
     "de.resx": ('<root><data name="A"><value>Hallo {1}</value></data><data name="B"><value>Tschüss</value></data></root>')},
     ["--source", "en.resx", "--langs", "de"])
FTL_EN = ("# comment\nhello = Hello, { $name }!\n-brand = KAERIS\nabout = About { -brand }\nlogin = Log in\n    .label = Login label\n"
          "    .title = Login title\nmulti =\n    first line\n    second { $x }\nemails = { $n ->\n    [one] { $n } email\n   *[other] { $n } emails\n  }\n"
          "photos = { $user } added { $n ->\n    [one] a photo\n   *[other] { $n } photos\n  } to { $album }\nbad = { $x\nnot a message\n"
          "gender = { $g ->\n    [male] He\n    [female] She\n   *[other] They\n  }\n")
FTL_DE = ("hello = Hallo, { $name }!\n-brand = KAERIS\nabout = Über { -brand }\nlogin = Anmelden\n    .label = Anmelde-Label\n    .title = Titel\n"
          "multi =\n    erste Zeile\n    zweite { $y }\nemails = { $n ->\n    [one] { $n } E-Mail\n   *[other] { $n } E-Mails\n  }\n"
          "gender = { $g ->\n    [male] Er\n   *[other] Sie\n  }\n")
case("ftl-rich", {"en.ftl": FTL_EN, "de.ftl": FTL_DE}, ["--source", "en.ftl", "--langs", "de"])
MD = ('---\ntitle: My page\nslug: my-page\ndate: 2024-01-01\ntags: [a, b]\nsummary: "A short summary"\nimage:\n  src: /a.jpg\n  caption: A caption\n---\n\n'
      '# Heading {#anchor}\n\nA paragraph with `code`, a [link](https://example.com "t") and <b>html</b>.\nSecond line of it.\n\n'
      '- Item one\n- Item two\n1. First\n2) Second\n\n> A quote\n\n| Col A | Col B |\n| --- | --- |\n| cell one | cell two |\n\n'
      '```js\nconst x = 1; // not translated\n```\n\n<div>\nblock html\n</div>\n\nimport X from "x"\n\n    indented code\n\n[ref]: https://x.y\n\n---\n\nLast &amp; line.\n')
MD_DE = MD.replace("My page", "Meine Seite").replace("Heading", "Überschrift").replace("A paragraph with", "Ein Absatz mit").replace("Item one", "Punkt eins")
case("markdown-rich", {"en.md": MD, "de.md": MD_DE}, ["--source", "en.md", "--langs", "de"])
case("markdown-block-count", {"en.md": MD, "de.md": MD_DE.replace("- Item two\n", "")}, ["--source", "en.md", "--langs", "de"])
case("markdown-mdx", {"en.mdx": "import A from 'a'\n\n# Title\n\nSome <Note>inline</Note> text.\n", "de.mdx": "import A from 'a'\n\n# Titel\n\nEtwas <Note>Zeile</Note> Text.\n"},
     ["--source", "en.mdx", "--langs", "de"])
case("markdown-empty-source", {"en.md": "```\ncode only\n```\n"}, ["--source", "en.md", "--langs", "de"])
case("markdown-toml-front", {"en.md": "+++\ntitle = \"x\"\n+++\n\n# Hello\n", "de.md": "+++\ntitle = \"x\"\n+++\n\n# Hallo\n"}, ["--source", "en.md", "--langs", "de"])
case("csv-rich", {"t.csv": ('key,en,de,fr\r\ngreeting,"Hello, {name}","Hallo, {name}",\r\nmulti,"Line one\nline two","Zeile eins\nZeile zwei","Ligne"\r\n'
                            'quote,"Say ""hi""","Sag ""hallo""",Dire\r\n\r\nformula,\'=SUM(A1),\'=SUMME(A1),\'=SUM(A1)\r\nblank,Text,,Texte\r\n,no key,x,y\r\n')},
     ["--source", "t.csv", "--langs", "de,fr,es"])
case("csv-translations-folder", {"t.csv": 'key,en\na,Hello {n}\nb,Bye\n', "translations/t.csv": 'key,en,de\na,Hello {n},Hallo {m}\nb,Bye,Tschüss\n'},
     ["--source", "t.csv", "--langs", "de"])
case("csv-too-short", {"t.csv": "key,en\n"}, ["--source", "t.csv", "--langs", "de"])
case("csv-no-source-column", {"t.csv": "key\na\n"}, ["--source", "t.csv", "--langs", "de"])
case("csv-source-lang-config", {"t.csv": "key,en,de\na,Hello,Hallo\n", "kaeris.json": J({"source_lang": "de", "langs": ["en"]})}, ["--source", "t.csv"])

# ── E. reports ──────────────────────────────────────────────────────────────────────────────────
case("sarif-report", {"en.json": J({"a": "Hello {n}", "b": "Bye", "c": "Third"}), "de.json": J({"a": "Hallo {m}", "c": "Dritte", "z": "extra"})},
     ["--source", "en.json", "--langs", "de,fr", "--sarif", "out/report.sarif"])
case("sarif-clean", {"en.json": J({"a": "Hello"}), "de.json": J({"a": "Hallo"})}, ["--source", "en.json", "--langs", "de", "--sarif", "r.sarif"])
case("config-broken", {"kaeris.json": "{nope", "en.json": J({"a": "x"})}, ["--source", "en.json", "--langs", "de"])
case("config-not-object", {"kaeris.json": "[1]", "en.json": J({"a": "x"})}, ["--source", "en.json", "--langs", "de"])
case("config-flags-win", {"en.json": J({"a": "Hello"}), "de.json": J({"a": "Hallo"}), "kaeris.json": J({"source": "nope.json", "langs": ["fr"]})},
     ["--source", "en.json", "--langs", "de"])
case("lproj-sibling", {"en.lproj/L.strings": '"a" = "Hello %@";\n', "de.lproj/L.strings": '"a" = "Hallo %@";\n'}, ["--source", "en.lproj/L.strings", "--langs", "de"])
case("pretty-keys-xcstrings", {"L.xcstrings": catalog({"cart": {"localizations": {"en": plural("%lld item", "%lld items"), "de": {}}}})},
     ["--source", "L.xcstrings", "--langs", "de"])


# ── pairs ───────────────────────────────────────────────────────────────────────────────────────
HAND = [
    ("Hello {name}", "Bonjour {name}", "fr", None), ("Hello {name}", "Bonjour {nom}", "fr", None), ("Hello {name}", "Bonjour", "fr", None),
    ("Hello {{name}}", "Hallo {{ name }}", "de", None), ("Hello %s", "Hallo %d", "de", None), ("Hello %1$s %2$d", "Hallo %2$d %1$s", "de", None),
    ("Rate 100%", "Bewerten Sie 100%", "de", None), ("Hi ${name}", "Hallo {name}", "de", None), ("See $t(a.b)", "Siehe a.b", "de", None),
    ("See @:nav.shop", "Siehe @:nav.shop", "de", None), ("Hi :name:", "Hallo :nom:", "de", None), ("Use %(count)d items", "Nutze %(count)s Dinge", "de", None),
    ("{n, plural, one {# file} other {# files}}", "{n, plural, one {# Datei} other {# Dateien}}", "de", None),
    ("{n, plural, one {# file} other {# files}}", "{n, plural, one {# файл} other {# файла}}", "ru", None),
    ("{n, plural, one {# file} other {# files}}", "{n, plural, one {# файл} few {# файла} many {# файлов} other {# файла}}", "ru", None),
    ("{n, plural, one {# file} other {# files}}", "{n, plural, one {# Datei}}", "de", None),
    ("{n, plural, =0 {none} one {# file} other {# files}}", "{n, plural, one {# Datei} other {# Dateien}}", "de", None),
    ("{n, select, a {A} other {B}}", "{n, select, a {A}}", "de", None), ("{n, plural, one {# file} other {# files}}", "Dateien", "de", None),
    ("{n, plural, one {Hi {name}} other {Hi all {name}}}", "{n, plural, one {Hallo} other {Hallo {name}}}", "de", None),
    ("{n, plural, one {Hi {name}} other {Hi all {name}}}", "{n, plural, zero {a {name}} one {b {name}} two {c {name}} few {d {name}} many {e {name}} other {f {name}}}", "ar", None),
    ("Delete 5 files", "Delete 50 files", "en", None), ("Delete 5 files", "5 Dateien löschen", "de", None), ("Delete five files", "Lösche 5 Dateien", "de", None),
    ("Price 9.99", "Preis 9,99", "de", None), ("Price 9.99", "Preis 999", "de", None), ("1,000 users", "1.000 Nutzer", "de", None), ("1,000 users", "1 000 Nutzer", "fr", None),
    ("Only $79", "Nur 79 $", "fr", None), ("Only $79", "Nur 79 €", "fr", None), ("Only $79", "Nur 79", "fr", None), ("Only $79", "Nur 79 USD", "de", None),
    ("Only 79", "Nur 79 $", "de", None), ("Only R$79", "Nur $79", "de", None), ("15k users", "15.000 Nutzer", "de", None), ("15k users", "15 Tsd. Nutzer", "de", None),
    ("15k users", "15 тыс. пользователей", "ru", None), ("15k users", "1万5千人", "ja", None), ("3 hours", "三時間", "ja", None), ("an hour", "1時間", "ja", None),
    ("an hour", "через 1 час", "ru", None), ("Ninety seconds", "90秒", "ja", None), ("two hundred testers", "200人", "ja", None), ("one", "1", "ja", None),
    ("Save &amp; exit", "Speichern &amp;amp; beenden", "de", None), ("Save", "Speichern \\u12", "de", None), ("Save \\u0041", "Speichern \\uZZ", "de", None),
    ("Click <b>here</b>", "Klicke hier", "de", None), ("Click <b>here</b>", "Klicke <b>hier</b>", "de", None), ("Line<br/>two", "Zeile zwei", "de", None),
    ("Click <0>here</0>", "Klicke <1>hier</1>", "de", None), ("a < b and c > d", "a < b und c > d", "de", None),
    ("Line one\nLine two", "Zeile eins Zeile zwei", "de", None), ("Line one\nLine two", "Zeile eins\nZeile zwei", "de", None), ("\nStart", "Start", "de", None),
    ("This is *important*", "Das ist *wichtig", "de", None), ("This is *important*", "Das ist wichtig", "de", None), ("This is *important*", "Das ist *wichtig* und *mehr*", "de", None),
    ("**Bold** and *it*", "**Fett** und *kursiv*", "de", None), ("* item", "* Punkt", "de", None),
    ("Welcome to KAERIS", "Willkommen bei Kaeris", "de", ["KAERIS"]), ("Welcome to KAERIS", "Willkommen", "de", ["KAERIS"]), ("Welcome to KAERIS", "Willkommen bei KAERIS-System", "de", ["KAERIS"]),
    ("How do I start with KAERIS", "KAERIS", "de", ["KAERIS"]), ("Pro plan", "Pro-Tarif", "de", ["Pro"]), ("Project plan", "Projektplan", "de", ["Pro"]),
    ("Save changes to the file", "Save changes to the file", "ru", None), ("Save changes to the file", "Сохранить изменения в файле", "ru", None), ("GitHub Actions", "GitHub Actions", "ru", None),
    ("How do I get my API key?", "How do I get my API key?", "ru", None), ("Menu", "Menu", "de", None), ("Sign in to continue please", "Sign in to continue please", "de", None),
    ("Hello world", "こんにちは世界", "ja", None), ("Hello world", "Hello world", "ja", None), ("Hello world", "你好世界", "zh-Hant", None), ("Hello world", "नमस्ते", "hi", None),
    ("Hello world", "Hello world friends", "ko", None), ("Hello world", "안녕하세요", "ko", None), ("Hello world", "สวัสดี", "th", None), ("Hello world", "Привіт світе", "uk", None),
    ("How do I get my API key?", "Vous pouvez obtenir votre clé API en vous inscrivant sur le site web du fournisseur de services. " * 2, "fr", None),
    ("Save", "", "de", None), ("", "x", "de", None), ("Hello", "   ", "de", None), ("Hello", "Hello", "en", None),
    ("100% sure {n}", "100% sicher {n}", "de", None), ("%%", "%", "de", None), ("%d%% off", "%d%% Rabatt", "de", None), ("%d%% off", "%d Rabatt", "de", None),
    ("{0} of {1}", "{1} von {0}", "de", None), ("{0:C} and {1,-10:N2}", "{0:C} und {1,-10:N2}", "de", None), ("{0:C}", "{0:N2}", "de", None),
    ("Hi %{user} and %<a>s", "Hallo %{user}", "de", None), ("%.2f and %05d and %-10s", "%.2f und %05d und %-10s", "de", None), ("%ld items", "%d Dinge", "de", None),
    ("Time 10:30:45 ratio 3:4:5", "Zeit 10:30:45 Verhältnis 3:4:5", "de", None), ("😀 emoji 𝒜 text {n}", "😀 Emoji 𝒜 Text {n}", "de", None),
    ("{count, plural, one {# item} other {# items}} {name}", "{count, plural, one {# Element} other {# Elemente}}", "de", None),
    ("a {b} c {d} e", "a {d} c {b} e", "de", None), ("{a}{a}", "{a}", "de", None), ("{a}", "{a}{a}", "de", None),
    ("Hello World Friend", "Hallo Welt Freund Mensch Dame Herr Sie du", "de", None),
    ("Hello Du", "Hallo Sie", "de", None), ("Ünïcode ß ſ K", "ünïcode SS s k", "de", ["ß"]),
]
WORDS = {
    "de": ["Speichern", "Abbrechen", "Datei", "Einstellungen", "Willkommen", "Sie", "du", "dein", "Ihnen", "Konto", "löschen", "Weiter"],
    "fr": ["Enregistrer", "Annuler", "Fichier", "Paramètres", "Bienvenue", "vous", "tu", "votre", "ton", "compte", "supprimer", "Suivant"],
    "ru": ["Сохранить", "Отмена", "Файл", "Настройки", "Добро пожаловать", "вы", "ты", "ваш", "твой", "аккаунт", "удалить", "Далее"],
    "ja": ["保存", "キャンセル", "ファイル", "設定", "ようこそ", "アカウント", "削除", "次へ", "千", "万", "ドル", "円"],
    "zh": ["保存", "取消", "文件", "设置", "欢迎", "账户", "删除", "下一步", "千米", "万", "美元", "元"],
    "ar": ["حفظ", "إلغاء", "ملف", "إعدادات", "مرحبا", "حساب", "حذف", "التالي", "ألف", "دولار", "يورو", "مليون"],
    "hi": ["सहेजें", "रद्द करें", "फ़ाइल", "सेटिंग्स", "स्वागत", "खाता", "हटाएं", "अगला", "हज़ार", "डॉलर", "यूरो", "लाख"],
    "es": ["Guardar", "Cancelar", "Archivo", "Ajustes", "Bienvenido", "usted", "tú", "tus", "ustedes", "cuenta", "eliminar", "Siguiente"],
    "pl": ["Zapisz", "Anuluj", "Plik", "Ustawienia", "Witamy", "konto", "usuń", "Dalej", "tys.", "mln", "zł", "dolarów"],
    "he": ["שמור", "ביטול", "קובץ", "הגדרות", "ברוכים", "חשבון", "מחק", "הבא", "אלף", "דולר", "יורו", "מיליון"],
    "ko": ["저장", "취소", "파일", "설정", "환영합니다", "계정", "삭제", "다음", "천", "만", "달러", "유로"],
    "it": ["Salva", "Annulla", "File", "Impostazioni", "Benvenuto", "Lei", "tu", "tuo", "tue", "account", "elimina", "Avanti"],
    "pt-BR": ["Salvar", "Cancelar", "Arquivo", "Configurações", "Bem-vindo", "conta", "excluir", "Próximo", "mil", "milhões", "R$", "dólares"],
    "th": ["บันทึก", "ยกเลิก", "ไฟล์", "การตั้งค่า", "ยินดีต้อนรับ", "บัญชี", "ลบ", "ถัดไป", "พัน", "ดอลลาร์", "ยูโร", "ล้าน"],
    "en": ["Save", "Cancel", "File", "Settings", "Welcome", "account", "delete", "Next", "thousand", "dollars", "euro", "million"],
}
SOURCES = [
    "Save {name}", "Hello, {name}!", "You have {count} new messages", "Total: ${amount} for %d items", "Welcome {{ user.name }}", "Open %@ now",
    "Rails %{user} says %s", "Cost: $9.99 per month", "Only 5 left", "Price 1,299.50 EUR", "Delete 3 files?", "Version 2.4.1 released", "Click <b>here</b> to continue",
    "A <a href=\"/x\">link</a> and <br/>", "Line one\nLine two", "This is *important* text", "**Bold** and *italic* words", "{count, plural, one {# file} other {# files}}",
    "{n, plural, =0 {Nothing} one {# item} other {# items}} in {place}", "{g, select, male {He} female {She} other {They}} said hi", "Use $t(nav.cart) or @:nav.shop",
    "Save", "Cancel", "OK", "Search results", "Page not found", "Welcome to KAERIS Cloud", "Sign in with GitHub today", "Rate 100% of them",
    "At least one language", "Ninety seconds left", "Two hundred testers needed", "15k users and 2M views", "Wait a second please", "an hour ago",
    "Save & exit &amp; leave", "Total {0} of {1:N2}", "Time 10:30:45 ratio 3:4:5", "Emoji 😀 and 𝒜 text", "What is your name, dear friend today?",
    "Settings", "Delete this route? This cannot be undone.", "How do I get my API key?",
]


def mutate(rng, src, lang):
    w = WORDS.get(lang.split("-")[0], WORDS.get(lang, WORDS["en"]))
    kind = rng.randrange(14)
    out = src
    if kind == 0:
        return src                                                             # untouched
    if kind in (1, 2):                                                          # translate prose, keep placeholders
        return re.sub(r"[A-Za-z]{3,}(?![^{}]*\})", lambda m: rng.choice(w), src)
    if kind == 3:                                                               # drop a placeholder
        return re.sub(r"\{[^{}]*\}|%[sd@]|%\d\$[sd]", "", src, count=1) or rng.choice(w)
    if kind == 4:                                                               # rename braces
        return re.sub(r"\{(\w+)\}", lambda m: "{" + m.group(1) + "x}", src)
    if kind == 5:                                                               # change a number
        return re.sub(r"\d+", lambda m: str(int(m.group(0)) + rng.choice([1, 9, 10])), src, count=1)
    if kind == 6:                                                               # swap currency
        return src.replace("$", "€").replace("EUR", "USD")
    if kind == 7:                                                               # strip tags
        return re.sub(r"</?[a-z]+[^>]*>", "", src) or rng.choice(w)
    if kind == 8:                                                               # joined lines / broken emphasis
        return src.replace("\n", " ").replace("*italic*", "*italic").replace("*important*", "important")
    if kind == 9:
        return " ".join(rng.choice(w) for _ in range(rng.randrange(1, 12)))     # all new words
    if kind == 10:
        return src.upper()
    if kind == 11:
        return ""
    if kind == 12:                                                              # long expansion
        return (src + " ") * rng.randrange(2, 5)
    return src.replace("&amp;", "&amp;amp;").replace("{", "{{", 1)


TOKENS = ["{", "}", "{name}", "{{x}}", "%s", "%d", "%1$s", "%@", "%%", "${v}", "%{r}", "$t(a.b)", "@:k.s", ":sym:", "{0}", "{0:N2}",
          "{n, plural, one {# a} other {# b}}", "{g, select, a {A} other {B}}", "<b>", "</b>", "<br/>", "<a href=\"x\">", "*", "**", "\n", " ",
          " ", " ", "$", "€", "£", "R$", "USD", "EUR", "ドル", "доллар", "0", "1", "2", "5", "10", "9.99", "1,000", "1.000", "15k", "2M",
          "万", "千", "mln", "tys.", "one", "two", "ten", "hundred", "a", "an", "and", "KAERIS", "Pro", "github", "Hello", "world", "Save",
          "Speichern", "Привет", "你好", "こんにちは", "नमस्ते", "مرحبا", "שלום", "한국어", "ไทย", "ß", "😀", "𝒜", "é", "&amp;", "&amp;amp;",
          "\\u0041", "\\u", ".", ",", ":", "!", "…", "。", "-", "_", "'", "\"", "(", ")", "=", "/", "\\", "‌", "́"]
LABELS = ["Cancel", "Save", "OK", "Yes", "Go", "Play", "Search", "Search results", "Page not found", "Sign in", "Delete",
          "Delete this route? This cannot be undone.", "Your account has been created. Please check your email.", "Loading...",
          "{count, plural, one {# file} other {# files}} found", "Hello {name}", "Line one\nLine two\nLine three", "You can do it",
          "Thank you very much", "Please log in", "Your profile", "Do you want to continue?"]
TRANSLATED = {
    "de": ["Abbrechen", "Speichern", "Abbruch", "Du kannst es", "Bitte melde dich an", "Sie können es", "Ihre Daten", "dein Konto", "Ihnen", "Seite nicht gefunden aber leider verschwunden"],
    "fr": ["Annuler", "Enregistrer", "Tu peux", "Vous pouvez", "votre compte", "ton compte", "Merci beaucoup pour tout", "vos données", "tes données"],
    "ru": ["Отмена", "Отменить", "Сохранить", "Вы можете", "Ты можешь", "ваш аккаунт", "твой аккаунт", "Нет результатов поиска"],
    "es": ["Cancelar", "Guardar", "usted puede", "tú puedes", "ustedes", "tus datos"],
    "it": ["Annulla", "Salva", "Lei può", "tu puoi", "tuo account", "Lei, grazie"],
    "ja": ["キャンセル", "保存", "再生", "検索結果", "ページが見つかりません", "このルートを削除しますか？この操作は元に戻せません。"],
    "fi": ["Peruuta", "Tallenna", "Hakutulokset eivät löytyneet mistään tällä hetkellä oikeasti"],
}


def random_pairs(rng, n):
    langs = ["de", "fr", "ru", "ja", "zh", "zh-Hant", "ar", "hi", "bn", "pl", "he", "ko", "es", "it", "th", "uk", "pt-BR", "en", "cs", "lv", "sl", "ro", "fa", "tr"]
    out = []
    for _ in range(n):
        src = "".join(rng.choice(TOKENS) for _ in range(rng.randrange(0, 14)))
        t = list(src)
        for _ in range(rng.randrange(0, 4)):
            if t and rng.random() < .4:
                del t[rng.randrange(len(t))]
            else:
                t.insert(rng.randrange(len(t) + 1), rng.choice(TOKENS))
        tgt = "".join(t) if rng.random() < .7 else "".join(rng.choice(TOKENS) for _ in range(rng.randrange(0, 14)))
        out.append({"src": src, "tgt": tgt, "lang": rng.choice(langs), "glossary": rng.choice([None, None, ["KAERIS"], ["Pro", "github", "ß"]])})
    return out


def build_pairs():
    rng = random.Random(20261026)
    pairs = [{"src": s, "tgt": t, "lang": l, "glossary": g} for s, t, l, g in HAND]
    langs = sorted(WORDS)
    for _ in range(900):
        lang = rng.choice(langs)
        src = rng.choice(SOURCES)
        glossary = rng.choice([None, None, None, ["KAERIS"], ["GitHub", "Pro"]])
        pairs.append({"src": src, "tgt": mutate(rng, src, lang), "lang": lang, "glossary": glossary})
    pairs += random_pairs(random.Random(99), 500)
    for p in pairs:
        p["expect"] = det.string_faults(p["src"], p["tgt"], p["lang"], p["glossary"])
    return pairs


def build_files():
    rng = random.Random(7)
    files = []
    for n in range(80):
        lang = rng.choice(sorted(WORDS))
        src, tgt = {}, {}
        for i in range(rng.randrange(4, 14)):
            s = rng.choice(SOURCES + ["Cancel", "Cancel", "Save", "Save", "Yes", "Go"])
            src[f"k{i}"] = s
            tgt[f"k{i}"] = mutate(rng, s, lang) if rng.random() < 0.8 else rng.choice(WORDS.get(lang.split("-")[0], WORDS["en"]))
        files.append({"lang": lang, "src": src, "tgt": tgt, "expect": det.file_faults(src, tgt, lang)})
    # labels with the words that make the file-level checks speak: register, expansion, repeats
    rng = random.Random(11)
    for _ in range(120):
        lang = rng.choice(sorted(TRANSLATED))
        src, tgt = {}, {}
        for i in range(rng.randrange(6, 22)):
            s = rng.choice(LABELS)
            src[f"k{i}"] = s
            r = rng.random()
            if r < .5:
                t = rng.choice(TRANSLATED[lang])
            elif r < .75:
                t = " ".join(rng.choice(TRANSLATED[lang]) for _ in range(rng.randrange(1, 5)))
            elif r < .9:
                t = (rng.choice(TRANSLATED[lang]) + " ") * rng.randrange(2, 6)
            else:
                t = s
            tgt[f"k{i}"] = t
        files.append({"lang": lang, "src": src, "tgt": tgt, "expect": det.file_faults(src, tgt, lang)})
    return files


# ── running the Python original ─────────────────────────────────────────────────────────────────

def run_check(directory, args):
    """(exit code, stdout, stderr) of `kaeris check ARGS` run in `directory`."""
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    os.environ.update({"NO_COLOR": "1", "KAERIS_NO_UPDATE_CHECK": "1", "CI": "1"})
    out, err = io.StringIO(), io.StringIO()
    try:
        os.chdir(directory)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["check", *args])
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    return code, out.getvalue(), err.getvalue()


def materialize(root, files):
    for rel, content in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))


def norm(text):
    return text.replace("\\", "/")


def baseline_for(name, spec):
    with tempfile.TemporaryDirectory() as tmp:
        materialize(tmp, spec["files"])
        code_j, out_j, err_j = run_check(tmp, spec["args"] + ["--json"])
        try:
            body = json.loads(out_j) if out_j.strip() else None
        except ValueError:
            body = {"unparsed": out_j}
        sarif = None
        for i, a in enumerate(spec["args"]):
            if a == "--sarif":
                path = os.path.join(tmp, spec["args"][i + 1])
                sarif = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
        shutil.rmtree(tmp, ignore_errors=True)
    with tempfile.TemporaryDirectory() as tmp:
        materialize(tmp, spec["files"])
        code_h, out_h, err_h = run_check(tmp, spec["args"])
    return {"json": {"code": code_j, "stdout": body, "stderr": norm(err_j)},
            "human": {"code": code_h, "stdout": out_h, "stderr": norm(err_h)}, "sarif": sarif}


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def desired():
    """{relative path: bytes} of everything this script owns."""
    want = {}
    for name, spec in sorted(CASES.items()):
        for rel, content in spec["files"].items():
            want[f"corpus/{name}/{rel}"] = content if isinstance(content, bytes) else content.encode("utf-8")
        want[f"corpus/{name}/case.json"] = dump({"args": spec["args"]}).encode("utf-8")
        want[f"baseline/{name}.json"] = dump(baseline_for(name, spec)).encode("utf-8")
    want["pairs.json"] = dump({"pairs": build_pairs(), "files": build_files()}).encode("utf-8")
    return want


def main(argv):
    want = desired()
    if "--write" in argv:
        for d in (CORPUS, BASELINE):
            shutil.rmtree(d, ignore_errors=True)
        for rel, data in want.items():
            p = os.path.join(HERE, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        print(f"wrote {len(want)} files, {len(CASES)} cases")
        return 0
    bad = []
    for rel, data in want.items():
        p = os.path.join(HERE, *rel.split("/"))
        have = open(p, "rb").read() if os.path.exists(p) else None
        if have is None or have.replace(b"\r\n", b"\n") != data.replace(b"\r\n", b"\n"):
            bad.append(rel)
    on_disk = set()
    for base in (CORPUS, BASELINE):
        for dirpath, _dirs, names in os.walk(base):
            for n in names:
                on_disk.add(os.path.relpath(os.path.join(dirpath, n), HERE).replace(os.sep, "/"))
    bad += sorted(on_disk - set(want))
    if bad:
        print("out of date:", *bad[:20], sep="\n  ")
        return 1
    print(f"{len(CASES)} cases are current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
