"""`kaeris translate --dry-run`: what a run would be charged, worked out before it starts.

Nothing here talks to the translation API. cli.py works out, per file and per language, which
lines a real run would send and which it would take from the lock or the project memory; this
module prices that the way the server does and sets it against the plan's limits.

The pricing rule is the server's, copied, never invented (backend/main.py, `_meter_cost_one`):

    cost of one language = characters actually sent to the model
                           + 2 x the characters of the whole job when verify is on

Verify adds the back-translation and the independent judge, and both re-check the whole job,
not only what was new, so remembered lines do not make it cheaper there. The pricing page says
the same in other words ("three times with the meaning check"). The limits themselves (file
size, languages per run, monthly volume) are read from the server by the caller and passed in:
this file holds no plan numbers, so a plan change on the server cannot leave a stale one here.
"""

# The tier ids the server keys limits by, as people read them (same names as the run receipt).
PLAN_NAMES = {"premium": "Pro", "teamapi": "Team/API", "byok": "Lifetime", "team": "Scale",
              "free": "Free key", "anonymous": "Free"}


def metered(billable, char_count, verify):
    """Characters one language costs against the monthly volume. `billable` is what is sent to
    the model (nothing that is reused), `char_count` the whole job's source text."""
    return billable + (2 * char_count if verify else 0)


def limits_for(key, config, own_model_key=False):
    """The limits this run meets, from the server's answers; a missing answer stays None.

    `key` is /api/key/info and `config` /api/config. A Pro key used together with the person's
    own OpenRouter key is served like Lifetime (no monthly volume, the Lifetime file size): the
    server decides that from the same two facts."""
    key = key or {}
    config = config or {}
    tier = key.get("tier") or ""
    out = {"tier": tier, "plan": PLAN_NAMES.get(tier, tier) or None,
           "char_limit": key.get("char_limit"), "max_langs": None, "verify_max_langs": None,
           "own_key": tier == "byok", "month_cap": key.get("month_cap") or None}
    if tier == "premium" and own_model_key:
        out["own_key"] = True
        out["month_cap"] = None
        out["char_limit"] = config.get("byok_char_limit") or out["char_limit"]
    # Only the tiers that run on the shared key are capped on languages per request.
    if tier in ("anonymous", "free"):
        out["max_langs"] = config.get("max_langs_free" if tier == "free" else "max_langs_anon")
        out["verify_max_langs"] = config.get("verify_free_max_langs")
    return out


def volume(key, lim, total):
    """The month's volume against this run: {remaining, after, cap, used, resets, note}.

    `remaining` is None when the answer is not known, and `note` says why in words. 'Unknown'
    is never rounded to zero or to 'plenty': a number the server did not give is not ours."""
    out = {"cap": None, "used": None, "remaining": None, "after": None, "resets": None,
           "note": ""}
    if key is None:
        out["note"] = "the server could not be asked, remaining unknown"
    elif lim["own_key"]:
        out["note"] = "no monthly volume on this plan, the tokens are paid on your OpenRouter key"
    elif lim["month_cap"]:
        cap = int(lim["month_cap"])
        used = int(key.get("month_used") or 0)
        left = int(key.get("month_remaining", max(0, cap - used)))
        out.update(cap=cap, used=used, remaining=left, after=left - total,
                   resets=key.get("month_resets") or None)
    elif lim["tier"] == "anonymous":
        out["note"] = ("no API key, remaining unknown (the keyless demo has a daily allowance "
                       "the server does not report)")
    else:
        out["note"] = f"plan '{lim['tier'] or 'unknown'}' reports no monthly volume, remaining unknown"
    return out


def problems(files, lim, verify, vol):
    """What the server would refuse, as [{code, file, message}]. Only what the server's own
    answers let us say: an unknown limit is not checked."""
    found = []
    plan = lim["plan"] or "this plan"
    for f in files:
        for job in f["jobs"]:
            limit = lim["char_limit"]
            if limit and job["chars"] > limit:
                found.append({"code": "file_too_large", "file": f["file"], "message": (
                    f"{f['file']}: {job['chars']:,} characters in one job, the {plan} limit is "
                    f"{limit:,} per file")})
            n = len(job["langs"])
            if lim["max_langs"] and n > lim["max_langs"]:
                found.append({"code": "too_many_languages", "file": f["file"], "message": (
                    f"{f['file']}: {n} languages in one job, {plan} takes up to "
                    f"{lim['max_langs']} per run")})
            if verify and lim["verify_max_langs"] and n > lim["verify_max_langs"]:
                found.append({"code": "verify_languages", "file": f["file"], "message": (
                    f"{f['file']}: --verify on {n} languages, {plan} verifies up to "
                    f"{lim['verify_max_langs']}")})
    if vol["after"] is not None and vol["after"] < 0:
        found.append({"code": "volume", "file": None, "message": (
            f"this run needs {vol['remaining'] - vol['after']:,} characters and "
            f"{vol['remaining']:,} are left this month: the jobs that do not fit are refused")})
    return found


def build(files, key, config, verify, own_model_key=False):
    """The whole report, the shape `--json` prints. `files` come from cli._dry_plan."""
    lim = limits_for(key, config, own_model_key)
    total = sum(f["charged"] for f in files)
    vol = volume(key, lim, total)
    return {
        "dry_run": True,
        "characters": total,
        "reused_characters": sum(f["reused_characters"] for f in files),
        "verify": bool(verify),
        "plan": {"tier": lim["tier"] or None, "name": lim["plan"],
                 "char_limit": lim["char_limit"], "max_languages": lim["max_langs"],
                 "own_openrouter_key": lim["own_key"]},
        "volume": vol,
        "files": files,
        "problems": problems(files, lim, verify, vol),
    }


def _table(rows):
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return ["  " + "  ".join(str(c).ljust(w) if i == 0 else str(c).rjust(w)
                             for i, (c, w) in enumerate(zip(r, widths))).rstrip()
            for r in rows]


def render(rep):
    """The report as lines for a person. ASCII only: a legacy Windows console (cp1251/cp1252)
    raises on anything else, and this is the one output people paste into a ticket."""
    out = ["Dry run: nothing is translated, nothing is sent to the translation API, "
           "no characters are used.", ""]
    for f in rep["files"]:
        head = f"{f['file']}  ({f['format']}, {f['mode']}; {f['keys']:,} keys, {f['source_characters']:,} characters)"
        out.append(head)
        if f.get("note"):
            out.append("  " + f["note"])
        rows = [("language", "to translate", "reused", "up to date", "charged")]
        for lang, r in f["languages"].items():
            rows.append((lang, r["keys_to_translate"], r["keys_reused"], r["keys_up_to_date"],
                         f"{r['charged']:,}"))
        out += _table(rows) + [""]
    word = "will be charged" if not rep["plan"]["own_openrouter_key"] else "will be translated"
    out.append(f"Total: {rep['characters']:,} characters {word}"
               + (f" ({rep['reused_characters']:,} more reused, not charged)"
                  if rep["reused_characters"] else "") + ".")
    if rep["verify"]:
        out.append("--verify is counted: each language also costs 2 x the job's characters "
                   "(back-translation and the independent check).")
    vol = rep["volume"]
    plan = rep["plan"]["name"]
    if vol["remaining"] is not None:
        out.append(f"{plan + ': ' if plan else ''}{vol['remaining']:,} of {vol['cap']:,} left this month; "
                   f"after this run {max(vol['after'], 0):,} left"
                   + (f", resets {vol['resets']}" if vol["resets"] else "") + ".")
    else:
        out.append(f"{plan + ': ' if plan else ''}{vol['note']}.")
    for p in rep["problems"]:
        out.append("The server would refuse: " + p["message"] + ".")
    return out
