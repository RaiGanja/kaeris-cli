# CLI: speed, digits, file-name case, PATH (0.2.32 candidate)

From a customer run on 0.2.31: one JSON file (~176 strings) and three Markdown pages into 12
languages in two batches of six, with `--verify --tm --lock --context --tone --keep`. Each batch
took 23-26 minutes; fa/bn came back with native digits; `zh-Hant.json` was written next to the
project's `zh-hant.json`; `kaeris` was not found after `pip install` on Windows (Python 3.14
from the install manager).

## 1. Speed

Measured on one input (an 84-string JSON and two pages, fa/bn/ja, the same flags), local
server, same model:

| CLI | time | JSON / page / page |
|---|---|---|
| 0.2.31 — files one after another | 395 s | 259 / 90 / 44 s |
| this branch — `--jobs 4` | 340 s (−14%) | 339 / 51 / 89 s |
| this branch, server running the meaning check in parallel (prototype) | 119 s (×3.3) | 117 / 56 / 53 s |

The same number of model calls in every run, no rate-limit errors, the lines flagged for review
within the model's own run-to-run spread.

Where the time goes: with `--verify` the server's meaning check runs one call after another —
language after language, batch after batch, judge chunk after judge chunk. The longest file's
check is the critical path, so sending files side by side helps only a little; the large win is
on the server and is tracked there.

In the CLI:
- `--jobs N` (default 4, `jobs` in kaeris.json): files go to the server side by side; under
  `--only-new` the per-language jobs of a file do too. Files, QA, memory and receipts are still
  written and reported in order. A failing language under `--only-new` no longer drops the
  languages that succeeded.
- The owner's edit token is kept per job, so parallel receipts stay the owner's.
- A 429 on submit is waited out (Retry-After, at most 60 s, 3 retries).
- A receipt asked for a moment too early (409) is asked again.

## 2. Digits — `--digits native|latin`
Default `native`: nobody's output changes. `latin` rewrites every non-ASCII decimal digit
(Persian, Bengali, Devanagari, Arabic-Indic, …) in the result to 0-9, placeholders untouched.
`digits` in kaeris.json.

## 3. File-name case
An existing file or folder that differs from the language code only in case (`zh-hant.json`,
`devlog/zh-hant/`) is written to under its own name — by translate, `--only-new` and check.
`--lang-case lower` for a project that has none yet. Without either, names are as before.

## 4. PATH on Windows
README: "kaeris is not recognized" — `python -m kaeris`, how to find the Scripts folder.
CI runs `python -m kaeris` from the built wheel and on windows-latest with Python 3.14.
