<img src="https://kaeris.dev/icon-512.png" alt="KAERIS" width="72" align="left" style="margin-right:16px" />

# KAERIS i18n — CLI

AI localization from your terminal. Translate your app's strings files into **46 languages** —
locally or in CI/CD. Format-aware, placeholder-safe, and **incremental** (only new keys).

- **Zero dependencies** — pure Python stdlib, installs in a second
- **13 formats** — JSON, YAML, `.strings`, Xcode String Catalog `.xcstrings`, Markdown/MDX, `.po`, ARB, Android XML, CSV (Godot/Unity), XLIFF 1.2, Java `.properties`, .NET `.resx`, Mozilla Fluent `.ftl`
- **Incremental** — `--only-new` translates just the keys you added, merges the rest
- **Translation QA** — flags dropped placeholders & UI-overflow risk; `--verify` back-translates so you can check the meaning
- **`kaeris check`** — an i18n firewall: fails CI if a locale is untranslated or placeholder-broken, no API call
- **`kaeris quota`** — how much of your monthly volume is left, before a big run rather than after
- **CI-ready** — GitHub Action included; open a PR with fresh translations on every push

## Install

```bash
pip install kaeris
# or, without installing:
pipx run kaeris --help
```

## Config file (`kaeris.json`) — set it up once, forget it

```bash
kaeris init                    # generic config
kaeris init --preset next-intl # or: i18next, react-i18next, react-intl, vue-i18n, flutter-arb
```

Writes a `kaeris.json` in the current directory with `source`, `langs`, `keep`, `context`, `tone`,
`icu`, `only_new`, `out` and `format`. Once it exists, just run:

```bash
kaeris translate
```

with no arguments — the CLI reads `kaeris.json` from the current directory (or `--config PATH`,
passed before the subcommand) for anything you don't pass on the command line. **Precedence: CLI
flag > kaeris.json > built-in default** — so you can still override any one setting for a single
run, e.g. `kaeris translate --langs es` or `kaeris translate --no-icu`. `kaeris init` refuses to
overwrite an existing `kaeris.json` unless you pass `--force`.

## Quick start

```bash
# Translate a whole file into Spanish, French and Japanese
kaeris translate locales/en.json --langs es,fr,ja --out locales

# Only translate keys that are missing from the existing target files
kaeris translate locales/en.json --langs es,fr,ja --out locales --only-new

# Keep brand/product names verbatim in every language (glossary / do-not-translate)
kaeris translate locales/en.json --langs es,de --keep "KAERIS,GitHub,OpenRouter"
# (--glossary is the same flag, if that name is easier to remember)

# Tell the model what the app is, so ambiguous strings get the right sense
# ("Bank" the money kind, not the river kind; "Play" the game, not the theatre)
kaeris translate locales/en.json --langs de,ja --context "a mobile bank for teenagers"

# Translation QA — verify meaning (back-translate) and write verify.json to review
kaeris translate locales/en.json --langs de,ja --verify

# List all supported languages
kaeris languages
```

Output files are written next to the source (or into `--out`), named by language:
`es.json`, `fr.json`, `ja.json` (or `values-es/strings.xml` for Android, etc.).

**Markdown and MDX** are translated as documents, not as key/value files: `guide.md` becomes
`de.md`, `ja.md` and so on. Prose is translated; everything that is not prose is not. Fenced and
indented code blocks, URLs, inline code, HTML and JSX tags, MDX `import`/`export` lines, heading
anchors (`{#install}`) and front-matter configuration (`slug`, `date`, `tags`) come back byte
for byte — only front-matter fields a reader actually sees (`title`, `description`, `summary`,
…) are translated. Link *text* is translated, link *targets* are not.

One limitation, said out loud: text inside a multi-line JSX component is preserved rather than
translated. Guessing at the boundaries of arbitrary JSX with regexes is how you ship a broken
page.

**Xcode String Catalogs are the exception**, because the format is: one `.xcstrings` file holds
every language, so there is one file to write, not one per language. `kaeris translate
Localizable.xcstrings --langs de,fr,ja` updates that catalog in place — your comments, your
extraction states, languages already in the file and anything marked `shouldTranslate: false`
are carried through untouched. Plurals are expanded to the categories each language actually
needs: an English `one`/`other` becomes `one`/`few`/`many`/`other` in Russian and all six forms
in Arabic, with `%lld` left exactly where it was.

## Authentication & tiers

| Tier | How | Chars per file | Languages per run |
|------|-----|----------------|-------------------|
| **Free** (anonymous) | no key | 10,000 | 8 |
| **Pro / Scale** | `--key kaerisp_…` or `KAERIS_API_KEY` | 200k / 500k | all 46 |
| **Lifetime (BYOK)** | `--key` **and** `--openrouter-key sk-or-…` | unlimited (you pay OpenRouter for tokens) | all 46 |

All 46 languages are available to everyone — the free tier translates up to **8 of them per
run**, so `--langs` with a longer list is refused before anything is charged or written.

```bash
export KAERIS_API_KEY=kaerisp_xxxxxxxx
export KAERIS_OPENROUTER_KEY=sk-or-v1-xxxx   # Lifetime/BYOK only
kaeris translate en.json --langs de,uk
```

Get a key at <https://kaeris.dev/pricing.html>. A free OpenRouter key: <https://openrouter.ai/keys>.

## `kaeris quota` — how much of the month is left

Paid plans include a monthly character volume (Pro 30M, Scale 75M, Team/API 150M, counted as
characters × languages). Ask before a large run, rather than finding out from a refusal in the
middle of one:

```bash
kaeris quota
# ✓ Monthly volume: 1.2M of 30M used · 28.8M left · resets 2026-09-01
```

Every `kaeris translate` prints the same line when it finishes, and raises it to a warning once
**80%** of the month is spent — the point at which we also email the address the plan was bought
with, and again at **95%**. Lifetime (BYOK) has no monthly volume: you pay OpenRouter for tokens
directly.

## `kaeris check` — the i18n firewall

A **local, static, no-API** check: compares your source file against each target locale and
fails (non-zero exit) if anything's missing or broken — the kind of check no other i18n tool
gates a merge on.

```bash
kaeris check                              # reads source/langs/out from kaeris.json
kaeris check --source en.json --langs de,fr,ja --out locales
kaeris check --strict                     # also fail on extra/stale keys in a target
kaeris check --json                       # machine-readable output for CI/agents
kaeris check --ci                         # same exit-code contract; a stable, named entry point for pipelines
```

It runs the same deterministic quality detectors that power the web app — offline, with no
account and no network. **RED** problems fail the build; **YELLOW** are advisory (pass
`--strict` to fail on those too).

**RED — breaks the build (non-zero exit):**

- **Missing keys** — a key exists in the source but not in a target (untranslated).
- **Placeholder mismatch** — a target string's placeholders (`{name}`, `%s`, `%d`, `{{x}}`,
  `${x}`, ICU `{count, plural, ...}`) don't match the source's — dropped, renamed, or
  hallucinated placeholders crash or silently drop data at runtime.
- **Number drift** — a number in the source changed or vanished in the translation
  (`Delete 5 files` → `Delete 50 files`).
- **Broken entities / encoding** — double-encoded (`&amp;amp;`) or malformed `\u` escapes.
- **Lost inline tags** — an HTML/markup tag (`<b>`, `</b>`, …) present in the source is gone.
- **ICU / CLDR plural completeness** — the target language is missing a plural category its
  CLDR rules require.
- **Glossary term dropped** (opt-in) — a term listed under `keep` in kaeris.json (or passed as
  `kaeris check --glossary "KAERIS,GitHub"`) isn't carried into the translation.

**YELLOW — advisory warnings (`--strict` to fail):**

- **Untranslated leftover** — the target still reads as the source language.
- **Register / casing drift**, **cross-key inconsistency**, and **UI-overflow risk** (a
  translation much longer than its source, relative to that language's typical expansion).
- **Extra/stale keys** — present in a target but not the source.

Exit codes: `0` clean, `1` a RED problem was found (or a YELLOW under `--strict`), `2` bad
usage (source not found, no languages given, etc.) — drop it straight into CI:

```yaml
- run: kaeris check --source locales/en.json --langs es,fr,de,ja --out locales
```

JSON, ARB (its `@`-metadata is ignored) and **Xcode String Catalogs**; more formats land next
release.

A `.xcstrings` is checked differently because the format is different: the source language and
every translation live in one file, so there is nothing to open per language — both sides come
out of the same parse.

```bash
kaeris check --source Localizable.xcstrings --langs de,ru,ar
```

Worth running even if you never translate with us: Xcode does not fail a build for a missing
translation, and it says nothing at all when a Russian plural carries only the two forms English
had — which reads wrong to every user whose count ends in 2, 3 or 4. Entries marked
`shouldTranslate: false` are not demanded, and a key with no localization at all counts as its
own source string, the way Xcode treats it.

### Findings in your pull request

```bash
kaeris check --source locales/en.json --langs de,fr --out locales --sarif kaeris.sarif
```

Writes a SARIF 2.1.0 report (kaeris ≥ 0.2.12). Upload it with
`github/codeql-action/upload-sarif` and every finding becomes an annotation on the exact line
of the locale file, inside the diff — the GitHub Action takes it as `sarif-file`. Written even
when the run is clean, so the upload step never fails on a missing file. Inline annotations are
free on public repositories; private ones need GitHub Advanced Security.

## CI/CD (GitHub Actions) — translations as PRs, plus an i18n firewall

Add two workflows and translations arrive as a normal pull request — reviewed and merged like
any other code change, no separate translation tool to context-switch into — while a second
workflow blocks merges that leave a locale incomplete or placeholder-broken.

The [`kaeris-translate`](.github/actions/kaeris-translate/action.yml) composite action has
two modes:

- **`mode: translate`** (default) — runs `kaeris translate`, then a `kaeris check --json` before
  *and* after so it can report real numbers, and exposes outputs: `changed` (`"true"` only if a
  locale/lockfile actually changed — gate your PR step on this), `summary` (a markdown report:
  keys translated, a per-language matrix, placeholder-loss count, overflow-warning count when
  available), `missing` and `placeholder-issues` (totals).
- **`mode: check`** — runs `kaeris check --ci` (no API call). Non-zero exit fails the step (and
  the job) if any target locale is missing keys or has a placeholder mismatch — the i18n firewall.

**1. Auto-PR on every push** — see
[`translate.example.yml`](.github/workflows/translate.example.yml):

```yaml
- uses: RaiGanja/kaeris-cli/.github/actions/kaeris-translate@main
  id: kaeris
  with:
    mode: translate
    source: locales/en.json
    languages: es,fr,de,ja
    out: locales
    only-new: "true"

- uses: peter-evans/create-pull-request@v6
  if: steps.kaeris.outputs.changed == 'true'
  with:
    title: "i18n: new translations"
    body: ${{ steps.kaeris.outputs.summary }}
    branch: kaeris/i18n-updates
    add-paths: locales/**
```

**2. Firewall on every PR** — see
[`i18n-check.example.yml`](.github/workflows/i18n-check.example.yml):

```yaml
- uses: RaiGanja/kaeris-cli/.github/actions/kaeris-translate@main
  with:
    mode: check
    source: locales/en.json
    languages: es,fr,de,ja
    out: locales
```

Add that job to branch protection as a required status check and a PR simply cannot merge with
an untranslated or placeholder-broken locale.

## How incremental mode works

`--only-new` (JSON) parses your source and each existing translation, then translates a key if
it's **missing** from the target **or its source text changed** since the last run — and merges
the results back, preserving your existing translations and any non-string values (numbers,
booleans). No more re-translating (and re-paying for) the whole file every time you add one
string, and no more silently-stale translations when you edit an existing English string.

Change detection is powered by `kaeris.lock` — a small JSON file written next to your source
file after every incremental run. It records a SHA-256 of each source string **plus the
settings that produced it**: your tone, your glossary, your app context, and the model. Change any of them and
the whole locale is re-translated, so it never ends up a mix of two tones or two models. Every
tier runs the same model (GPT-4o-mini), so a plan change alone never forces a re-translation —
the lock records the model anyway, so the day we change it, you find out from a full re-run
rather than from a locale quietly built by two models. It's what lets
`--only-new` notice an edited key even though it's still present in the target; without it, a
plain "is this key missing?" check would skip the key and leave the old (now wrong) translation
in place. Commit `kaeris.lock` alongside your source file so the check works across machines/CI.
Override its location with `--lock PATH` or `"lock"` in `kaeris.json` (default: `kaeris.lock`
next to the source).

## All flags

Anything not passed falls back to `kaeris.json`, then to the built-in default.

**`kaeris translate [FILE]`**

| Flag | What it does |
|------|--------------|
| `--langs es,fr,ja` | target languages (`kaeris languages` lists all 46) |
| `--out DIR` | where to write; default is next to the source |
| `--only-new` / `--no-only-new` | translate only new/changed keys (JSON, ARB) — or force a full run |
| `--lock PATH` | where `kaeris.lock` lives (default: next to the source) |
| `--assume-current` | first incremental run on a project already translated: trust the existing target strings instead of re-translating everything |
| `--keep "A,B"` / `--glossary "A,B"` | terms to carry over verbatim (two names for one flag) |
| `--context "…"` | one line about the app, so ambiguous words get the right sense |
| `--tone neutral\|formal\|casual` | tone of voice |
| `--icu` / `--no-icu` | hint that strings use ICU MessageFormat, so plurals/select survive |
| `--source-lang en` | base language code; also detects `locales/<lang>/<namespace>.json` layouts |
| `--verify` | back-translate and write `verify.json` so you can check the meaning |
| `--back-lang en` | language to back-translate into with `--verify` |
| `--quiet` | no progress output |

**`kaeris check`**

| Flag | What it does |
|------|--------------|
| `--source FILE`, `--langs`, `--out DIR` | what to compare (or leave them to `kaeris.json`) |
| `--pattern "{lang}.json"` | target filename pattern — set it if your locales are named `de/common.json`, `strings.de.json`, … |
| `--strict` | also fail on extra/stale keys and YELLOW warnings |
| `--json` | machine-readable result |
| `--ci` | same exit-code contract, a stable entry point for pipelines |
| `--glossary "A,B"` | fail a target that dropped one of these terms |
| `--sarif FILE` | write a SARIF 2.1.0 report (needs kaeris ≥ 0.2.12) |

**`kaeris init`** — `--preset NAME`, `--force` (overwrite an existing `kaeris.json`).
**`kaeris quota`** — no flags; reads the key from `--key` or `KAERIS_API_KEY` (needs kaeris ≥ 0.2.15).
**Global** — `--key`, `--openrouter-key`, `--config PATH` (before the subcommand), `--version`.

## Environment variables

- `KAERIS_API_KEY` — API key
- `KAERIS_OPENROUTER_KEY` — OpenRouter key (BYOK)
- `KAERIS_API_URL` — override the API base URL
- `NO_COLOR` — disable coloured output

## License

MIT
