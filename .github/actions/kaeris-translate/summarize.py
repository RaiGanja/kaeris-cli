#!/usr/bin/env python3
"""Build a markdown summary (PR body / step summary) for the KAERIS i18n Action,
and emit GitHub Actions outputs — all derived from real `kaeris check --json`
output (and, best-effort, the `kaeris translate` log for overflow QA). No number
in here is fabricated: if the CLI didn't measure something, we say so instead of
guessing zero.

Usage:
  summarize.py --mode translate|check --langs es,fr,de \
      --after AFTER.json [--before BEFORE.json] [--log translate.log] \
      --out-file summary.md [--github-output "$GITHUB_OUTPUT"]
"""
import argparse
import json
import os
import re
import secrets
import sys


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def parse_overflow_count(log_path):
    """Best-effort: `kaeris translate` (whole-file mode only, i.e. --only-new not set)
    prints a line like '! 3 translation(s) may overflow your UI ...' to stderr with
    Translation QA. --only-new (the Action's default) doesn't run this QA step yet,
    so return None (not measured) rather than fabricating 0."""
    if not log_path or not os.path.isfile(log_path):
        return None
    total = None
    pattern = re.compile(r"(\d+)\s+translation\(s\)\s+may overflow")
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                total = (total or 0) + int(m.group(1))
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="translate", choices=["translate", "check"])
    ap.add_argument("--langs", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--before", default=None)
    ap.add_argument("--log", default=None)
    ap.add_argument("--out-file", required=True)
    ap.add_argument("--github-output", default=None)
    args = ap.parse_args()

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    after = load_json(args.after)
    before = load_json(args.before)

    if after is None:
        # `kaeris check` itself failed to run (bad usage, exit 2) — no data to summarize.
        md = ["## KAERIS i18n\n", "`kaeris check` did not produce a result "
              "(bad usage — check source/langs/out) — see the step log above.\n"]
        write(args, "\n".join(md), missing=None, ph=None)
        return

    missing_after = after.get("missing", {})
    missing_before = (before or {}).get("missing", {}) if before else None
    ph_by_lang = {}
    for issue in after.get("placeholder_issues", []):
        ph_by_lang.setdefault(issue["lang"], []).append(issue)
    missing_files = set(after.get("missing_files", []))
    extra = after.get("extra", {})

    overflow = parse_overflow_count(args.log) if args.mode == "translate" else None

    total_missing = 0
    total_ph = 0
    total_translated = 0
    have_before = missing_before is not None

    rows = []
    for lang in langs:
        m_after = len(missing_after.get(lang, []))
        ph = len(ph_by_lang.get(lang, []))
        total_missing += m_after
        total_ph += ph

        translated_cell = "–"
        if have_before:
            m_before = len(missing_before.get(lang, []))
            translated = max(m_before - m_after, 0)
            total_translated += translated
            translated_cell = str(translated)

        if lang in missing_files:
            status = "⚠ locale file missing"
        elif m_after == 0 and ph == 0:
            status = "✓ complete"
        else:
            bits = []
            if m_after:
                bits.append(f"{m_after} missing")
            if ph:
                bits.append(f"{ph} placeholder issue{'s' if ph != 1 else ''}")
            status = "⚠ " + ", ".join(bits)

        rows.append((lang, status, m_after, ph, translated_cell))

    lines = ["## KAERIS i18n" + (" — CI check" if args.mode == "check" else " — translate")]
    lines.append("")
    if args.mode == "translate":
        if have_before:
            lines.append(f"Translated **{total_translated}** key(s) across **{len(langs)}** language(s).")
        else:
            lines.append(f"Ran across **{len(langs)}** language(s) (no baseline available for a translated-key count).")
    lines.append(f"Placeholder-loss: **{total_ph}** (should be 0).")
    if args.mode == "translate":
        if overflow is None:
            lines.append("Overflow warnings: _not measured_ (only available without `--only-new`/incremental mode).")
        else:
            lines.append(f"Overflow warnings: **{overflow}**.")
    if total_missing:
        lines.append(f"Missing keys remaining: **{total_missing}**.")
    lines.append("")
    lines.append("| Language | Status | Missing | Placeholder issues | Translated |")
    lines.append("|---|---|---|---|---|")
    for lang, status, m_after, ph, translated_cell in rows:
        lines.append(f"| {lang} | {status} | {m_after} | {ph} | {translated_cell} |")

    if any(extra.values()):
        lines.append("")
        n_extra = sum(len(v) for v in extra.values())
        lines.append(f"_{n_extra} extra/stale key(s) found in target locale(s) not in the source "
                     "(warning only unless `--strict`)._")

    lines.append("")
    lines.append("_Generated from `kaeris check --json`" +
                  ("" if overflow is None else " and the translate log") + " — no fabricated numbers._")

    write(args, "\n".join(lines) + "\n", missing=total_missing, ph=total_ph)


def write(args, markdown, missing, ph):
    with open(args.out_file, "w", encoding="utf-8") as f:
        f.write(markdown)

    if not args.github_output:
        return
    with open(args.github_output, "a", encoding="utf-8") as f:
        f.write(f"missing={missing if missing is not None else ''}\n")
        f.write(f"placeholder-issues={ph if ph is not None else ''}\n")
        delim = "KAERIS_SUMMARY_" + secrets.token_hex(8)
        f.write(f"summary<<{delim}\n")
        f.write(markdown)
        if not markdown.endswith("\n"):
            f.write("\n")
        f.write(f"{delim}\n")


if __name__ == "__main__":
    sys.exit(main())
