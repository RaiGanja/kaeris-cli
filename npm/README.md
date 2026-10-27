<img src="https://kaeris.dev/icon-512.png" alt="KAERIS" width="72" align="left" style="margin-right:16px" />

# kaeris

[![npm](https://img.shields.io/npm/v/kaeris)](https://www.npmjs.com/package/kaeris)
[![license](https://img.shields.io/npm/l/kaeris)](https://github.com/kaeris-io/kaeris-cli/blob/main/LICENSE)
[![node](https://img.shields.io/node/v/kaeris)](https://www.npmjs.com/package/kaeris)
[![install size](https://img.shields.io/badge/dependencies-0-brightgreen)](https://www.npmjs.com/package/kaeris?activeTab=dependencies)

An i18n check for CI. It compares your source locale file with its translations and exits non-zero
when a translation is missing, a key is left over, or a placeholder was lost or renamed:
`Hello {name}` that became `Bonjour`, `%s` that became `%d`, an ICU plural without its `other` branch.

It runs offline, reads 13 formats, and has no dependencies. Node 18 or newer.

```bash
npx kaeris check --source locales/en.json --langs de,fr,ja
```

```
✓ de: complete & placeholder-safe
✗ fr: 1 missing key(s): checkout.title
✗ ja: 1 placeholder mismatch(es):
    greeting: missing ['{name}'], added -
✗ 1 missing key, 1 placeholder mismatch
```

Exit code: `0` clean, `1` a problem was found, `2` bad usage or an unreadable file.

## Formats

JSON (nested and arrays), ARB, YAML, `.strings`, Xcode String Catalog `.xcstrings`, `.po`,
Android XML, CSV, XLIFF 1.2, `.properties`, `.resx`, Mozilla Fluent `.ftl`, Markdown and MDX.

Target files are found where the usual tools write them: `locales/en.json` → `locales/de.json`,
`locales/en/common.json` → `locales/de/common.json`, `app_en.arb` → `app_de.arb`,
`res/values/strings.xml` → `res/values-de/strings.xml`, `en.lproj/…` → `de.lproj/…`.
A String Catalog or a CSV holds every language in one file. Use `--pattern 'messages.{lang}.po'`
or `--out DIR` for any other layout.

## What it reports

Fails the build:

- keys missing from a translation, and a locale file that does not exist
- placeholders lost, invented or changed: `{name}`, `{{name}}`, `%s`, `%1$d`, `%@`, `${x}`,
  `%{x}`, `{0}`, ICU arguments, `$t(key)`, `@:key`
- ICU plural and select blocks that were dropped, lack `other`, or lack a form the language needs
  (Russian `few`/`many`, Arabic `zero`/`two`, …)
- numbers and currencies that changed, double-encoded entities, lost inline tags
- terms from `--glossary` that were dropped

Warns (fails only with `--strict`): extra keys, empty translations, text left in the source
language, a lost line break or `*emphasis*`, mixed formal and informal address, a label that grew
past what a button holds, the same source text translated two ways.

## Options

| flag | |
| --- | --- |
| `--source PATH` | source file; a glob that matches one file works too |
| `--langs, -l a,b` | target languages; `pt_br` and `PT-br` are read as `pt-BR` |
| `--out, -o DIR` | where the translations are |
| `--pattern P` | translation file name with `{lang}` in it |
| `--glossary, --keep a,b` | terms that must survive verbatim |
| `--strict` | extra keys and warnings fail the build too |
| `--json` | the report as JSON on stdout |
| `--sarif PATH` | also write SARIF 2.1.0 for `github/codeql-action/upload-sarif` |
| `--config PATH` | config file (before the command: `kaeris --config x.json check`) |

Flags can be left out when `kaeris.json` in the working directory has `source`, `langs`, `out`,
`source_lang` and `keep`:

```json
{ "source": "locales/en.json", "langs": ["de", "fr", "ja"], "keep": ["KAERIS"] }
```

## GitHub Actions

```yaml
- uses: actions/setup-node@v4
  with: { node-version: 20 }
- run: npx kaeris check --source locales/en.json --langs de,fr,ja --sarif kaeris.sarif
- uses: github/codeql-action/upload-sarif@v3
  if: always()
  with: { sarif_file: kaeris.sarif }
```

## Relation to the Python package

This is a port of `kaeris check` from the [Python package](https://github.com/kaeris-io/kaeris-cli).
The same files give the same report and exit code; the Python repository holds the corpus that
proves it. `translate`, `fix`, `init`, `languages` and `quota` talk to the service and stay in the
Python package: `pipx install kaeris`.

MIT.
