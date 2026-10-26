# kaeris check against the npm tools that do the same job

Measured or read on 2026-10-26. "no" in the tables means the tool's README does not list it. Sizes were taken from the registry and then confirmed by installing both tools into empty folders
(files and megabytes matched). Formats and checks are from each tool's published README; timings
are from running them on the set described below.

## Size of what `npx` has to fetch and unpack

| | kaeris 0.2.35 | @shipi18n/cli 2.16.2 | @lingual/i18n-check 0.9.5 |
| --- | --- | --- | --- |
| packages installed | **1** | 37 | 88 |
| files | **22** | 886 | about 2,090 |
| unpacked | **0.2 MB** | 4.5 MB | 43.9 MB |
| largest piece | `src/detectors.js`, 32 kB | fast-xml-parser, 1.3 MB | typescript, 22.5 MB |
| runtime dependencies | **0** | 4 direct | 7 direct |

## Formats

| | kaeris | @shipi18n/cli | @lingual/i18n-check |
| --- | --- | --- | --- |
| JSON (nested, arrays) | yes | yes | yes |
| YAML | yes | no | yes |
| ARB (Flutter) | yes | yes | no |
| Xcode `.xcstrings` | yes | yes | no |
| Apple `.strings` | yes | no | no |
| Android `strings.xml` (plurals, arrays) | yes | yes | no |
| gettext `.po` (plural forms, context) | yes | yes | no |
| XLIFF | 1.2 | 1.2 and 2.0 | no |
| Java `.properties` | yes | no | no |
| .NET `.resx` | yes | no | no |
| Fluent `.ftl` | yes | no | no |
| CSV | yes | no | no |
| Markdown / MDX | yes | no | no |
| **count** | **13** | 6 | 2 |

## Checks

| | kaeris | @shipi18n/cli | @lingual/i18n-check |
| --- | --- | --- | --- |
| missing and extra keys | yes | yes | yes |
| placeholders lost, invented, renamed | yes | yes | no (a planted `{nom}` for `{name}` was not reported) |
| plural forms the target language needs (ru few/many, ar zero/two, …) | yes | collapsed plurals | no |
| numbers and currencies changed | yes | no | no |
| lost inline tags, double-encoded entities | yes | tags | no |
| glossary / kept terms | yes | no | no |
| text left in the source language | yes | yes | no |
| formal/informal address mixed, label overflow, one term translated two ways | yes | no | no |
| ICU message syntax validated by a parser | by structure | no | yes |
| unused keys / keys used in code but absent | no | no | yes |
| reports | text, JSON, SARIF | text, JSON, SARIF, JUnit | text |
| baseline of known findings, `--changed-only`, `--min-coverage` | no | yes | no |
| exit codes | 0 / 1 / 2 | 0 / 1 / 2 | 0 / 1 |

What the others have that this does not: XLIFF 2.0, JUnit output, a baseline file, a coverage
threshold, `--changed-only`, and (@lingual) a scan of the source code for unused keys.

## Time

Same locale set for every command: one source file and 20 targets of 1,000 keys each (nested JSON,
a third of the strings with placeholders or ICU plurals, three planted faults). Wall time of the
whole process, median of 10 runs after one warm-up, Windows 11, Node 24, Python 3.14.

| | median | what it reported |
| --- | --- | --- |
| @lingual/i18n-check 0.9.5 | **0.31 s** | missing keys only |
| kaeris (this package) | 0.65 s | missing keys, placeholders, numbers, plural forms, overflow, ... |
| @shipi18n/cli 2.16.2 | 1.08 s | missing keys, placeholders, plurals, coverage |
| `python -m kaeris check` (the original) | 4.2 s | same as this package |
| starting Node alone | 0.07 s | |

@lingual/i18n-check is faster because it does less: on this set it found the missing keys and not
the renamed placeholder. Against @shipi18n/cli, which does the comparable work, this package takes
60% of the time. `node docs/bench.mjs DIR 1000 10 "label::command" ...` rebuilds the set and times
any command over it.

## Where the numbers come from

- kaeris: `npm pack --dry-run` in `npm/` (22 files, 204 kB unpacked, 60 kB tarball).
- Other tools: `npm view` and `https://registry.npmjs.org/<name>/<version>` per package of the
  resolved tree. The tree for @lingual/i18n-check includes a native formatjs binary and
  typescript; platform-specific packages for other systems were left out.
- Formats and checks: the READMEs of the versions above.
