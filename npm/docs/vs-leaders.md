# kaeris check against the npm tools that do the same job

Measured or read on 2026-10-26. "no" in the tables means the tool's README does not list it. Sizes are the registry's own numbers for the version named, summed
over the dependency tree npm resolves for Windows x64 (`npm install --package-lock-only`, then
`dist.unpackedSize` and `dist.fileCount` of every package); nothing of the other tools was
installed or run. Formats and checks are from each tool's published README.

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
| placeholders lost, invented, renamed | yes | yes | not documented (ICU syntax errors only) |
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

| | median |
| --- | --- |
| `node bin/kaeris.js check` (this package) | 0.55 s |
| `python -m kaeris check` (the original this was ported from) | 4.2 s |
| `node bin/kaeris.js --version` (what starting Node costs) | 0.07 s |

The other two tools were not timed: running third-party packages was not permitted in the session
that wrote this. `node docs/bench.mjs DIR 1000 10 "label::command" …` builds the same set and
times any command over it; the table above is its output for the first three rows.

## Where the numbers come from

- kaeris: `npm pack --dry-run` in `npm/` (22 files, 204 kB unpacked, 60 kB tarball).
- Other tools: `npm view` and `https://registry.npmjs.org/<name>/<version>` per package of the
  resolved tree. The tree for @lingual/i18n-check includes a native formatjs binary and
  typescript; platform-specific packages for other systems were left out.
- Formats and checks: the READMEs of the versions above.
