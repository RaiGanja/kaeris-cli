# KAERIS i18n — CLI

AI localization from your terminal. Translate your app's strings files into **46 languages** —
locally or in CI/CD. Format-aware, placeholder-safe, and **incremental** (only new keys).

- **Zero dependencies** — pure Python stdlib, installs in a second
- **10 formats** — JSON, YAML, `.strings`, `.po`, ARB, Android XML, CSV (Godot/Unity), XLIFF 1.2, Java `.properties`, .NET `.resx`
- **Incremental** — `--only-new` translates just the keys you added, merges the rest
- **Translation QA** — flags dropped placeholders & UI-overflow risk; `--verify` back-translates so you can check the meaning
- **`kaeris check`** — an i18n firewall: fails CI if a locale is untranslated or placeholder-broken, no API call
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

Writes a `kaeris.json` in the current directory with `source`, `langs`, `keep`, `tone`, `icu`,
`only_new`, `out` and `format`. Once it exists, just run:

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

# Translation QA — verify meaning (back-translate) and write verify.json to review
kaeris translate locales/en.json --langs de,ja --verify

# List all supported languages
kaeris languages
```

Output files are written next to the source (or into `--out`), named by language:
`es.json`, `fr.json`, `ja.json` (or `values-es/strings.xml` for Android, etc.).

## Authentication & tiers

| Tier | How | Limit |
|------|-----|-------|
| **Free** (anonymous) | no key | 10,000 chars/file |
| **Pro / Scale** | `--key kaerisp_…` or `KAERIS_API_KEY` | 200k / 500k chars/file |
| **Lifetime (BYOK)** | `--key` **and** `--openrouter-key sk-or-…` | unlimited (you pay OpenRouter for tokens) |

```bash
export KAERIS_API_KEY=kaerisp_xxxxxxxx
export KAERIS_OPENROUTER_KEY=sk-or-v1-xxxx   # Lifetime/BYOK only
kaeris translate en.json --langs de,uk
```

Get a key at <https://kaeris.dev/pricing.html>. A free OpenRouter key: <https://openrouter.ai/keys>.

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

It reports two kinds of problems:

- **Missing keys** — a key exists in the source but not in a target (untranslated).
- **Placeholder mismatch** — a target string's placeholders (`{name}`, `%s`, `%d`, `{{x}}`,
  ICU `{count, plural, ...}`) don't match the source's — a translation that dropped, renamed,
  or hallucinated a placeholder will crash or silently drop data at runtime.

Extra/stale keys (present in a target but not the source) are reported as a warning; pass
`--strict` to fail on those too. Exit codes: `0` clean, `1` a problem was found, `2` bad
usage (source not found, no languages given, etc.) — drop it straight into CI:

```yaml
- run: kaeris check --source locales/en.json --langs es,fr,de,ja --out locales
```

JSON-only for now; other formats are on the roadmap.

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

Change detection is powered by `kaeris.lock` — a small JSON file (dotted key → SHA-256 of the
source string) written next to your source file after every incremental run. It's what lets
`--only-new` notice an edited key even though it's still present in the target; without it, a
plain "is this key missing?" check would skip the key and leave the old (now wrong) translation
in place. Commit `kaeris.lock` alongside your source file so the check works across machines/CI.
Override its location with `--lock PATH` or `"lock"` in `kaeris.json` (default: `kaeris.lock`
next to the source).

## Environment variables

- `KAERIS_API_KEY` — API key
- `KAERIS_OPENROUTER_KEY` — OpenRouter key (BYOK)
- `KAERIS_API_URL` — override the API base URL
- `NO_COLOR` — disable coloured output

## License

MIT

<!-- synced from local cli/ -->
