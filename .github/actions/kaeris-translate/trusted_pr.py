#!/usr/bin/env python3
"""The "PR you can trust" half of the KAERIS i18n Action: a quality report built from what the
CLI measured, and a pull request that is created once and updated afterwards.

Two subcommands, both called from action.yml:

  trusted_pr.py report   --langs es,fr --before B.json --pre P.json --after A.json \
                         [--verify-json verify.json] [--apply-fixes true] \
                         --out-file report.md [--github-output "$GITHUB_OUTPUT"]
  trusted_pr.py open-pr  --source locales/en.json --out locales --branch kaeris/i18n \
                         --base main --title "..." --body-file report.md [--github-output ...]

Every number in the report is read from `kaeris check --json` (run before the automatic fixes
and after them) and from verify.json. Nothing is estimated; what the CLI did not measure is
said to be unmeasured.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

MARKER = "<!-- kaeris-trusted-pr -->"
BODY_LIMIT = 60000          # GitHub rejects a PR body over 65536 characters
LIST_LIMIT = 25

PLACEHOLDERS, ICU, TAGS, NUMBERS, MISSING, OVERFLOW, OTHER = (
    "placeholders", "icu", "tags", "numbers", "missing", "overflow", "other")
COLUMNS = [(PLACEHOLDERS, "Placeholders"), (ICU, "ICU plurals"), (TAGS, "HTML tags"),
           (NUMBERS, "Numbers, currency"), (MISSING, "Missing keys"), (OVERFLOW, "Overflow"),
           (OTHER, "Other")]

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_OVERFLOW_KEY = re.compile(r"^(.*?): translation \+")
_APPLIED = re.compile(r"^\s*\[([^\]]+)\] (.+?): (suggested fix|line breaks / emphasis) → ")
_SKIPPED = re.compile(r"^\s*\[([^\]]+)\] (.+?): (.+)$")


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def classify(msg):
    m = (msg or "").lower()
    if "may overflow ui" in m:
        return OVERFLOW
    if m.startswith(("lost placeholder", "invented placeholder", "placeholder ")):
        return PLACEHOLDERS
    if m.startswith("icu "):
        return ICU
    if m.startswith("lost inline tag"):
        return TAGS
    if m.startswith(("number ", "currency ")):
        return NUMBERS
    return OTHER


def findings_of(check):
    """{(lang, key, category): (severity, [messages])} from one `kaeris check --json` result.
    A file-level note with no key (register, consistency) is kept under key ""."""
    out = {}

    def add(lang, key, category, severity, msg):
        sev, msgs = out.get((lang, key, category), (severity, []))
        if msg not in msgs:
            msgs.append(msg)
        out[(lang, key, category)] = ("error" if "error" in (sev, severity) else "warn", msgs)

    if not check:
        return out
    for lang, keys in (check.get("missing") or {}).items():
        for key in keys:
            add(lang, key, MISSING, "error", "key is missing")
    for issue in check.get("placeholder_issues") or []:
        bits = []
        if issue.get("missing"):
            bits.append("lost " + " ".join(issue["missing"]))
        if issue.get("added"):
            bits.append("invented " + " ".join(issue["added"]))
        add(issue["lang"], issue["key"], PLACEHOLDERS, "error", "; ".join(bits) or "placeholder mismatch")
    for group, severity in (("faults", "error"), ("warnings", "warn")):
        for f in check.get(group) or []:
            msg = f.get("msg", "")
            category = classify(msg)
            key = f.get("key", "")
            if not key and category == OVERFLOW:
                m = _OVERFLOW_KEY.match(msg)
                key = m.group(1) if m else ""
            add(f["lang"], key, category, severity, msg)
    return out


def parse_fix_log(text):
    """Fallback for a CLI older than 0.2.35, whose verify.json has no `_applied`/`_skipped`: read a saved
    `kaeris fix` log. The action no longer captures one; without it the report counts fixes from the
    before/after `check` diff alone. ({(lang, key): how}, {(lang, key): why not})."""
    applied, skipped = {}, {}
    in_skipped = False
    for raw in (text or "").splitlines():
        line = _ANSI.sub("", raw).rstrip()
        if re.search(r"\d+ line\(s\) not fixed", line):
            in_skipped = True
            continue
        m = _APPLIED.match(line)
        if m:
            applied[(m.group(1), m.group(2))] = m.group(3)
            continue
        if in_skipped:
            m = _SKIPPED.match(line)
            if m:
                skipped[(m.group(1), m.group(2))] = m.group(3)
            elif line.strip() and not line.startswith(" "):
                in_skipped = False
    return applied, skipped


def _sections(verify):
    if not isinstance(verify, dict):
        return []
    if "_review" in verify or "_files" in verify:
        return [verify]
    return [v for v in verify.values() if isinstance(v, dict) and ("_review" in v or "_files" in v)]


def fix_records(verify):
    """({(lang, key): what was done}, {(lang, key): why not}) from the `_applied`/`_skipped` lists
    `kaeris fix` (0.2.35+) writes into verify.json; None when no section carries them."""
    sections = _sections(verify)
    if not any("_applied" in s or "_skipped" in s for s in sections):
        return None
    applied, skipped = {}, {}
    for sec in sections:
        for rec in sec.get("_applied") or []:
            applied[(rec["lang"], rec["key"])] = rec.get("why") or rec.get("how") or "fixed"
        for rec in sec.get("_skipped") or []:
            skipped[(rec["lang"], rec["key"])] = rec.get("why") or ""
    return applied, skipped


def review_entries(verify):
    """{(lang, key): entry} for every line verify.json asks a person to read; and the checked-
    lines summary. Handles one source file's verify.json and the {source: results} form."""
    entries, summary = {}, {"lines": 0, "to_review": 0, "error": 0, "review": 0, "unchecked": 0}
    if not isinstance(verify, dict):
        return entries, None
    by_lang, seen = {}, False
    for sec in _sections(verify):
        for lang, lines in (sec.get("_review") or {}).items():
            for key, entry in lines.items():
                entries[(lang, key)] = entry
        s = sec.get("_summary")
        if s:
            seen = True
            for name in summary:
                summary[name] += int(s.get(name, 0))
            for lang, counts in (s.get("by_lang") or {}).items():
                acc = by_lang.setdefault(lang, {name: 0 for name in summary})
                for name in acc:
                    acc[name] += int(counts.get(name, 0))
    if not seen:
        return entries, None
    if by_lang:
        summary["by_lang"] = by_lang
    return entries, summary


def _clip(text, n=160):
    return str(text).replace("|", "\\|").replace("\n", " ").replace("`", "'")[:n]


def _cell(remaining, fixed):
    if fixed:
        return f"{remaining} ({fixed} fixed)"
    return str(remaining)


def build_report(langs, before, pre, after, verify=None, apply_fixes=True):
    """(markdown, metrics). `pre` is the check before the automatic fixes, `after` the one after;
    pass the same dict twice when no fixes ran."""
    post = findings_of(after)
    prior = findings_of(pre)
    entries, vsum = review_entries(verify)
    applied, skipped = fix_records(verify) or ({}, {})

    resolved = {k: v for k, v in prior.items() if k not in post}
    fixed_lines = {(lang, key) for (lang, key, _c) in resolved if key} | set(applied)

    human = {}
    for (lang, key, category), (severity, msgs) in post.items():
        if severity == "error" and key:
            human.setdefault((lang, key), []).extend(msgs)
    for (lang, key), entry in entries.items():
        if (lang, key) in fixed_lines:
            continue
        if entry.get("status") == "unchecked":
            continue
        why_not = skipped.get((lang, key), "")
        if why_not.startswith("no fix suggested"):
            why_not = "no automatic fix"
        reason = (why_not or entry.get("meaning")
                  or "; ".join(entry.get("warnings") or []) or entry.get("status", "to read"))
        human.setdefault((lang, key), []).append(reason)
    unchecked = [(lang, key) for (lang, key), e in entries.items()
                 if e.get("status") == "unchecked" and (lang, key) not in fixed_lines]

    missing_before = ((before or {}).get("missing")) if before else None
    blocking = sum(1 for (_l, _k, _c), (sev, _m) in post.items() if sev == "error")

    rows, totals = [], {c: 0 for c, _ in COLUMNS}
    total_translated, have_translated = 0, missing_before is not None
    for lang in langs:
        cells = []
        for category, _title in COLUMNS:
            now = len({k for (l, k, c), _ in post.items() if l == lang and c == category})
            fixed = len({k for (l, k, c) in resolved if l == lang and c == category})
            totals[category] += now
            cells.append(_cell(now, fixed))
        translated = "-"
        if have_translated:
            n = max(len((missing_before or {}).get(lang, [])) - len((after.get("missing") or {}).get(lang, [])), 0)
            total_translated += n
            translated = str(n)
        n_fixed = len({k for (l, k) in fixed_lines if l == lang})
        n_human = len({k for (l, k) in human if l == lang})
        rows.append((lang, translated, cells, n_fixed, n_human))

    n_fixed_total = len(fixed_lines)
    n_human_total = len(human)

    md = [MARKER, "## KAERIS i18n: translation report", ""]
    if have_translated:
        md.append(f"Translated **{total_translated}** key(s) into **{len(langs)}** language(s).")
    else:
        md.append(f"Ran over **{len(langs)}** language(s); no baseline, so no translated-key count.")
    if vsum is not None:
        md.append(f"Read-back by an independent model: **{vsum['lines']}** line(s) checked, "
                  f"**{vsum['to_review']}** flagged ({vsum['error']} likely wrong, {vsum['review']} to read)"
                  + (f", **{vsum['unchecked']}** could not be checked." if vsum["unchecked"] else "."))
    else:
        md.append("Read-back meaning check: not run (no verify.json).")
    if apply_fixes:
        md.append(f"Fixed automatically: **{n_fixed_total}** line(s), each re-checked by the same detectors before it was written.")
    else:
        md.append("Automatic fixes: off (`apply-fixes: false`).")
    md.append(f"Left for a person: **{n_human_total}** line(s). Blocking problems after fixes: **{blocking}**.")
    md.append("")
    head = ["Language", "Translated"] + [t for _c, t in COLUMNS] + ["Fixed", "For a person"]
    md.append("| " + " | ".join(head) + " |")
    md.append("|" + "---|" * len(head))
    for lang, translated, cells, n_fixed, n_human in rows:
        md.append("| " + " | ".join([f"`{lang}`", translated, *cells, str(n_fixed), str(n_human)]) + " |")
    md.append("")
    by_lang = (vsum or {}).get("by_lang")
    if by_lang:
        md.append("<details><summary>Read-back per language</summary>\n")
        md.append("| Language | Read | Flagged |")
        md.append("|---|---|---|")
        for lang in langs:
            c = by_lang.get(lang)
            if c:
                md.append(f"| `{lang}` | {c['lines']} | {c['to_review']} |")
        md.append("\n</details>\n")
    md.append("Cells show what is left after the automatic fixes; `(n fixed)` is what the same check "
              "found before them.")
    md.append("")

    if fixed_lines:
        md.append(f"<details><summary>Fixed automatically ({n_fixed_total})</summary>\n")
        md.append("| Language | Key | What was done |")
        md.append("|---|---|---|")
        for lang, key in sorted(fixed_lines)[:LIST_LIMIT]:
            what = applied.get((lang, key))
            if not what:
                cats = sorted({c for (l, k, c) in resolved if l == lang and k == key})
                what = "resolved: " + ", ".join(cats)
            md.append(f"| `{lang}` | `{_clip(key, 80)}` | {_clip(what)} |")
        if n_fixed_total > LIST_LIMIT:
            md.append(f"\n{n_fixed_total - LIST_LIMIT} more not listed.")
        md.append("\n</details>\n")

    if human:
        md.append(f"<details open><summary>Needs a person ({n_human_total})</summary>\n")
        md.append("| Language | Key | Why |")
        md.append("|---|---|---|")
        for (lang, key), why in sorted(human.items())[:LIST_LIMIT]:
            md.append(f"| `{lang}` | `{_clip(key, 80)}` | {_clip('; '.join(dict.fromkeys(why)))} |")
        if n_human_total > LIST_LIMIT:
            md.append(f"\n{n_human_total - LIST_LIMIT} more not listed; run `kaeris check --source ... --langs ...` locally for all of them.")
        md.append("\n</details>\n")
    else:
        md.append("Nothing is left for a person to fix. Lines are still worth a skim: a check cannot judge tone.\n")

    if unchecked:
        md.append(f"{len(unchecked)} line(s) could not be read back for meaning (the checker did not answer); read them yourself.\n")

    md.append("Checks run on every translated string: placeholders (names, types, repeats), numbers, "
              "currency, HTML entities, inline tags, ICU plural/select structure and the plural forms "
              "each language requires, line breaks and emphasis, glossary terms, untranslated text; "
              "per file: missing keys, UI overflow, consistent register and terminology.")
    md.append("")
    md.append("_Numbers come from `kaeris check --json` before and after the fixes and from verify.json; nothing is estimated._")
    text = "\n".join(md) + "\n"
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT].rsplit("\n", 1)[0] + "\n\n_Report shortened to fit GitHub's limit._\n"
    metrics = {"fixes-applied": n_fixed_total, "needs-review": n_human_total, "blocking": blocking,
               "missing": totals[MISSING], "placeholder-issues": totals[PLACEHOLDERS]}
    return text, metrics


def write_outputs(path, **values):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for name, value in values.items():
            value = "" if value is None else value
            if "\n" in str(value):
                import secrets
                delim = "KAERIS_" + secrets.token_hex(8)
                f.write(f"{name}<<{delim}\n{value}\n{delim}\n")
            else:
                f.write(f"{name}={value}\n")


# ── git and the pull request ─────────────────────────────────────────────────────────────────

class GitHub:
    """The four calls this needs. `request` is the only network entry; tests replace it."""

    def __init__(self, token, api_url="https://api.github.com", opener=urllib.request.urlopen):
        self.token, self.api_url, self.opener = token, api_url.rstrip("/"), opener

    def request(self, method, path, body=None):
        req = urllib.request.Request(
            self.api_url + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "kaeris-action"})
        try:
            with self.opener(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"message": raw.decode("utf-8", "replace")[:300]}


def open_or_update_pr(api, repo, branch, base, title, body):
    """Create the PR for `branch`, or update the open one. ("created"|"updated", number, url).
    Never opens a second PR from the same branch."""
    owner = repo.split("/")[0]

    def find():
        status, found = api.request("GET", f"/repos/{repo}/pulls?state=open&head={owner}:{branch}&per_page=5")
        if status != 200:
            raise RuntimeError(f"cannot list pull requests ({status}): {(found or {}).get('message', '')}")
        return found[0] if found else None

    def update(pr):
        status, res = api.request("PATCH", f"/repos/{repo}/pulls/{pr['number']}", {"title": title, "body": body})
        if status != 200:
            raise RuntimeError(f"cannot update PR #{pr['number']} ({status}): {(res or {}).get('message', '')}")
        return "updated", res["number"], res["html_url"]

    existing = find()
    if existing:
        return update(existing)
    status, res = api.request("POST", f"/repos/{repo}/pulls",
                              {"title": title, "body": body, "head": branch, "base": base})
    if status == 201:
        return "created", res["number"], res["html_url"]
    # Lost a race with a parallel run, or the list was stale: the PR exists, so update it.
    if status == 422 and "already exists" in json.dumps(res):
        again = find()
        if again:
            return update(again)
    hint = ""
    if status in (403, 422) and "pull request" in json.dumps(res).lower():
        hint = (" Enable Settings > Actions > General > 'Allow GitHub Actions to create and approve "
                "pull requests', or pass a token with pull-requests: write.")
    raise RuntimeError(f"cannot create the pull request ({status}): {(res or {}).get('message', '')}.{hint}")


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True, encoding="utf-8")


def paths_to_commit(porcelain_z, out_dir, source_dir):
    """Changed files that belong in the PR: everything under the locale folder, and the files the
    CLI keeps beside the source (kaeris.lock, project memory). Never verify.json."""
    out_dir, source_dir = out_dir.rstrip("/\\") or ".", source_dir.rstrip("/\\") or "."
    keep = []
    for entry in porcelain_z.split("\0"):
        if len(entry) < 4:
            continue
        path = entry[3:].replace("\\", "/")
        if os.path.basename(path) == "verify.json":
            continue
        under_out = out_dir == "." or path == out_dir or path.startswith(out_dir.replace("\\", "/") + "/")
        beside_source = os.path.dirname(path) == (source_dir.replace("\\", "/") if source_dir != "." else "")
        if under_out or beside_source:
            keep.append(path)
    return keep


def commit_and_push(branch, message, out_dir, source_dir, run=git):
    """Branch from the current checkout, commit the locale changes, force-push the branch. Each
    run rebuilds the branch from the base, so a PR is always one fresh commit on top of it.
    Returns the committed paths ([] when there was nothing to commit)."""
    status = run("status", "--porcelain", "-z", "-uall").stdout
    paths = paths_to_commit(status, out_dir, source_dir)
    if not paths:
        return []
    if not run("config", "user.name", check=False).stdout.strip():
        run("config", "user.name", "github-actions[bot]")
        run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("checkout", "-B", branch)
    run("add", "--", *paths)
    if run("diff", "--cached", "--quiet", check=False).returncode == 0:
        return []
    run("commit", "-m", message)
    run("push", "--force", "origin", f"HEAD:refs/heads/{branch}")
    return paths


# ── command line ─────────────────────────────────────────────────────────────────────────────

def _read(path):
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def cmd_report(args):
    langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    after = load_json(args.after)
    if after is None:
        md, metrics = (MARKER + "\n## KAERIS i18n: translation report\n\n`kaeris check` did not produce a result, "
                       "so the translations in this PR are NOT checked. Check the source, languages and out "
                       "inputs; until it runs, read every changed line. "
                       "See the step log.\n"), \
                      {"fixes-applied": "", "needs-review": "", "blocking": "", "missing": "", "placeholder-issues": ""}
    else:
        pre = load_json(args.pre) or after
        md, metrics = build_report(langs, load_json(args.before), pre, after, load_json(args.verify_json),
                                   _truthy(args.apply_fixes))
    with open(args.out_file, "w", encoding="utf-8") as f:
        f.write(md)
    write_outputs(args.github_output, missing=metrics["missing"],
                  **{"placeholder-issues": metrics["placeholder-issues"]},
                  summary=md, **{"fixes-applied": metrics["fixes-applied"],
                                 "needs-review": metrics["needs-review"]})


def cmd_open_pr(args):
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("::error::create-pr needs a token and GITHUB_REPOSITORY; run the action inside GitHub Actions "
              "and give the job `permissions: contents: write, pull-requests: write`.", file=sys.stderr)
        return 2
    base = args.base or os.environ.get("GITHUB_BASE_REF") or os.environ.get("GITHUB_REF_NAME") or "main"
    paths = commit_and_push(args.branch, args.message, args.out, os.path.dirname(args.source) or ".")
    if not paths:
        print("Nothing to commit: no PR opened.")
        write_outputs(args.github_output, **{"pr-action": "none", "pr-url": "", "pr-number": ""})
        return 0
    api = GitHub(token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    action, number, url = open_or_update_pr(api, repo, args.branch, base, args.title, _read(args.body_file))
    print(f"Pull request {action}: {url}")
    write_outputs(args.github_output, **{"pr-action": action, "pr-url": url, "pr-number": number})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--langs", required=True)
    r.add_argument("--before")
    r.add_argument("--pre")
    r.add_argument("--after", required=True)
    r.add_argument("--verify-json")
    r.add_argument("--apply-fixes", default="true")
    r.add_argument("--out-file", required=True)
    r.add_argument("--github-output")
    r.set_defaults(fn=cmd_report)
    o = sub.add_parser("open-pr")
    o.add_argument("--source", required=True)
    o.add_argument("--out", required=True)
    o.add_argument("--branch", required=True)
    o.add_argument("--base", default="")
    o.add_argument("--title", required=True)
    o.add_argument("--message", default="i18n: update translations")
    o.add_argument("--body-file", required=True)
    o.add_argument("--github-output")
    o.set_defaults(fn=cmd_open_pr)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except RuntimeError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
